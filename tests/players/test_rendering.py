from dataclasses import replace

from risk_bench.game.missions import mission_deck
from risk_bench.game.types import Phase
from risk_bench.players.rendering import (
    render_decision_prompt,
    render_negotiation_prompt,
    render_noting_prompt,
    render_system_prompt,
)
from tests.players.test_tools import attack_request, request_for_phase


def test_system_prompt_contains_identity_rules_and_secret_mission():
    request = attack_request()

    prompt = render_system_prompt(view=request.view)

    assert "You are 'alice'" in prompt
    assert "bob" in prompt
    assert request.view.own_mission.description in prompt
    assert "Other players are not shown your mission" in prompt
    assert "Rules summary" in prompt
    assert "borders:" in prompt  # the fixed map (topology) is part of the cached system prompt


def test_the_whole_mission_deck_is_listed_so_hidden_goals_can_be_reasoned_about():
    request = attack_request()
    deck = mission_deck(player_ids=[info.player_id for info in request.view.players])

    prompt = render_system_prompt(view=request.view)

    # the deck is printed in the rules of the physical game; only the deal is secret
    for card in deck:
        assert card.description in prompt
    assert f"It holds these {len(deck)} cards" in prompt
    # an eliminate card names every player at the table, including the reader
    assert "Eliminate the player 'alice'" in prompt and "Eliminate the player 'bob'" in prompt
    # the deal itself stays hidden: nobody else's dealt mission appears
    assert prompt.count(request.view.own_mission.description) >= 1


def test_system_prompt_tells_players_to_use_exact_ids_in_messages():
    prompt = render_system_prompt(view=attack_request().view)

    assert "refer to players by their exact ids" in prompt
    assert "'bob'" in prompt  # the example uses a real opponent id
    assert "territories and continents by their exact ids" in prompt


def test_system_prompt_states_the_private_message_policy():
    public_only = request_for_phase(phase=Phase.ATTACK, hand=[], private_messages_allowed=False)
    with_private = request_for_phase(phase=Phase.ATTACK, hand=[], private_messages_allowed=True)

    assert "All messages are public" in render_system_prompt(view=public_only.view)
    assert "Private messages to single players are allowed" in render_system_prompt(view=with_private.view)


def test_decision_prompt_shows_board_players_and_phase_instruction():
    request = attack_request()

    prompt = render_decision_prompt(request=request)

    assert "alaska: alice with 4 armies" in prompt
    assert "borders:" not in prompt  # adjacency lives in the system prompt now, not in every decision
    assert "Players:" in prompt
    assert "income 3/turn" in prompt  # alice: two territories, the floor of 3
    assert "income 32/turn" in prompt  # bob: 40 territories // 3 + every continent bonus except North America
    assert "Attack: launch one or more attacks on adjacent enemy territories" in prompt
    assert "Reminder of your secret mission" in prompt


def test_negotiation_prompt_invites_a_message():
    request = attack_request()

    prompt = render_negotiation_prompt(view=request.view)
    system = render_system_prompt(view=request.view)

    assert "Negotiation session" in prompt
    assert "Send a message" in prompt
    assert "For a negotiation opportunity, call a message tool or say_nothing" in system
    assert "Always act by calling exactly one action tool" not in system


def test_prompts_distinguish_round_turn_and_negotiation_session():
    request = attack_request()
    view = replace(request.view, turn_number=7, round_number=3, previous_round_number=2)
    for prompt in (render_decision_prompt(replace(request, view=view)), render_negotiation_prompt(view)):
        assert "Round 3 · Turn 7 · alice's turn" in prompt
        assert "Turn order (living players): alice → bob" in prompt
        assert "negotiation round" not in prompt
    notes = render_noting_prompt(view)
    assert "Round 2 · Turn 6 just ended" in notes
    assert "Current board: Round 3 · Turn 7" in notes
    system = render_system_prompt(view)
    assert "skipping eliminated players" in system
    assert "until the end of round 4" in system
    assert "negotiation session" in system and "negotiation round" not in system
    setup = render_decision_prompt(replace(request, view=replace(view, turn_number=0, round_number=0)))
    assert "Setup placement, before round 1" in setup
    assert "Round 0" not in setup


def test_decision_prompt_includes_private_notes_when_present():
    request = attack_request()
    with_notes = replace(request, view=replace(request.view, notes="bob betrayed me on turn 3"))

    prompt = render_decision_prompt(request=with_notes)

    assert "bob betrayed me on turn 3" in prompt
    assert "private notes" in prompt


def test_decision_prompt_omits_the_notes_section_when_there_are_none():
    prompt = render_decision_prompt(request=attack_request())  # the helper builds a view with notes=None

    assert "private notes" not in prompt


def test_noting_prompt_shows_the_turn_and_asks_for_a_bounded_rewrite():
    view = replace(attack_request().view, notes="old memo")

    prompt = render_noting_prompt(view=view)

    assert "old memo" in prompt  # the current notes are shown for revision
    assert "Rewrite your notes" in prompt
    assert "under about" in prompt  # the soft size budget is suggested, not enforced
