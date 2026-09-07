import json
from pathlib import Path

import pytest

from risk_bench.agents.base import Message, Response, Tool, ToolCall
from risk_bench.agents.models import estimate_cost_usd
from risk_bench.agents.tracing import (
    TraceWriter,
    TracingAgent,
    per_turn_usage,
    trace_path_for_journal,
    usage_by_caller,
)

A_TOOL = Tool(name="wave", description="Wave at someone.", parameters={"type": "object", "properties": {}})
A_CONVERSATION = [Message(role="system", content="be nice"), Message(role="user", content="hello")]


class ScriptedAgent:
    def __init__(self, response: Response) -> None:
        self.response = response

    def respond(self, messages: list[Message], tools: list[Tool]) -> Response:
        return self.response


class FailingAgent:
    def respond(self, messages: list[Message], tools: list[Tool]) -> Response:
        raise RuntimeError("the provider is down")


def read_trace(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_every_call_is_recorded_with_context_usage_and_payloads(tmp_path: Path):
    writer = TraceWriter(path=tmp_path / "game.trace.jsonl", append=False)
    response = Response(
        text="waving now",
        tool_calls=(ToolCall(id="call-1", name="wave", arguments={"at": "gpt-5.6-terra"}),),
        input_tokens=120,
        output_tokens=15,
        cache_write_tokens=80,
        cache_read_tokens=20,
    )
    agent = TracingAgent(
        agent=ScriptedAgent(response=response), model="claude-sonnet-5", caller="claude-sonnet-5", writer=writer
    )

    writer.set_context(turn=3, kind="decision")
    returned = agent.respond(messages=A_CONVERSATION, tools=[A_TOOL])
    writer.close()

    assert returned == response
    [record] = read_trace(path=writer.path)
    assert record["seq"] == 1
    assert (record["turn"], record["kind"]) == (3, "decision")
    assert (record["caller"], record["model"]) == ("claude-sonnet-5", "claude-sonnet-5")
    assert [message["role"] for message in record["messages"]] == ["system", "user"]
    assert record["tools"][0]["name"] == "wave"
    assert record["response"]["tool_calls"] == [{"id": "call-1", "name": "wave", "arguments": {"at": "gpt-5.6-terra"}}]
    assert (record["input_tokens"], record["output_tokens"]) == (120, 15)
    assert (record["cache_write_tokens"], record["cache_read_tokens"]) == (80, 20)
    assert record["error"] is None
    assert record["latency_ms"] >= 0


def test_a_failing_call_is_recorded_and_the_exception_passes_through(tmp_path: Path):
    writer = TraceWriter(path=tmp_path / "game.trace.jsonl", append=False)
    agent = TracingAgent(agent=FailingAgent(), model="gpt-5.6-terra", caller="gpt-5.6-terra", writer=writer)

    with pytest.raises(RuntimeError, match="the provider is down"):
        agent.respond(messages=A_CONVERSATION, tools=[])
    writer.close()

    [record] = read_trace(path=writer.path)
    assert record["error"] == "RuntimeError: the provider is down"
    assert record["response"] is None
    assert record["input_tokens"] is None


def test_appending_continues_the_sequence_numbering(tmp_path: Path):
    path = tmp_path / "game.trace.jsonl"
    first_writer = TraceWriter(path=path, append=False)
    agent = TracingAgent(agent=ScriptedAgent(response=Response(text="hi")), model="m", caller="m", writer=first_writer)
    agent.respond(messages=A_CONVERSATION, tools=[])
    agent.respond(messages=A_CONVERSATION, tools=[])
    first_writer.close()

    resumed_writer = TraceWriter(path=path, append=True)
    resumed_agent = TracingAgent(
        agent=ScriptedAgent(response=Response(text="hi")), model="m", caller="m", writer=resumed_writer
    )
    resumed_agent.respond(messages=A_CONVERSATION, tools=[])
    resumed_writer.close()

    assert [record["seq"] for record in read_trace(path=path)] == [1, 2, 3]


def test_per_turn_usage_breaks_down_by_category_and_caller_in_tokens_and_cost(tmp_path: Path):
    writer = TraceWriter(path=tmp_path / "game.trace.jsonl", append=False)

    def record(turn: int, kind: str, caller: str, model: str, input_tokens: int, output_tokens: int) -> None:
        response = Response(text="x", input_tokens=input_tokens, output_tokens=output_tokens)
        agent = TracingAgent(agent=ScriptedAgent(response=response), model=model, caller=caller, writer=writer)
        writer.set_context(turn=turn, kind=kind)
        agent.respond(messages=A_CONVERSATION, tools=[])

    # sonnet is priced $2/$10 per Mtok, haiku $1/$5; one million-token unit makes the cost arithmetic exact
    record(1, "negotiation", "sonnet", "claude-sonnet-5", 1_000_000, 0)
    record(1, "decision", "sonnet", "claude-sonnet-5", 0, 100_000)
    record(1, "decision", "haiku", "claude-haiku-4-5", 500_000, 0)
    record(2, "commentary:skill", "commentator:skill", "claude-sonnet-5", 200_000, 0)
    writer.close()

    usage = per_turn_usage(trace_path=writer.path)

    assert usage["turns"] == [1, 2]
    assert usage["categories"] == ["negotiation", "decision", "commentary"]
    assert usage["callers"] == ["sonnet", "haiku", "commentator:skill"]  # first-seen order

    # by category: turn-1 decision sums across callers (haiku 500k in, sonnet 100k out)
    decision = usage["by_category"]["decision"]
    assert decision["input_tokens"] == [500_000, 0]
    assert decision["output_tokens"] == [100_000, 0]
    assert decision["input_cost"] == [0.5, 0.0]  # haiku 500k * $1/Mtok
    assert decision["output_cost"] == [1.0, 0.0]  # sonnet 100k * $10/Mtok

    # by caller: sonnet turn-1 sums across its categories (1M negotiation in, 100k decision out)
    sonnet = usage["by_caller"]["sonnet"]
    assert sonnet["input_tokens"] == [1_000_000, 0]
    assert sonnet["output_tokens"] == [100_000, 0]
    assert sonnet["input_cost"] == [2.0, 0.0]  # 1M * $2/Mtok
    assert sonnet["output_cost"] == [1.0, 0.0]  # 100k * $10/Mtok


def test_per_turn_usage_of_a_missing_trace_is_empty(tmp_path: Path):
    assert per_turn_usage(trace_path=tmp_path / "absent.trace.jsonl") == {
        "turns": [],
        "categories": [],
        "callers": [],
        "by_category": {},
        "by_caller": {},
    }


def test_the_trace_lives_next_to_the_journal():
    assert trace_path_for_journal(journal_path=Path("data/games/x-seed0.jsonl")) == Path(
        "data/games/x-seed0.trace.jsonl"
    )


def test_cost_estimates_use_the_pricing_table_and_admit_ignorance():
    known = estimate_cost_usd(
        model="claude-sonnet-5",
        input_tokens=1_000_000,
        cache_write_tokens=0,
        cache_read_tokens=0,
        output_tokens=200_000,
    )
    assert known == pytest.approx(2.00 + 0.2 * 10.00)
    unknown = estimate_cost_usd(
        model="some-custom-model", input_tokens=10, cache_write_tokens=0, cache_read_tokens=0, output_tokens=10
    )
    assert unknown is None


def test_cost_estimates_price_the_cached_shares_at_their_own_rates():
    # opus: input $5, cache write $6.25 (1.25x), cache read $0.50 (0.1x), output $25 per Mtok.
    # 1M total input of which 200k written to cache and 700k read from it -> 100k uncached.
    cached = estimate_cost_usd(
        model="claude-opus-5",
        input_tokens=1_000_000,
        cache_write_tokens=200_000,
        cache_read_tokens=700_000,
        output_tokens=40_000,
    )
    assert cached == pytest.approx(0.1 * 5.00 + 0.2 * 6.25 + 0.7 * 0.50 + 0.04 * 25.00)
    # the same call priced cache-blind would cost 1M x $5 + 40k x $25: caching cuts it by ~60%
    assert cached is not None and cached < 5.00 + 1.00


def test_usage_by_caller_aggregates_cache_shares_into_the_cost(tmp_path: Path):
    writer = TraceWriter(path=tmp_path / "game.trace.jsonl", append=False)
    cached_response = Response(
        text="x", input_tokens=1_000_000, output_tokens=0, cache_write_tokens=0, cache_read_tokens=1_000_000
    )
    agent = TracingAgent(
        agent=ScriptedAgent(response=cached_response), model="claude-sonnet-5", caller="sonnet", writer=writer
    )
    agent.respond(messages=A_CONVERSATION, tools=[])
    writer.close()

    entry = usage_by_caller(trace_path=writer.path)["sonnet"]

    assert entry["input_tokens"] == 1_000_000 and entry["cache_read_tokens"] == 1_000_000
    assert entry["cost_usd"] == pytest.approx(0.20)  # 1M fully cache-read at $0.20/Mtok, not $2.00
