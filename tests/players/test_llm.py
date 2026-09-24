import pytest

from risk_bench.agents.base import Message, Response, Tool, ToolCall
from risk_bench.game.actions import Attack, PlaceArmies
from risk_bench.game.types import Phase, Territory
from risk_bench.players.llm import InvalidResponseError, LLMPlayer, ProviderUnavailableError
from tests.players.test_tools import ALICE, attack_request, reinforce_request_with_tradeable_set, request_for_phase


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
    tool_calls=(
        tool_call("attack", launches=[{"from_territory": "alaska", "to_territory": "kamchatka", "attacker_dice": 3}]),
    ),
)
INVALID_ATTACK_RESPONSE = Response(
    text=None,
    tool_calls=(
        tool_call("attack", launches=[{"from_territory": "alaska", "to_territory": "japan", "attacker_dice": 3}]),
    ),
)
EXPECTED_ATTACK = Attack(
    from_territory=Territory.ALASKA,
    to_territory=Territory.KAMCHATKA,
    attacker_dice=3,
    max_attacks=1,
    armies_to_leave_behind=1,
)


def llm_player(agent: StubAgent) -> LLMPlayer:
    return LLMPlayer(player_id=ALICE, agent=agent, max_invalid_retries=2)


def test_a_valid_tool_call_becomes_the_action():
    agent = StubAgent(responses=[VALID_ATTACK_RESPONSE])
    player = llm_player(agent=agent)

    decision = player.decide_action(attack_request())

    assert decision.actions == (EXPECTED_ATTACK,)
    assert decision.chat_message is None
    assert player.invalid_action_count == 0
    system_prompt = agent.conversations[0][0].content
    assert system_prompt is not None and "SECRET MISSION" in system_prompt


def test_an_invalid_call_is_corrected_with_the_error_and_retried():
    agent = StubAgent(responses=[INVALID_ATTACK_RESPONSE, VALID_ATTACK_RESPONSE])
    player = llm_player(agent=agent)

    decision = player.decide_action(attack_request())

    assert decision.actions == (EXPECTED_ATTACK,)
    assert player.invalid_action_count == 0
    retry_conversation = agent.conversations[1]
    tool_results = [message for message in retry_conversation if message.role == "tool"]
    assert len(tool_results) == 1
    assert tool_results[0].content is not None and "Invalid action" in tool_results[0].content


def test_exhausted_retries_pause_without_an_action():
    agent = StubAgent(responses=[INVALID_ATTACK_RESPONSE] * 3)
    player = llm_player(agent=agent)
    request = attack_request()

    with pytest.raises(ProviderUnavailableError):
        player.decide_action(request)
    assert player.invalid_action_count == 1
    assert len(agent.conversations) == 3


def test_responses_without_tool_calls_are_nudged_then_pause():
    agent = StubAgent(responses=[Response(text="I think I shall attack Kamchatka!", tool_calls=())] * 3)
    player = llm_player(agent=agent)
    request = attack_request()

    with pytest.raises(ProviderUnavailableError):
        player.decide_action(request)
    assert player.invalid_action_count == 1
    nudges = [message for message in agent.conversations[1] if message.role == "user"]
    assert any(message.content is not None and "calling exactly one" in message.content for message in nudges)


class FailingAgent:
    """Raises on every request, like a provider outage or a content-policy flag."""

    def respond(self, messages: list[Message], tools: list[Tool]) -> Response:
        raise RuntimeError("400 invalid_prompt: flagged as potentially violating our usage policy")


def test_provider_failure_pauses_immediately():
    player = LLMPlayer(
        player_id=ALICE,
        agent=FailingAgent(),
        max_invalid_retries=2,
    )
    request = attack_request()

    with pytest.raises(ProviderUnavailableError):
        player.decide_action(request)
    assert player.invalid_action_count == 0


def test_persistent_provider_failure_pauses_the_game_instead_of_playing_randomly():
    player = LLMPlayer(
        player_id=ALICE,
        agent=FailingAgent(),
        max_invalid_retries=2,
    )
    request = attack_request()

    for _ in range(2):
        with pytest.raises(ProviderUnavailableError, match="provider failed"):
            player.decide_action(request)


def test_a_successful_call_resets_the_provider_failure_streak():
    class FlakyAgent:
        """Fails twice before every successful response, like an intermittent provider."""

        def __init__(self) -> None:
            self._calls = 0

        def respond(self, messages: list[Message], tools: list[Tool]) -> Response:
            self._calls += 1
            if self._calls % 3 != 0:
                raise RuntimeError("429 rate limited")
            return VALID_ATTACK_RESPONSE

    player = LLMPlayer(
        player_id=ALICE,
        agent=FlakyAgent(),
        max_invalid_retries=2,
    )
    request = attack_request()

    for _ in range(4):
        for _ in range(2):
            with pytest.raises(ProviderUnavailableError):
                player.decide_action(request)
        assert player.decide_action(request).actions == (EXPECTED_ATTACK,)
        assert player._consecutive_provider_failures == 0
    assert player.invalid_action_count == 0


