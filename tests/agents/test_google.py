from google.genai import types

from risk_bench.agents.base import Message
from risk_bench.agents.google import _parse_response, _to_content


def gemini_function_call_response(thought_signature: bytes | None) -> types.GenerateContentResponse:
    part = types.Part(
        function_call=types.FunctionCall(name="attack", args={"attacker_dice": 3}),
        thought_signature=thought_signature,
    )
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=[part]))]
    )


def test_thought_signatures_round_trip_through_replayed_tool_calls():
    """Gemini 3 rejects replayed function calls whose thought_signature is missing."""
    response = _parse_response(response=gemini_function_call_response(thought_signature=b"signature-bytes"))
    call = response.tool_calls[0]
    assert call.provider_data == b"signature-bytes"

    replayed = _to_content(message=Message(role="assistant", tool_calls=(call,)))
    assert replayed.parts is not None
    assert replayed.parts[0].thought_signature == b"signature-bytes"


def test_tool_calls_without_signatures_replay_cleanly():
    response = _parse_response(response=gemini_function_call_response(thought_signature=None))
    call = response.tool_calls[0]
    assert call.provider_data is None
    assert call.arguments == {"attacker_dice": 3}

    replayed = _to_content(message=Message(role="assistant", tool_calls=(call,)))
    assert replayed.parts is not None
    assert replayed.parts[0].thought_signature is None
