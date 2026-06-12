from __future__ import annotations

import dataclasses

from google import genai
from google.genai import types

from risk_bench.agents.base import Message, Response, Tool, ToolCall


class GeminiAgent:
    """Provider-agnostic chat model backed by the Google Gemini API."""

    def __init__(self, model: str, api_key: str, max_tokens: int) -> None:
        self._model = model
        self._max_tokens = max_tokens
        self._client = genai.Client(api_key=api_key)

    def respond(self, messages: list[Message], tools: list[Tool]) -> Response:
        """Generate the next assistant turn via the Gemini API.

        Args:
            messages: Full conversation history, oldest first.
            tools: Tools the model may call; pass an empty list for plain chat.

        Returns:
            The assistant's reply, with text and/or tool calls populated.
        """
        system_instruction, contents = _split_system(messages=messages)

        response = self._client.models.generate_content(
            model=self._model,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                max_output_tokens=self._max_tokens,
                tools=[_to_tool(tool=tool) for tool in tools] if tools else None,
            ),
        )
        parsed = _parse_response(response=response)
        usage = response.usage_metadata
        if usage is None:
            return parsed
        # Gemini bills hidden thinking as output, so it counts toward output tokens here
        output_tokens = (usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0)
        return dataclasses.replace(parsed, input_tokens=usage.prompt_token_count, output_tokens=output_tokens)


def _split_system(messages: list[Message]) -> tuple[str | None, list[types.Content]]:
    system_parts = [message.content for message in messages if message.role == "system" and message.content]
    contents = [_to_content(message=m) for m in messages if m.role != "system"]
    system_instruction = "\n\n".join(system_parts) if system_parts else None
    return system_instruction, contents


def _to_content(message: Message) -> types.Content:
    if message.role == "tool":
        function_response = types.FunctionResponse(
            name=message.tool_call_id or "",
            response={"result": message.content or ""},
        )
        return types.Content(role="user", parts=[types.Part(function_response=function_response)])

    if message.role == "assistant" and message.tool_calls:
        # Gemini 3 requires the thought_signature captured at parse time to be echoed on replay.
        parts = [
            types.Part(
                function_call=types.FunctionCall(name=call.name, args=call.arguments),
                thought_signature=call.provider_data if isinstance(call.provider_data, bytes) else None,
            )
            for call in message.tool_calls
        ]
        return types.Content(role="model", parts=parts)

    role = "model" if message.role == "assistant" else "user"
    return types.Content(role=role, parts=[types.Part(text=message.content or "")])


def _to_tool(tool: Tool) -> types.Tool:
    declaration = types.FunctionDeclaration(
        name=tool.name,
        description=tool.description,
        parameters_json_schema=tool.parameters,
    )
    return types.Tool(function_declarations=[declaration])


def _parse_response(response: types.GenerateContentResponse) -> Response:
    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    for part in _response_parts(response=response):
        call = part.function_call
        if call and (call_name := call.name):
            tool_calls.append(
                ToolCall(
                    id=call_name,
                    name=call_name,
                    arguments=dict(call.args or {}),
                    provider_data=part.thought_signature,
                )
            )
        elif part.text:
            text_parts.append(part.text)

    text = "".join(text_parts) or None
    return Response(text=text, tool_calls=tuple(tool_calls))


def _response_parts(response: types.GenerateContentResponse) -> list[types.Part]:
    if not response.candidates:
        return []
    content = response.candidates[0].content
    if content is None or content.parts is None:
        return []
    return content.parts
