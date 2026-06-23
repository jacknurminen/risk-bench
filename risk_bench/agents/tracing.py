"""Recording every provider API call into a sidecar trace file.

The trace is purely observational: it captures exactly what was sent to and
received from each provider (full message lists, tool schemas, raw responses,
token usage, latency, and errors) so games can be audited and costed after the
fact. It lives next to the game journal as ``{game}.trace.jsonl`` but is never
read back into the game — deleting a trace loses nothing about the game itself.
"""

import json
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from risk_bench.agents.base import Agent, Message, Response, Tool
from risk_bench.agents.models import estimate_cost_usd

# call categories in the order a turn produces them, for a stable stacking order in the chart
TOKEN_CATEGORY_ORDER: Final[tuple[str, ...]] = ("negotiation", "decision", "commentary", "other")


def usage_by_caller(trace_path: Path) -> dict[str, dict[str, Any]]:
    """Aggregate a trace file into per-caller call counts, token totals, and cost.

    Args:
        trace_path: The ``.trace.jsonl`` file; a missing file yields an empty dict.

    Returns:
        Caller mapped to model, calls, errors, token totals, and estimated cost
        in USD (None when the model's pricing is unknown).
    """
    usage: dict[str, dict[str, Any]] = {}
    for record in _iter_trace_records(trace_path=trace_path):
        entry = usage.setdefault(
            record["caller"],
            {"model": record["model"], "calls": 0, "errors": 0, "input_tokens": 0, "output_tokens": 0},
        )
        entry["calls"] += 1
        entry["errors"] += 1 if record.get("error") else 0
        entry["input_tokens"] += record.get("input_tokens") or 0
        entry["output_tokens"] += record.get("output_tokens") or 0
    for entry in usage.values():
        entry["cost_usd"] = estimate_cost_usd(
            model=entry["model"], input_tokens=entry["input_tokens"], output_tokens=entry["output_tokens"]
        )
    return usage


def per_turn_usage(trace_path: Path) -> dict[str, Any]:
    """Aggregate a trace into per-turn token and cost series, broken down two ways.

    Every call is bucketed by turn and by both its category (negotiation /
    decision / commentary, folding all commentary rubrics together) and its
    caller, so a chart can stack a turn either by what the calls were for
    (``by_category``) or by which model made them (``by_caller``), in tokens or
    in estimated cost. A missing trace yields empty series.

    Args:
        trace_path: The ``.trace.jsonl`` file.

    Returns:
        ``turns`` and the ordered ``categories`` / ``callers`` group lists, plus
        ``by_category`` and ``by_caller`` mapping each group to per-turn
        ``input_tokens`` / ``output_tokens`` / ``input_cost`` / ``output_cost``
        series aligned with ``turns``.
    """
    counts: dict[int, dict[str, dict[str, dict[str, int]]]] = {}
    model_by_caller: dict[str, str] = {}
    for record in _iter_trace_records(trace_path=trace_path):
        caller = record["caller"]
        model_by_caller.setdefault(caller, record["model"])
        category = _category_for_kind(kind=record.get("kind", "unknown"))
        bucket = (
            counts.setdefault(int(record.get("turn", 0)), {})
            .setdefault(caller, {})
            .setdefault(category, {"input": 0, "output": 0})
        )
        bucket["input"] += record.get("input_tokens") or 0
        bucket["output"] += record.get("output_tokens") or 0
    turns = sorted(counts)
    categories = [
        category
        for category in TOKEN_CATEGORY_ORDER
        if any(category in by_caller for turn in turns for by_caller in counts[turn].values())
    ]
    callers = [caller for caller in model_by_caller if any(caller in counts[turn] for turn in turns)]
    return {
        "turns": turns,
        "categories": categories,
        "callers": callers,
        "by_category": {
            category: _usage_series(counts=counts, turns=turns, models=model_by_caller, category=category)
            for category in categories
        },
        "by_caller": {
            caller: _usage_series(counts=counts, turns=turns, models=model_by_caller, caller=caller)
            for caller in callers
        },
    }


def trace_path_for_journal(journal_path: Path) -> Path:
    """Derive the trace sidecar path that belongs to a game journal.

    Args:
        journal_path: The game's ``.jsonl`` journal path.

    Returns:
        The ``.trace.jsonl`` path next to the journal.
    """
    return journal_path.with_suffix(".trace.jsonl")


