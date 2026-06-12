"""Recording every provider API call into a sidecar trace file.

The trace is purely observational: it captures exactly what was sent to and
received from each provider (full message lists, tool schemas, raw responses,
token usage, latency, and errors) so games can be audited and costed after the
fact. It lives next to the game journal as ``{game}.trace.jsonl`` but is never
read back into the game — deleting a trace loses nothing about the game itself.
"""

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from risk_bench.agents.base import Agent, Message, Response, Tool


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
