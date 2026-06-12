from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

Role = Literal["system", "user", "assistant", "tool"]


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

    Token counts are as reported by the provider (None when not reported);
    output tokens include any hidden reasoning tokens the provider bills for.
    """

    text: str | None
    tool_calls: tuple[ToolCall, ...] = field(default_factory=tuple)
    input_tokens: int | None = None
    output_tokens: int | None = None


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
