import json
from pathlib import Path

import pytest

from risk_bench.agents.base import Message, Response, Tool, ToolCall
from risk_bench.agents.models import estimate_cost_usd
from risk_bench.agents.tracing import TraceWriter, TracingAgent, trace_path_for_journal

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
        tool_calls=(ToolCall(id="call-1", name="wave", arguments={"at": "gpt-5.4"}),),
        input_tokens=120,
        output_tokens=15,
    )
    agent = TracingAgent(
        agent=ScriptedAgent(response=response), model="claude-sonnet-4-6", caller="claude-sonnet-4-6", writer=writer
    )

    writer.set_context(turn=3, kind="decision")
    returned = agent.respond(messages=A_CONVERSATION, tools=[A_TOOL])
    writer.close()

    assert returned == response
    [record] = read_trace(path=writer.path)
    assert record["seq"] == 1
    assert (record["turn"], record["kind"]) == (3, "decision")
    assert (record["caller"], record["model"]) == ("claude-sonnet-4-6", "claude-sonnet-4-6")
    assert [message["role"] for message in record["messages"]] == ["system", "user"]
    assert record["tools"][0]["name"] == "wave"
    assert record["response"]["tool_calls"] == [{"id": "call-1", "name": "wave", "arguments": {"at": "gpt-5.4"}}]
    assert (record["input_tokens"], record["output_tokens"]) == (120, 15)
    assert record["error"] is None
    assert record["latency_ms"] >= 0


def test_a_failing_call_is_recorded_and_the_exception_passes_through(tmp_path: Path):
    writer = TraceWriter(path=tmp_path / "game.trace.jsonl", append=False)
    agent = TracingAgent(agent=FailingAgent(), model="gpt-5.4", caller="gpt-5.4", writer=writer)

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


def test_the_trace_lives_next_to_the_journal():
    assert trace_path_for_journal(journal_path=Path("data/games/x-seed0.jsonl")) == Path(
        "data/games/x-seed0.trace.jsonl"
    )


def test_cost_estimates_use_the_pricing_table_and_admit_ignorance():
    known = estimate_cost_usd(model="claude-sonnet-4-6", input_tokens=1_000_000, output_tokens=200_000)
    assert known == pytest.approx(3.00 + 0.2 * 15.00)
    assert estimate_cost_usd(model="some-custom-model", input_tokens=10, output_tokens=10) is None