def test_provider_failures_during_negotiation_pause():
    player = LLMPlayer(
        player_id=ALICE,
        agent=FailingAgent(),
        max_invalid_retries=2,
    )
    view = request_for_phase(phase=Phase.ATTACK, hand=[], private_messages_allowed=False).view

    with pytest.raises(ProviderUnavailableError):
        player.compose_messages(view=view)


def test_a_placement_split_becomes_a_batch_of_actions_from_one_response():
    response = Response(
        text=None,
        tool_calls=(
            tool_call(
                "place_armies",
                placements=[{"territory": "alaska", "count": 3}, {"territory": "alberta", "count": 2}],
            ),
        ),
    )
    player = llm_player(agent=StubAgent(responses=[response]))

    decision = player.decide_action(reinforce_request_with_tradeable_set())

    assert decision.actions == (
        PlaceArmies(territory=Territory.ALASKA, count=3),
        PlaceArmies(territory=Territory.ALBERTA, count=2),
    )
    assert player.invalid_action_count == 0


def test_a_message_call_alongside_the_action_becomes_table_talk():
    response = Response(
        text=None,
        tool_calls=(
            tool_call(
                "attack",
                launches=[{"from_territory": "alaska", "to_territory": "kamchatka", "attacker_dice": 3}],
            ),
            tool_call("send_public_message", text="This is for Ukraine!"),
        ),
    )
    player = llm_player(agent=StubAgent(responses=[response]))

    decision = player.decide_action(attack_request())

    assert decision.actions == (EXPECTED_ATTACK,)
    assert decision.chat_message is not None
    assert decision.chat_message.text == "This is for Ukraine!"
    assert decision.chat_message.sender == ALICE


def test_multiple_messages_require_correction():
    response = Response(
        text=None,
        tool_calls=(
            tool_call("send_public_message", text="First!"),
            VALID_ATTACK_RESPONSE.tool_calls[0],
            tool_call("send_public_message", text="Second!"),
        ),
    )
    with pytest.raises(InvalidResponseError):
        llm_player(agent=StubAgent(responses=[response] * 3)).decide_action(attack_request())


def test_extra_action_tools_are_not_silently_discarded():
    response = Response(text=None, tool_calls=VALID_ATTACK_RESPONSE.tool_calls * 2)
    agent = StubAgent([response, VALID_ATTACK_RESPONSE])
    player = llm_player(agent)
    assert player.decide_action(attack_request()).actions == (EXPECTED_ATTACK,)
    assert len(agent.conversations) == 2
    assert any("exactly one action tool" in (m.content or "") for m in agent.conversations[1])


def test_a_message_only_response_is_pushed_back_for_an_action():
    message_only = Response(text=None, tool_calls=(tool_call("send_public_message", text="Thinking out loud..."),))
    agent = StubAgent(responses=[message_only, VALID_ATTACK_RESPONSE])

    decision = llm_player(agent=agent).decide_action(attack_request())

    assert decision.actions == (EXPECTED_ATTACK,)
    assert decision.chat_message is None  # the chatter on the failed attempt is not delivered
    pushbacks = [message for message in agent.conversations[1] if message.role == "tool"]
    assert len(pushbacks) == 1
    assert pushbacks[0].content is not None and "must include a game action" in pushbacks[0].content


def test_messages_on_responses_with_invalid_actions_are_not_delivered():
    invalid_with_message = Response(
        text=None,
        tool_calls=(
            INVALID_ATTACK_RESPONSE.tool_calls[0],
            tool_call("send_public_message", text="Goodbye, Japan!"),
        ),
    )
    agent = StubAgent(responses=[invalid_with_message, VALID_ATTACK_RESPONSE])

    decision = llm_player(agent=agent).decide_action(attack_request())

    assert decision.actions == (EXPECTED_ATTACK,)
    assert decision.chat_message is None
    pushbacks = [message for message in agent.conversations[1] if message.role == "tool"]
    assert len(pushbacks) == 2  # both the failed action and the undelivered message are answered
    assert any(message.content is not None and "Not delivered" in message.content for message in pushbacks)


def test_an_invalid_message_is_corrected_before_any_action():
    response = Response(
        text=None,
        tool_calls=(
            VALID_ATTACK_RESPONSE.tool_calls[0],
            tool_call("send_private_message", recipient="bob", text="Psst"),  # private messages are off
        ),
    )
    player = llm_player(agent=StubAgent(responses=[response, VALID_ATTACK_RESPONSE]))

    decision = player.decide_action(attack_request())

    assert decision.actions == (EXPECTED_ATTACK,)
    assert decision.chat_message is None
    assert player.invalid_action_count == 0


