from risk_bench.game.types import Phase
from risk_bench.players.rendering import render_decision_prompt, render_negotiation_prompt, render_system_prompt
from tests.players.test_tools import attack_request, request_for_phase


def test_system_prompt_contains_identity_rules_and_secret_mission():
    request = attack_request()

    prompt = render_system_prompt(view=request.view)

    assert "You are 'alice'" in prompt
    assert "bob" in prompt
    assert request.view.own_mission.description in prompt
    assert "Never reveal your mission" in prompt
    assert "Rules summary" in prompt


def test_system_prompt_states_the_private_message_policy():
    public_only = request_for_phase(phase=Phase.ATTACK, hand=[], private_messages_allowed=False)
    with_private = request_for_phase(phase=Phase.ATTACK, hand=[], private_messages_allowed=True)

    assert "All messages are public" in render_system_prompt(view=public_only.view)
    assert "Private messages to single players are allowed" in render_system_prompt(view=with_private.view)


def test_decision_prompt_shows_board_players_and_phase_instruction():
    request = attack_request()

    prompt = render_decision_prompt(request=request)

    assert "alaska: alice with 4 armies" in prompt
    assert "borders:" in prompt
    assert "Players:" in prompt
    assert "income 3/turn" in prompt  # alice: two territories, the floor of 3
    assert "income 32/turn" in prompt  # bob: 40 territories // 3 + every continent bonus except North America
    assert "Attack: strike adjacent enemy territories" in prompt
    assert "Reminder of your secret mission" in prompt


def test_negotiation_prompt_invites_a_message():
    request = attack_request()

    prompt = render_negotiation_prompt(view=request.view)

    assert "negotiation round" in prompt
    assert "Send a message" in prompt