class TraceWriter:
    """Appends one JSON line per provider API call, flushed immediately.

    The writer also carries the game context (turn number and call kind) that
    the agents themselves cannot know; the game runner updates it before each
    round of calls so every record can be grouped by turn in the inspector.
    """

    def __init__(self, path: Path, append: bool) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.turn = 0
        self.kind = "unknown"
        self._sequence = _existing_record_count(path=path) if append and path.exists() else 0
        self._file = path.open("a" if append else "w", encoding="utf-8")

    def set_context(self, turn: int, kind: str) -> None:
        """Set the game context stamped onto subsequent records.

        Args:
            turn: The current game turn.
            kind: What the calls are for: 'decision', 'negotiation', or 'commentary'.
        """
        self.turn = turn
        self.kind = kind

    def write_call(
        self,
        caller: str,
        model: str,
        messages: list[Message],
        tools: list[Tool],
        response: Response | None,
        error: str | None,
        latency_ms: int,
    ) -> None:
        """Record one provider API call, successful or failed.

        Args:
            caller: Who made the call: a player id or 'commentator'.
            model: The concrete model id the call was made against.
            messages: The full conversation sent to the provider.
            tools: The tool schemas offered alongside the messages.
            response: The provider's reply, or None when the call raised.
            error: The exception description when the call raised, else None.
            latency_ms: Wall-clock duration of the call in milliseconds.
        """
        self._sequence += 1
        record = {
            "seq": self._sequence,
            "time": datetime.now(tz=UTC).isoformat(),
            "turn": self.turn,
            "kind": self.kind,
            "caller": caller,
            "model": model,
            "messages": [_message_to_json(message=message) for message in messages],
            "tools": [
                {"name": tool.name, "description": tool.description, "parameters": tool.parameters} for tool in tools
            ],
            "response": _response_to_json(response=response) if response is not None else None,
            "error": error,
            # usage duplicated at the top level so aggregation can skip parsing bodies
            "input_tokens": response.input_tokens if response is not None else None,
            "output_tokens": response.output_tokens if response is not None else None,
            "latency_ms": latency_ms,
        }
        self._file.write(json.dumps(record) + "\n")
        self._file.flush()

    def close(self) -> None:
        """Close the underlying file."""
        self._file.close()


class TracingAgent:
    """An agent decorator that records every call to a trace writer.

    Transparent to the caller: responses and exceptions pass through unchanged,
    so retries, corrections, and provider failures all leave a record.
    """

    def __init__(self, agent: Agent, model: str, caller: str, writer: TraceWriter) -> None:
        self._agent = agent
        self._model = model
        self._caller = caller
        self._writer = writer

    def respond(self, messages: list[Message], tools: list[Tool]) -> Response:
        """Generate the next assistant turn via the wrapped agent, recording the exchange.

        Args:
            messages: Full conversation history, oldest first.
            tools: Tools the model may call; pass an empty list for plain chat.

        Returns:
            The wrapped agent's reply, unchanged.
        """
        started_at = time.monotonic()
        try:
            response = self._agent.respond(messages=messages, tools=tools)
        except Exception as error:
            self._write(
                messages=messages,
                tools=tools,
                response=None,
                error=f"{type(error).__name__}: {error}",
                started_at=started_at,
            )
            raise
        self._write(messages=messages, tools=tools, response=response, error=None, started_at=started_at)
        return response

    def _write(
        self,
        messages: list[Message],
        tools: list[Tool],
        response: Response | None,
        error: str | None,
        started_at: float,
    ) -> None:
        self._writer.write_call(
            caller=self._caller,
            model=self._model,
            messages=messages,
            tools=tools,
            response=response,
            error=error,
            latency_ms=int((time.monotonic() - started_at) * 1000),
        )


def _usage_series(
    counts: dict[int, dict[str, dict[str, dict[str, int]]]],
    turns: list[int],
    models: dict[str, str],
    category: str | None = None,
    caller: str | None = None,
) -> dict[str, list[float]]:
    """Build per-turn token and cost series, summing over whichever axis is left free.

    Fixing ``category`` sums across callers (the by-category breakdown); fixing
    ``caller`` sums across categories (the by-caller breakdown). Cost is derived
    per bucket from that caller's model, so a mix of models aggregates correctly.
    """
    input_tokens, output_tokens, input_cost, output_cost = [], [], [], []
    for turn in turns:
        tokens_in = tokens_out = 0
        cost_in = cost_out = 0.0
        callers_at_turn = counts.get(turn, {})
        for current_caller in [caller] if caller is not None else callers_at_turn:
            categories_for_caller = callers_at_turn.get(current_caller, {})
            for current_category in [category] if category is not None else categories_for_caller:
                bucket = categories_for_caller.get(current_category)
                if bucket is None:
                    continue
                model = models[current_caller]
                tokens_in += bucket["input"]
                tokens_out += bucket["output"]
                cost_in += estimate_cost_usd(model=model, input_tokens=bucket["input"], output_tokens=0) or 0.0
                cost_out += estimate_cost_usd(model=model, input_tokens=0, output_tokens=bucket["output"]) or 0.0
        input_tokens.append(tokens_in)
        output_tokens.append(tokens_out)
        input_cost.append(round(cost_in, 4))
        output_cost.append(round(cost_out, 4))
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "input_cost": input_cost,
        "output_cost": output_cost,
    }


def _iter_trace_records(trace_path: Path) -> Iterator[dict[str, Any]]:
    if not trace_path.exists():
        return
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            return  # a crash mid-write leaves at most one partial trailing line


def _category_for_kind(kind: str) -> str:
    if kind.startswith("commentary"):
        return "commentary"
    if kind in ("negotiation", "decision"):
        return kind
    return "other"


def _existing_record_count(path: Path) -> int:
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def _message_to_json(message: Message) -> dict[str, Any]:
    serialized: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.tool_calls:
        # provider_data is opaque (and possibly non-JSON) replay state; the trace drops it
        serialized["tool_calls"] = [
            {"id": call.id, "name": call.name, "arguments": call.arguments} for call in message.tool_calls
        ]
    if message.tool_call_id is not None:
        serialized["tool_call_id"] = message.tool_call_id
    return serialized


def _response_to_json(response: Response) -> dict[str, Any]:
    return {
        "text": response.text,
        "tool_calls": [
            {"id": call.id, "name": call.name, "arguments": call.arguments} for call in response.tool_calls
        ],
    }
