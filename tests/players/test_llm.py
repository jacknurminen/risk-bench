import random

from risk_bench.agents.base import Message, Response, Tool, ToolCall
from risk_bench.game.actions import Attack
from risk_bench.game.types import Phase, Territory
from risk_bench.players.llm import LLMPlayer
from tests.players.test_tools import ALICE, attack_request, request_for_phase


class StubAgent:
    """Returns scripted responses and records every conversation it is sent."""

    def __init__(self, responses: list[Response]) -> None:
        self._responses = list(responses)
        self.conversations: list[list[Message]] = []

    def respond(self, messages: list[Message], tools: list[Tool]) -> Response:
        self.conversations.append(list(messages))
        return self._responses.pop(0)


def tool_call(name: str, **arguments) -> ToolCall:
    return ToolCall(id=f"call-{name}", name=name, arguments=arguments)


VALID_ATTACK_RESPONSE = Response(
    text=None,
    tool_calls=(tool_call("attack", from_territory="alaska", to_territory="kamchatka", attacker_dice=3),),
)
INVALID_ATTACK_RESPONSE = Response(
    text=None,
    tool_calls=(tool_call("attack", from_territory="alaska", to_territory="japan", attacker_dice=3),),
)
EXPECTED_ATTACK = Attack(from_territory=Territory.ALASKA, to_territory=Territory.KAMCHATKA, attacker_dice=3)


def llm_player(agent: StubAgent) -> LLMPlayer:
    return LLMPlayer(player_id=ALICE, agent=agent, max_invalid_retries=2, rng=random.Random(0))


def test_a_valid_tool_call_becomes_the_action():
    agent = StubAgent(responses=[VALID_ATTACK_RESPONSE])
    player = llm_player(agent=agent)

    action = player.decide_action(attack_request())

    assert action == EXPECTED_ATTACK
    assert player.invalid_action_count == 0
    system_prompt = agent.conversations[0][0].content
    assert system_prompt is not None and "SECRET MISSION" in system_prompt


def test_an_invalid_call_is_corrected_with_the_error_and_retried():
    agent = StubAgent(responses=[INVALID_ATTACK_RESPONSE, VALID_ATTACK_RESPONSE])
    player = llm_player(agent=agent)

    action = player.decide_action(attack_request())

    assert action == EXPECTED_ATTACK
    assert player.invalid_action_count == 0
    retry_conversation = agent.conversations[1]
    tool_results = [message for message in retry_conversation if message.role == "tool"]
    assert len(tool_results) == 1
    assert tool_results[0].content is not None and "Invalid action" in tool_results[0].content


def test_exhausted_retries_fall_back_to_a_random_legal_action():
    agent = StubAgent(responses=[INVALID_ATTACK_RESPONSE] * 3)
    player = llm_player(agent=agent)
    request = attack_request()

    action = player.decide_action(request)

    assert action in request.legal_actions
    assert player.invalid_action_count == 1
    assert len(agent.conversations) == 3


def test_responses_without_tool_calls_are_nudged_then_fall_back():
    agent = StubAgent(responses=[Response(text="I think I shall attack Kamchatka!", tool_calls=())] * 3)
    player = llm_player(agent=agent)
    request = attack_request()

    action = player.decide_action(request)

    assert action in request.legal_actions
    assert player.invalid_action_count == 1
    nudges = [message for message in agent.conversations[1] if message.role == "user"]
    assert any(message.content is not None and "calling exactly one" in message.content for message in nudges)


def test_compose_messages_parses_negotiation_calls():
    view = request_for_phase(phase=Phase.ATTACK, hand=[], private_messages_allowed=False).view
    agent = StubAgent(responses=[Response(text=None, tool_calls=(tool_call("send_public_message", text="Hi"),))])

    messages = llm_player(agent=agent).compose_messages(view=view)

    assert len(messages) == 1
    assert messages[0].text == "Hi"
    assert messages[0].sender == ALICE


def test_compose_messages_falls_back_to_silence():
    view = request_for_phase(phase=Phase.ATTACK, hand=[], private_messages_allowed=False).view
    silent = StubAgent(responses=[Response(text=None, tool_calls=(tool_call("say_nothing"),))])
    broken = StubAgent(responses=[Response(text=None, tool_calls=(tool_call("send_telegram", text="?"),))])
    chatty_text_only = StubAgent(responses=[Response(text="Hello everyone!", tool_calls=())])

    assert llm_player(agent=silent).compose_messages(view=view) == []
    assert llm_player(agent=broken).compose_messages(view=view) == []
    assert llm_player(agent=chatty_text_only).compose_messages(view=view) == []
