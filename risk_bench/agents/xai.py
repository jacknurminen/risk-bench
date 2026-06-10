from __future__ import annotations

import json

import xai_sdk
import xai_sdk.chat
from xai_sdk.proto import chat_pb2
from xai_sdk.proto.v5 import chat_pb2 as chat_pb2_v5

from risk_bench.agents.base import Message, Response, Tool, ToolCall


class GrokAgent:
    """Provider-agnostic chat model backed by xAI's official Grok SDK."""

    def __init__(self, model: str, api_key: str, max_tokens: int) -> None:
        self._model = model
        self._max_tokens = max_tokens
        self._client = xai_sdk.Client(api_key=api_key)

    def respond(self, messages: list[Message], tools: list[Tool]) -> Response:
        """Generate the next assistant turn via the xAI Grok SDK.

        Args:
            messages: Full conversation history, oldest first.
            tools: Tools the model may call; pass an empty list for plain chat.

        Returns:
            The assistant's reply, with text and/or tool calls populated.
        """
        chat = self._client.chat.create(
            model=self._model,
            max_tokens=self._max_tokens,
            messages=[_to_message(message=message) for message in messages],
            tools=[_to_tool(tool=tool) for tool in tools] if tools else None,
        )
        return _parse_response(response=chat.sample())


def _to_message(message: Message) -> chat_pb2.Message:
    if message.role == "system":
        return xai_sdk.chat.system(message.content or "")

    if message.role == "tool":
        return xai_sdk.chat.tool_result(message.content or "", tool_call_id=message.tool_call_id)

    if message.role == "assistant" and message.tool_calls:
        content = [chat_pb2_v5.Content(text=message.content)] if message.content else []
        return chat_pb2_v5.Message(
            role=chat_pb2_v5.MessageRole.ROLE_ASSISTANT,
            content=content,
            tool_calls=[_to_tool_call(call=call) for call in message.tool_calls],
        )

    if message.role == "assistant":
        return xai_sdk.chat.assistant(message.content or "")

    return xai_sdk.chat.user(message.content or "")


def _to_tool_call(call: ToolCall) -> chat_pb2_v5.ToolCall:
    return chat_pb2_v5.ToolCall(
        id=call.id,
        function=chat_pb2_v5.FunctionCall(name=call.name, arguments=json.dumps(call.arguments)),
    )


def _to_tool(tool: Tool) -> chat_pb2.Tool:
    return xai_sdk.chat.tool(name=tool.name, description=tool.description, parameters=tool.parameters)


def _parse_response(response: xai_sdk.chat.Response) -> Response:
    tool_calls = [
        ToolCall(
            id=call.id,
            name=call.function.name,
            arguments=json.loads(call.function.arguments) if call.function.arguments else {},
        )
        for call in response.tool_calls
    ]
    return Response(text=response.content or None, tool_calls=tuple(tool_calls))