def test_thoughts_are_captured_from_the_successful_responses_text():
    response = Response(
        text="Kamchatka is weakly held; taking it cuts gpt off from Asia.",
        tool_calls=VALID_ATTACK_RESPONSE.tool_calls,
    )
    decision = llm_player(agent=StubAgent(responses=[response])).decide_action(attack_request())

    assert decision.actions == (EXPECTED_ATTACK,)
    assert decision.thought == "Kamchatka is weakly held; taking it cuts gpt off from Asia."


def test_a_responses_blank_text_means_no_thought():
    decision = llm_player(agent=StubAgent(responses=[VALID_ATTACK_RESPONSE])).decide_action(attack_request())

    assert decision.thought is None


def test_compose_messages_parses_negotiation_calls_and_captures_the_thought():
    view = request_for_phase(phase=Phase.ATTACK, hand=[], private_messages_allowed=False).view
    agent = StubAgent(
        responses=[Response(text="A truce buys me time.", tool_calls=(tool_call("send_public_message", text="Hi"),))]
    )

    negotiation_turn = llm_player(agent=agent).compose_messages(view=view)

    assert len(negotiation_turn.messages) == 1
    assert negotiation_turn.messages[0].text == "Hi"
    assert negotiation_turn.messages[0].sender == ALICE
    assert negotiation_turn.thought == "A truce buys me time."


def test_silence_requires_an_explicit_pass():
    view = request_for_phase(phase=Phase.ATTACK, hand=[], private_messages_allowed=False).view
    silent = StubAgent(responses=[Response(text=None, tool_calls=(tool_call("say_nothing"),))])
    broken = StubAgent(responses=[Response(text=None, tool_calls=(tool_call("send_telegram", text="?"),))] * 3)
    chatty_text_only = StubAgent(responses=[Response(text="Hello everyone!", tool_calls=())] * 3)

    assert llm_player(agent=silent).compose_messages(view=view).messages == ()
    for agent in (broken, chatty_text_only):
        with pytest.raises(InvalidResponseError):
            llm_player(agent=agent).compose_messages(view=view)


def test_empty_negotiation_is_corrected_then_explicitly_passes():
    view = request_for_phase(phase=Phase.ATTACK, hand=[], private_messages_allowed=False).view
    agent = StubAgent([
        Response(text=None, tool_calls=()),
        Response(text="I have nothing to add.", tool_calls=(tool_call("say_nothing"),)),
    ])
    player = llm_player(agent)

    turn = player.compose_messages(view)

    assert turn.messages == ()
    assert turn.thought == "I have nothing to add."
    assert any("explicit say_nothing" in (m.content or "") for m in agent.conversations[1])
    assert player._retry_conversation is None


def test_negotiation_correction_survives_pause_and_checkpoint():
    from risk_bench.runner import checkpoint

    view = request_for_phase(phase=Phase.ATTACK, hand=[], private_messages_allowed=False).view
    empty = Response(text=None, tool_calls=())
    player = llm_player(StubAgent([empty] * 3))
    with pytest.raises(InvalidResponseError):
        player.compose_messages(view)
    saved = checkpoint.player_state(player)
    restored_agent = StubAgent([Response(text=None, tool_calls=(tool_call("say_nothing"),))])
    restored = llm_player(restored_agent)
    checkpoint.restore_player(restored, saved)

    assert restored.compose_messages(view).messages == ()
    assert len(restored_agent.conversations[0]) > 2
    assert any("explicit say_nothing" in (m.content or "") for m in restored_agent.conversations[0])


def test_invalid_retries_preserve_context_and_resume_with_model_choice():
    agent = StubAgent([INVALID_ATTACK_RESPONSE] * 3 + [VALID_ATTACK_RESPONSE])
    player = llm_player(agent)
    with pytest.raises(InvalidResponseError):
        player.decide_action(attack_request())
    assert player.decide_action(attack_request()).actions == (EXPECTED_ATTACK,)
    assert len(agent.conversations[-1]) > 2


@pytest.mark.parametrize("bad", [Response(text=""), Response(text="notes", tool_calls=(tool_call("say_nothing"),))])
def test_invalid_notes_pause_and_later_resume(bad):
    agent = StubAgent([bad, Response(text="Remember Bob's promise.")])
    player = llm_player(agent)
    view = attack_request().view
    with pytest.raises(InvalidResponseError):
        player.update_notes(view)
    assert player.update_notes(view) == "Remember Bob's promise."
    assert agent.conversations[0] == agent.conversations[1]
