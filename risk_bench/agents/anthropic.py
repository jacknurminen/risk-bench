from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from typing import Literal, cast

import anthropic

from risk_bench.agents.base import Message, ReasoningEffort, Response, Tool, ToolCall


class AnthropicAgent:
    """Provider-agnostic chat model backed by the Anthropic Messages API."""

    def __init__(self, model: str, api_key: str, max_tokens: int, reasoning_effort: ReasoningEffort) -> None:
        self._model = model
        self._max_tokens = max_tokens
        self._reasoning_effort = reasoning_effort
        # Haiku 5.5 adds effort support; older Haiku versions reject the parameter.
        self._supports_effort = "haiku" not in model.lower() or model.lower().startswith("claude-haiku-5-5")
        self._client = anthropic.Anthropic(api_key=api_key)

    def respond(self, messages: list[Message], tools: list[Tool]) -> Response:
        """Generate the next assistant turn via the Anthropic Messages API.

        Args:
            messages: Full conversation history, oldest first.
            tools: Tools the model may call; pass an empty list for plain chat.

        Returns:
            The assistant's reply, with text and/or tool calls populated.
        """
        system_prompt, conversation = _split_system(messages=messages)
        system_blocks = _system_blocks(system_prompt=system_prompt)

        # Streamed rather than created in one shot: with a large output budget (the evaluator's
        # whole-game pass) the SDK refuses non-streaming requests that may run past 10 minutes.
        # Older SDKs omit stop_details when accumulating message_delta into the final message,
        # so preserve it from the stream as well as checking the final response.
        stop_details = None
        with self._client.messages.stream(
            model=self._model,
            max_tokens=self._max_tokens,
            messages=conversation,
            system=system_blocks if system_blocks else anthropic.omit,
            tools=[_to_anthropic_tool(tool=tool) for tool in tools] if tools else anthropic.omit,
            output_config={"effort": self._reasoning_effort.value} if self._supports_effort else anthropic.omit,
        ) as stream:
            for event in stream:
                if isinstance(event, anthropic.types.RawMessageDeltaEvent):
                    delta = event.delta.model_dump(mode="json")
                    if delta.get("stop_details") is not None:
                        stop_details = delta["stop_details"]
            response = stream.get_final_message()
            request_id = stream.request_id
        stop_details = response.model_dump(mode="json").get("stop_details") or stop_details
        parsed = _parse_response(content_blocks=response.content)
        parsed = dataclasses.replace(
            parsed,
            stop_reason=response.stop_reason,
            provider_metadata={
                "provider": "anthropic",
                "request_id": request_id,
                "response_id": response.id,
                "model": response.model,
                "stop_sequence": response.stop_sequence,
                "stop_details": stop_details,
                "max_tokens": self._max_tokens,
                "reasoning_effort": self._reasoning_effort.value if self._supports_effort else None,
                "content": [block.model_dump(mode="json", exclude_none=True) for block in response.content],
            },
        )
        return _with_usage(parsed=parsed, usage=response.usage)


def _with_usage(parsed: Response, usage: anthropic.types.Usage) -> Response:
    # Anthropic reports cache traffic apart from input_tokens (a cache write at 1.25x the input
    # rate, a cache read at a model-specific discount); normalize to total prompt tokens so
    # token counts mean the same thing across providers, and keep the cached shares for pricing
    cache_write_tokens = usage.cache_creation_input_tokens or 0
    cache_read_tokens = usage.cache_read_input_tokens or 0
    return dataclasses.replace(
        parsed,
        input_tokens=usage.input_tokens + cache_write_tokens + cache_read_tokens,
        output_tokens=usage.output_tokens,
        cache_write_tokens=cache_write_tokens,
        cache_read_tokens=cache_read_tokens,
    )


def _split_system(messages: list[Message]) -> tuple[str | None, list[anthropic.types.MessageParam]]:
    system_parts = [message.content for message in messages if message.role == "system" and message.content]
    conversation = [_to_anthropic_message(message=m) for m in messages if m.role != "system"]
    system_prompt = "\n\n".join(system_parts) if system_parts else None
    return system_prompt, conversation


def _system_blocks(system_prompt: str | None) -> list[anthropic.types.TextBlockParam]:
    """Render the system prompt as a cached block so its tokens are billed once per prefix, not per call.

    The system prompt is identical across all of a player's calls in a game, so
    marking it ``ephemeral`` lets the API serve it from cache on every call after
    the first (subject to the model's minimum cacheable prefix and cache TTL).
    """
    if not system_prompt:
        return []
    return [{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}]


def _to_anthropic_message(message: Message) -> anthropic.types.MessageParam:
    if message.role == "tool":
        return {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": message.tool_call_id or "", "content": message.content or ""}
            ],
        }

    if message.role == "assistant" and message.tool_calls:
        provider_data = message.tool_calls[0].provider_data
        if isinstance(provider_data, dict) and "anthropic_content" in provider_data:
            # Echo the exact assistant turn, including signed thinking and block order.
            # ToolCall provider data is also persisted by execution checkpoints.
            return {
                "role": "assistant",
                "content": cast(
                    list[anthropic.types.ContentBlockParam],
                    cast(dict[str, object], provider_data)["anthropic_content"],
                ),
            }
        blocks: list[anthropic.types.ContentBlockParam] = []
        if message.content:
            blocks.append({"type": "text", "text": message.content})
        for call in message.tool_calls:
            tool_use: anthropic.types.ToolUseBlockParam = {
                "type": "tool_use",
                "id": call.id,
                "name": call.name,
                "input": call.arguments,
            }
            blocks.append(tool_use)
        return {"role": "assistant", "content": blocks}

    role: Literal["user", "assistant"] = "assistant" if message.role == "assistant" else "user"
    return {"role": role, "content": message.content or ""}


def _to_anthropic_tool(tool: Tool) -> anthropic.types.ToolParam:
    return {"name": tool.name, "description": tool.description, "input_schema": tool.parameters}


def _parse_response(content_blocks: Sequence[anthropic.types.ContentBlock]) -> Response:
    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    for block in content_blocks:
        if isinstance(block, anthropic.types.TextBlock):
            text_parts.append(block.text)
        elif isinstance(block, anthropic.types.ToolUseBlock):
            arguments = block.input if isinstance(block.input, dict) else {}
            tool_calls.append(ToolCall(id=block.id, name=block.name, arguments=arguments))

    if tool_calls and any(block.type in {"thinking", "redacted_thinking"} for block in content_blocks):
        tool_calls[0] = dataclasses.replace(
            tool_calls[0],
            provider_data={
                "anthropic_content": [block.model_dump(mode="json", exclude_none=True) for block in content_blocks]
            },
        )
    text = "".join(text_parts) or None
    return Response(text=text, tool_calls=tuple(tool_calls))
