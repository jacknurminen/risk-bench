from types import SimpleNamespace

import openai.types.responses
import pytest
from openai.types.responses import (
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
    ResponseUsage,
)
from openai.types.responses.response_usage import InputTokensDetails, OutputTokensDetails

from risk_bench.agents.base import Message, ReasoningEffort, Response, Tool, ToolCall
from risk_bench.agents.openai import (
    OpenAIAgent,
    _parse_response,
    _to_input_items,
    _to_input_items_for_message,
    _with_usage,
)


def _items(message: Message) -> list[openai.types.responses.ResponseInputItemParam]:
    return list(_to_input_items_for_message(message=message))


def _output_text(text: str) -> ResponseOutputMessage:
    return ResponseOutputMessage(
        id="msg_1",
        role="assistant",
        status="completed",
        type="message",
        content=[ResponseOutputText(type="output_text", text=text, annotations=[])],
    )


class _FakeResponses:
    """Records the kwargs of each responses.create call and replays a canned reply."""

    def __init__(self, output: list[object]) -> None:
        self._output = output
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(output=self._output, usage=None)


def test_the_agent_sends_reasoning_effort_alongside_function_tools():
    # The regression this endpoint move exists for: on /v1/chat/completions the GPT-5.6 models
    # reject reasoning_effort outright whenever function tools are present, which silently cost
    # every OpenAI player its reasoning. The responses endpoint takes both together.
    agent = OpenAIAgent(model="gpt-5.6-luna", api_key="unused", max_tokens=16, reasoning_effort=ReasoningEffort.HIGH)
    responses = _FakeResponses(output=[_output_text("ok")])
    agent._client = SimpleNamespace(responses=responses)
    tool = Tool(name="attack", description="Attack a territory", parameters={"type": "object", "properties": {}})

    response = agent.respond(messages=[Message(role="user", content="hi")], tools=[tool])

    assert response.text == "ok"
    sent = responses.calls[0]
    assert sent["reasoning"] == {"effort": "high"}
    assert sent["tools"] == [
        {
            "type": "function",
            "name": "attack",
            "description": "Attack a territory",
            "parameters": {"type": "object", "properties": {}},
            "strict": False,
        }
    ]
    assert sent["store"] is False  # the caller replays the history, so nothing is kept server-side


def test_a_tool_result_replays_against_the_call_id_it_answers():
    assert _items(Message(role="tool", tool_call_id="call_1", content="conquered brazil")) == [
        {"type": "function_call_output", "call_id": "call_1", "output": "conquered brazil"}
    ]


def test_an_assistant_turn_with_only_tool_calls_replays_as_function_calls_alone():
    call = ToolCall(id="call_1", name="attack", arguments={"from": "peru", "to": "brazil"})

    assert _items(Message(role="assistant", content=None, tool_calls=(call,))) == [
        {
            "type": "function_call",
            "call_id": "call_1",
            "name": "attack",
            "arguments": '{"from": "peru", "to": "brazil"}',
        }
    ]


def test_an_assistant_turn_that_both_spoke_and_called_replays_as_text_then_the_call():
    call = ToolCall(id="call_1", name="attack", arguments={})

    items = _items(Message(role="assistant", content="I will attack.", tool_calls=(call,)))

    assert items[0] == {"role": "assistant", "content": "I will attack."}
    assert items[1]["type"] == "function_call"


@pytest.mark.parametrize("content", ["", None])
def test_an_empty_assistant_turn_still_replays_as_empty_text(content: str | None):
    # The model can spend its whole budget on hidden reasoning and say nothing; dropping the turn
    # would silently rewrite the history the next call is conditioned on.
    assert _items(Message(role="assistant", content=content)) == [{"role": "assistant", "content": ""}]


def test_system_and_user_turns_keep_their_roles_in_order():
    conversation = [
        Message(role="system", content="You play Risk."),
        Message(role="user", content="Your move."),
    ]

    assert list(_to_input_items(messages=conversation)) == [
        {"role": "system", "content": "You play Risk."},
        {"role": "user", "content": "Your move."},
    ]


def test_parsing_keeps_the_call_id_because_that_is_what_a_result_references():
    output = [
        _output_text("Attacking."),
        ResponseFunctionToolCall(
            type="function_call",
            id="fc_internal",  # the item id, which a function_call_output may NOT reference
            call_id="call_1",
            name="attack",
            arguments='{"to": "brazil"}',
        ),
    ]

    parsed = _parse_response(output=output)

    assert parsed.text == "Attacking."
    assert parsed.tool_calls == (ToolCall(id="call_1", name="attack", arguments={"to": "brazil"}),)


def test_parsing_a_turn_with_no_message_reports_no_text():
    parsed = _parse_response(output=[])

    assert parsed.text is None and parsed.tool_calls == ()


def test_usage_keeps_total_input_and_extracts_the_cached_share():
    # OpenAI's input_tokens already INCLUDES the cached share, which is billed at a discount
    usage = ResponseUsage(
        input_tokens=10_000,
        output_tokens=300,
        total_tokens=10_300,
        input_tokens_details=InputTokensDetails(cached_tokens=9_000),
        output_tokens_details=OutputTokensDetails(reasoning_tokens=250),
    )

    response = _with_usage(parsed=Response(text="ok"), usage=usage)

    assert response.input_tokens == 10_000
    assert response.cache_read_tokens == 9_000
    assert response.cache_write_tokens == 0  # OpenAI charges no premium for cache writes
    assert response.output_tokens == 300  # reasoning tokens are billed as output and already counted
