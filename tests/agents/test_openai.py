from types import SimpleNamespace

import httpx
import openai
import pytest
from openai.types import CompletionUsage
from openai.types.completion_usage import PromptTokensDetails

from risk_bench.agents.base import Message, ReasoningEffort, Response, ToolCall
from risk_bench.agents.openai import OpenAIAgent, _rejects_reasoning_effort, _to_openai_message, _with_usage


def _bad_request(message: str, param: str) -> openai.BadRequestError:
    response = httpx.Response(400, request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"))
    return openai.BadRequestError(message, response=response, body={"param": param, "type": "invalid_request_error"})


def _reasoning_effort_error() -> openai.BadRequestError:
    return _bad_request(
        "Function tools with reasoning_effort are not supported for gpt-5.4-mini in /v1/chat/completions.",
        param="reasoning_effort",
    )


class _FakeCompletions:
    """Records each call's reasoning_effort and rejects the parameter once, like a mini model does."""

    def __init__(self, error: openai.BadRequestError) -> None:
        self._error = error
        self.efforts_seen: list[object] = []

    def create(self, **kwargs: object) -> SimpleNamespace:
        effort = kwargs["reasoning_effort"]
        self.efforts_seen.append(effort)
        if not isinstance(effort, openai.Omit):
            raise self._error
        message = SimpleNamespace(content="ok", tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)


def test_rejects_reasoning_effort_matches_only_the_effort_refusal():
    assert _rejects_reasoning_effort(error=_reasoning_effort_error()) is True
    assert _rejects_reasoning_effort(error=_bad_request("messages array is empty", param="messages")) is False


def test_agent_drops_reasoning_effort_and_retries_when_the_model_rejects_it(monkeypatch: pytest.MonkeyPatch):
    agent = OpenAIAgent(model="gpt-5.4-mini", api_key="unused", max_tokens=16, reasoning_effort=ReasoningEffort.LOW)
    completions = _FakeCompletions(error=_reasoning_effort_error())
    monkeypatch.setattr(agent, "_client", SimpleNamespace(chat=SimpleNamespace(completions=completions)))

    response = agent.respond(messages=[Message(role="user", content="hi")], tools=[])

    assert response.text == "ok"
    assert completions.efforts_seen[0] == "low"  # first attempt sent the effort
    assert isinstance(completions.efforts_seen[1], openai.Omit)  # retry dropped it
    assert agent._reasoning_effort_supported is False  # and it stays off for later calls


def test_empty_assistant_turn_converts_with_empty_content_not_a_bare_message():
    # A replayed assistant turn can be empty (the model spent its whole budget on reasoning);
    # the API 400s on an assistant message that has neither content nor tool_calls
    assert _to_openai_message(message=Message(role="assistant", content="")) == {"role": "assistant", "content": ""}
    assert _to_openai_message(message=Message(role="assistant", content=None)) == {"role": "assistant", "content": ""}


def test_assistant_turn_with_only_tool_calls_omits_content():
    call = ToolCall(id="call_1", name="attack", arguments={"from": "peru", "to": "brazil"})

    converted = _to_openai_message(message=Message(role="assistant", content=None, tool_calls=(call,)))

    assert "content" not in converted
    assert converted["tool_calls"] == [
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "attack", "arguments": '{"from": "peru", "to": "brazil"}'},
        }
    ]


def test_usage_keeps_total_input_and_extracts_the_cached_share():
    # OpenAI's prompt_tokens already INCLUDES the cached share, which is billed at a discount
    usage = CompletionUsage(
        prompt_tokens=10_000,
        completion_tokens=300,
        total_tokens=10_300,
        prompt_tokens_details=PromptTokensDetails(cached_tokens=9_000),
    )

    response = _with_usage(parsed=Response(text="ok"), usage=usage)

    assert response.input_tokens == 10_000
    assert response.cache_read_tokens == 9_000
    assert response.cache_write_tokens == 0  # OpenAI charges no premium for cache writes
    assert response.output_tokens == 300


def test_usage_without_prompt_details_reports_no_cached_share():
    usage = CompletionUsage(prompt_tokens=100, completion_tokens=10, total_tokens=110)

    response = _with_usage(parsed=Response(text="ok"), usage=usage)

    assert response.input_tokens == 100 and response.cache_read_tokens == 0
