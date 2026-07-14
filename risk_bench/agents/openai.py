from __future__ import annotations

import dataclasses
import json

import openai
import openai.types.chat

from risk_bench.agents.base import Message, ReasoningEffort, Response, Tool, ToolCall


class OpenAIAgent:
    """Provider-agnostic chat model backed by the OpenAI chat completions API."""

    def __init__(self, model: str, api_key: str, max_tokens: int, reasoning_effort: ReasoningEffort) -> None:
        self._model = model
        self._max_tokens = max_tokens
        self._reasoning_effort = reasoning_effort
        # Some models reject reasoning_effort (see _create_chat_completion); flipped off on first refusal.
        self._reasoning_effort_supported = True
        self._client = openai.OpenAI(api_key=api_key)

    def respond(self, messages: list[Message], tools: list[Tool]) -> Response:
        """Generate the next assistant turn via the chat completions API.

        Args:
            messages: Full conversation history, oldest first.
            tools: Tools the model may call; pass an empty list for plain chat.

        Returns:
            The assistant's reply, with text and/or tool calls populated.
        """
        converted_messages = [_to_openai_message(message=message) for message in messages]
        converted_tools = [_to_openai_tool(tool=tool) for tool in tools] if tools else openai.omit
        response = self._create_chat_completion(messages=converted_messages, tools=converted_tools)
        parsed = _parse_response(message=response.choices[0].message)
        usage = response.usage
        if usage is None:
            return parsed
        return dataclasses.replace(parsed, input_tokens=usage.prompt_tokens, output_tokens=usage.completion_tokens)

    def _create_chat_completion(
        self,
        messages: list[openai.types.chat.ChatCompletionMessageParam],
        tools: list[openai.types.chat.ChatCompletionToolParam] | openai.Omit,
    ) -> openai.types.chat.ChatCompletion:
        try:
            return self._client.chat.completions.create(
                model=self._model,
                # newer OpenAI models reject 'max_tokens' in favor of 'max_completion_tokens'
                max_completion_tokens=self._max_tokens,
                reasoning_effort=self._reasoning_effort.value if self._reasoning_effort_supported else openai.omit,
                messages=messages,
                tools=tools,
            )
        except openai.BadRequestError as error:
            if not self._reasoning_effort_supported or not _rejects_reasoning_effort(error=error):
                raise
            # The cheap 'mini' tiers reject reasoning_effort when tools are present on chat
            # completions; drop it for this agent's remaining calls and retry, so those models
            # still run (just at the provider's default reasoning depth).
            self._reasoning_effort_supported = False
            return self._create_chat_completion(messages=messages, tools=tools)


def _rejects_reasoning_effort(error: openai.BadRequestError) -> bool:
    return getattr(error, "param", None) == "reasoning_effort" or "reasoning_effort" in str(error)


def _to_openai_message(message: Message) -> openai.types.chat.ChatCompletionMessageParam:
    if message.role == "system":
        return {"role": "system", "content": message.content or ""}

    if message.role == "tool":
        return {"role": "tool", "tool_call_id": message.tool_call_id or "", "content": message.content or ""}

    if message.role == "assistant":
        assistant: openai.types.chat.ChatCompletionAssistantMessageParam = {"role": "assistant"}
        if message.tool_calls:
            assistant["tool_calls"] = [_to_openai_tool_call(call=call) for call in message.tool_calls]
        # the API rejects an assistant turn carrying neither content nor tool_calls, so an
        # empty turn (e.g. the model spent its whole budget on reasoning) must send "" text
        if message.content or not message.tool_calls:
            assistant["content"] = message.content or ""
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
