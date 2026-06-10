from __future__ import annotations

import json

import openai
import openai.types.chat

from risk_bench.agents.base import Message, Response, Tool, ToolCall


class OpenAIAgent:
    """Provider-agnostic chat model backed by the OpenAI chat completions API."""

    def __init__(self, model: str, api_key: str, max_tokens: int) -> None:
        self._model = model
        self._max_tokens = max_tokens
        self._client = openai.OpenAI(api_key=api_key)

    def respond(self, messages: list[Message], tools: list[Tool]) -> Response:
        """Generate the next assistant turn via the chat completions API.

        Args:
            messages: Full conversation history, oldest first.
            tools: Tools the model may call; pass an empty list for plain chat.

        Returns:
            The assistant's reply, with text and/or tool calls populated.
        """
        response = self._client.chat.completions.create(
            model=self._model,
            # newer OpenAI models reject 'max_tokens' in favor of 'max_completion_tokens'
            max_completion_tokens=self._max_tokens,
            messages=[_to_openai_message(message=message) for message in messages],
            tools=[_to_openai_tool(tool=tool) for tool in tools] if tools else openai.omit,
        )
        return _parse_response(message=response.choices[0].message)


def _to_openai_message(message: Message) -> openai.types.chat.ChatCompletionMessageParam:
    if message.role == "system":
        return {"role": "system", "content": message.content or ""}

    if message.role == "tool":
        return {"role": "tool", "tool_call_id": message.tool_call_id or "", "content": message.content or ""}

    if message.role == "assistant":
        assistant: openai.types.chat.ChatCompletionAssistantMessageParam = {"role": "assistant"}
        if message.content:
            assistant["content"] = message.content
        if message.tool_calls:
            assistant["tool_calls"] = [_to_openai_tool_call(call=call) for call in message.tool_calls]
        return assistant

    return {"role": "user", "content": message.content or ""}


def _to_openai_tool_call(call: ToolCall) -> openai.types.chat.ChatCompletionMessageFunctionToolCallParam:
    return {
        "id": call.id,
        "type": "function",
        "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
    }


def _to_openai_tool(tool: Tool) -> openai.types.chat.ChatCompletionToolParam:
    return {
        "type": "function",
        "function": {"name": tool.name, "description": tool.description, "parameters": tool.parameters},
    }


def _parse_response(message: openai.types.chat.ChatCompletionMessage) -> Response:
    tool_calls: list[ToolCall] = []
    for call in message.tool_calls or []:
        if not isinstance(call, openai.types.chat.ChatCompletionMessageFunctionToolCall):
            continue
        arguments = json.loads(call.function.arguments) if call.function.arguments else {}
        tool_calls.append(ToolCall(id=call.id, name=call.function.name, arguments=arguments))

    return Response(text=message.content, tool_calls=tuple(tool_calls))
