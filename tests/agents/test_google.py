from unittest.mock import MagicMock

import pytest
from google import genai
from google.genai import types

from risk_bench import specs
from risk_bench.agents.base import Message, Response
from risk_bench.agents.google import _parse_response, _to_content, _with_usage
from risk_bench.config import Settings


@pytest.mark.parametrize("effort", ["low", "medium", "high"])
def test_argon_passes_selected_reasoning_effort_to_google(monkeypatch: pytest.MonkeyPatch, effort: str) -> None:
    client = MagicMock()
    client.models.generate_content.return_value = gemini_function_call_response(thought_signature=b"argon-signature")
    monkeypatch.setattr(genai, "Client", MagicMock(return_value=client))
    agent = specs.build_agent(
        spec=f"google:gemini-4-argon:{effort}",
        settings=Settings(google_api_key="unused"),
        max_tokens=32_768,
    )

    response = agent.respond(messages=[Message(role="user", content="Choose an action")], tools=[])

    sent = client.models.generate_content.call_args.kwargs
    assert sent["model"] == "gemini-4-argon"
    assert sent["config"].thinking_config.thinking_level.value.lower() == effort
    assert sent["config"].max_output_tokens == 32_768
    assert response.tool_calls[0].name == "attack"
    assert response.tool_calls[0].provider_data == b"argon-signature"


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


def test_usage_keeps_total_input_extracts_the_cached_share_and_bills_thinking_as_output():
    # Gemini's prompt_token_count already INCLUDES the implicitly-cached share
    usage = types.GenerateContentResponseUsageMetadata(
        prompt_token_count=5_000,
        cached_content_token_count=4_000,
        candidates_token_count=200,
        thoughts_token_count=800,
    )

    response = _with_usage(parsed=Response(text="ok"), usage=usage)

    assert response.input_tokens == 5_000
    assert response.cache_read_tokens == 4_000
    assert response.cache_write_tokens == 0  # implicit caching charges no write premium
    assert response.output_tokens == 1_000  # hidden thinking is billed as output
