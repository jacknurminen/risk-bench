from __future__ import annotations

import dataclasses
from typing import Literal

import anthropic

from risk_bench.agents.base import Message, ReasoningEffort, Response, Tool, ToolCall


class AnthropicAgent:
    """Provider-agnostic chat model backed by the Anthropic Messages API."""

    def __init__(self, model: str, api_key: str, max_tokens: int, reasoning_effort: ReasoningEffort) -> None:
        self._model = model
        self._max_tokens = max_tokens
        self._reasoning_effort = reasoning_effort
        # The effort parameter is rejected by Haiku-class models; only send it where it is supported.
        self._supports_effort = "haiku" not in model.lower()
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

        response = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            messages=conversation,
            system=system_blocks if system_blocks else anthropic.omit,
            tools=[_to_anthropic_tool(tool=tool) for tool in tools] if tools else anthropic.omit,
            output_config={"effort": self._reasoning_effort.value} if self._supports_effort else anthropic.omit,
        )
        parsed = _parse_response(content_blocks=response.content)
        return dataclasses.replace(
            parsed, input_tokens=response.usage.input_tokens, output_tokens=response.usage.output_tokens
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


def _parse_response(content_blocks: list[anthropic.types.ContentBlock]) -> Response:
    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    for block in content_blocks:
        if isinstance(block, anthropic.types.TextBlock):
            text_parts.append(block.text)
        elif isinstance(block, anthropic.types.ToolUseBlock):
            arguments = block.input if isinstance(block.input, dict) else {}
            tool_calls.append(ToolCall(id=block.id, name=block.name, arguments=arguments))

    text = "".join(text_parts) or None
    return Response(text=text, tool_calls=tuple(tool_calls))
