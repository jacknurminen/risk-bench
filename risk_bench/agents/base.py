from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal, Protocol, runtime_checkable

Role = Literal["system", "user", "assistant", "tool"]


class ReasoningEffort(StrEnum):
    """Provider-agnostic reasoning effort, mapped to each provider's native control.

    Lower effort spends fewer reasoning (and thus output) tokens at some cost to
    play quality. Each agent translates this to the knob its API exposes:
    Anthropic ``output_config.effort``, OpenAI ``reasoning_effort``, and Gemini
    ``thinking_level``.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class Tool:
    """A function the model may call, described in provider-agnostic terms.

    ``parameters`` is a JSON Schema object describing the call arguments. Each
    provider implementation converts this into its own native tool format.
    """

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    """A request from the model to invoke a tool. The caller executes it.

    ``provider_data`` is opaque provider-specific state that must be echoed
    back unchanged when the call is replayed in the conversation history
    (e.g. Gemini thought signatures). Callers never inspect it.
    """

    id: str
    name: str
    arguments: dict[str, Any]
    provider_data: object | None = None


@dataclass(frozen=True)
class Message:
    """A single turn in the conversation the caller maintains and replays.

    - ``user`` / ``system`` / ``assistant`` text turns set ``content``.
    - An assistant turn that called tools sets ``tool_calls``.
    - A ``tool`` turn carries a tool's result in ``content`` and references the
      originating call via ``tool_call_id``.
    """

    role: Role
    content: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None


@dataclass(frozen=True)
class Response:
    """The model's reply: free text, tool calls, or both.

    Token counts are normalized across providers (None when not reported):
    ``input_tokens`` is the call's TOTAL prompt tokens — including the cached share, which the
    providers report differently — of which ``cache_write_tokens`` were written to the provider's
    prompt cache (billed at a premium) and ``cache_read_tokens`` were served from it (billed at a
    discount). Output tokens include any hidden reasoning tokens the provider bills for.
    """

    text: str | None
    tool_calls: tuple[ToolCall, ...] = field(default_factory=tuple)
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_write_tokens: int | None = None
    cache_read_tokens: int | None = None


def list_argument(value: Any) -> list[Any] | None:
    """Read a tool-call argument that should be a list, accepting a JSON-encoded one.

    Models sometimes serialize a nested array as a string, and a provider hands the raw
    string back when its own accumulation of a large argument fails to parse. A stray NUL
    escape — seen in oversized arguments — is dropped before the second attempt, since a
    NUL is never part of a legitimate value here.

    Args:
        value: The argument exactly as the provider delivered it.

    Returns:
        The list, or None when the value is neither a list nor a JSON-encoded list.
    """
    if isinstance(value, list):
        return value
    if not isinstance(value, str):
        return None
    for candidate in (value, value.replace("\\u0000", "").replace("\x00", "")):
        try:
            decoded = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        return decoded if isinstance(decoded, list) else None
    return None


@runtime_checkable
class Agent(Protocol):
    """A unified, provider-agnostic chat model.

    Implementations wrap a single provider's API and know nothing about the
    environment they are used in. The caller owns the conversation: it passes
    the full message history each call and appends the resulting assistant turn
    (and any tool results) before calling again.
    """

    def respond(self, messages: list[Message], tools: list[Tool]) -> Response:
        """Generate the next assistant turn given the conversation so far.

        Args:
            messages: Full conversation history, oldest first.
            tools: Tools the model may call; pass an empty list for plain chat.

        Returns:
            The assistant's reply, with text and/or tool calls populated.
        """
        ...
