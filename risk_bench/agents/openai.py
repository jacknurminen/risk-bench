from __future__ import annotations

import dataclasses
import json
from collections.abc import Iterator, Sequence

import openai
import openai.types.responses

from risk_bench.agents.base import Message, ReasoningEffort, Response, Tool, ToolCall


class OpenAIAgent:
    """Provider-agnostic chat model backed by the OpenAI responses API."""

    def __init__(self, model: str, api_key: str, max_tokens: int, reasoning_effort: ReasoningEffort) -> None:
        self._model = model
        self._max_tokens = max_tokens
        self._reasoning_effort = reasoning_effort
        self._client = openai.OpenAI(api_key=api_key)

    def respond(self, messages: list[Message], tools: list[Tool]) -> Response:
        """Generate the next assistant turn via the OpenAI responses API.

        Args:
            messages: Full conversation history, oldest first.
            tools: Tools the model may call; pass an empty list for plain chat.

        Returns:
            The assistant's reply, with text and/or tool calls populated.
        """
        # The responses endpoint, not chat completions: the GPT-5.6 models reject reasoning_effort
        # outright when function tools are present on /v1/chat/completions, and the only accepted
        # value there is 'none'. Reasoning effort is the dial this benchmark compares models on, so
        # the endpoint that supports both is the only one that keeps OpenAI players comparable.
        response = self._client.responses.create(
            model=self._model,
            input=list(_to_input_items(messages=messages)),
            tools=[_to_openai_tool(tool=tool) for tool in tools] if tools else openai.omit,
            reasoning={"effort": self._reasoning_effort.value},
            max_output_tokens=self._max_tokens,
            # The caller replays the whole conversation on every call, so there is no server-side
            # state worth keeping; not storing also keeps game transcripts off OpenAI's servers.
            store=False,
        )
        parsed = _parse_response(output=response.output)
        usage = response.usage
        if usage is None:
            return parsed
        return _with_usage(parsed=parsed, usage=usage)


def _with_usage(parsed: Response, usage: openai.types.responses.ResponseUsage) -> Response:
    # input_tokens already includes the cached share, which is billed at the discounted
    # cached-input rate; OpenAI charges no premium for cache writes
    details = usage.input_tokens_details
    return dataclasses.replace(
        parsed,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_write_tokens=0,
        cache_read_tokens=(details.cached_tokens if details is not None else None) or 0,
    )


def _to_input_items(messages: list[Message]) -> Iterator[openai.types.responses.ResponseInputItemParam]:
    for message in messages:
        yield from _to_input_items_for_message(message=message)


def _to_input_items_for_message(
    message: Message,
) -> Iterator[openai.types.responses.ResponseInputItemParam]:
    if message.role == "tool":
        yield {
            "type": "function_call_output",
            "call_id": message.tool_call_id or "",
            "output": message.content or "",
        }
        return

    if message.role == "assistant":
        # A tool-calling turn replays as one function_call item per call, alongside any text the
        # model said. An assistant turn with neither (the model spent its whole budget on hidden
        # reasoning) still has to replay as empty text, or the turn vanishes from the history.
        if message.content or not message.tool_calls:
            yield {"role": "assistant", "content": message.content or ""}
        for call in message.tool_calls:
            yield {
                "type": "function_call",
                "call_id": call.id,
                "name": call.name,
                "arguments": json.dumps(call.arguments),
            }
        return

    role = "system" if message.role == "system" else "user"
    yield {"role": role, "content": message.content or ""}


def _to_openai_tool(tool: Tool) -> openai.types.responses.ToolParam:
    return {
        "type": "function",
        "name": tool.name,
        "description": tool.description,
        "parameters": tool.parameters,
        "strict": False,
    }


def _parse_response(output: Sequence[openai.types.responses.ResponseOutputItem]) -> Response:
    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    for item in output:
        if isinstance(item, openai.types.responses.ResponseOutputMessage):
            text_parts.extend(
                block.text for block in item.content if isinstance(block, openai.types.responses.ResponseOutputText)
            )
        elif isinstance(item, openai.types.responses.ResponseFunctionToolCall):
            # call_id, not id: call_id is what a function_call_output must reference on replay
            arguments = json.loads(item.arguments) if item.arguments else {}
            tool_calls.append(ToolCall(id=item.call_id, name=item.name, arguments=arguments))

    text = "".join(text_parts) or None
    return Response(text=text, tool_calls=tuple(tool_calls))
