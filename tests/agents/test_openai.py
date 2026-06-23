from types import SimpleNamespace

import httpx
import openai
import pytest

from risk_bench.agents.base import Message, ReasoningEffort
from risk_bench.agents.openai import OpenAIAgent, _rejects_reasoning_effort


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
