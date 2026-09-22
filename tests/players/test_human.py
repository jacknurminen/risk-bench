import json
import random
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import ValidationError

from risk_bench.game.actions import Attack, Fortify, PlaceArmies, SkipFortify, TradeCards
from risk_bench.game.cards import CardDesign, TerritoryCard
from risk_bench.game.engine import setup_game
from risk_bench.game.rules import legal_actions
from risk_bench.game.ruleset import CURRENT_RULESET
from risk_bench.game.serialization import action_to_json
from risk_bench.game.types import Territory
from risk_bench.players.base import Decision, DecisionRequest, NegotiationTurn
from risk_bench.players.human import (
    HumanPlayer,
    HumanResponse,
    parse_response,
    read_prompt,
    submit_response,
    view_to_json,
)
from risk_bench.players.view import build_player_view


def decision_request():
    state = setup_game(player_ids=["human", "bot", "bot2"], rng=random.Random(0), ruleset=CURRENT_RULESET)
    view = build_player_view(state, "human", (), (), True, None, None)
    return DecisionRequest(view, legal_actions(state))


def prompt_for(actions, kind="decision"):
    return {
        "request_id": "abc",
        "waiting": True,
        "kind": kind,
        "view": view_to_json(decision_request().view),
        "actions": [action_to_json(a) for a in actions],
    }


def test_human_waits_and_preserves_a_partial_placement_and_private_reasoning(tmp_path):
    player = HumanPlayer("human", tmp_path)
    request = decision_request()
    with ThreadPoolExecutor() as pool:
        answer = pool.submit(player.decide_action, request)
        deadline = time.monotonic() + 3
        while (prompt := read_prompt(tmp_path)) is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert prompt is not None
        assert not answer.done()
        submit_response(
            tmp_path,
            HumanResponse(
                request_id=prompt["request_id"],
                action_index=0,
                count=2,
                message="Alliance?",
                recipient="bot",
                thought="Protect my border",
            ),
        )
        result = answer.result(timeout=3)
    assert result.actions == (PlaceArmies(request.legal_actions[0].territory, 2),)
    assert result.chat_message is not None
    assert result.chat_message.sender == "human"
    assert result.chat_message.recipient == "bot"
    assert result.thought == "Protect my border"
    completed = read_prompt(tmp_path)
    assert completed is not None and completed["waiting"] is False
    assert player.invalid_action_count == 0


def test_bounds_stale_requests_and_duplicate_submissions_are_rejected(tmp_path):
    prompt = prompt_for([PlaceArmies(Territory.ALASKA, 5)])
    (tmp_path / "request.json").write_text(json.dumps(prompt))
    for response in [
        HumanResponse(request_id="old", action_index=0),
        HumanResponse(request_id="abc", action_index=1),
        HumanResponse(request_id="abc", action_index=0, count=6),
        HumanResponse(request_id="abc", action_index=0, attacker_dice=1),
    ]:
        with pytest.raises(ValueError):
            submit_response(tmp_path, response)
    good = HumanResponse(request_id="abc", action_index=0, count=3)
    submit_response(tmp_path, good)
    with pytest.raises(ValueError, match="already submitted"):
        submit_response(tmp_path, good)


@pytest.mark.parametrize("value", [True, 1.5, "2", 0, -1])
def test_army_counts_are_strict_positive_integers(value):
    with pytest.raises(ValidationError):
        HumanResponse(request_id="abc", count=value)


def test_attack_fortify_trade_and_end_phase_controls():
    attack = Attack(Territory.ALASKA, Territory.KAMCHATKA, 3, 1, 1)
    prompt = prompt_for([attack])
    answer = parse_response(
        prompt,
        HumanResponse(request_id="abc", action_index=0, attacker_dice=2, max_attacks=8, armies_to_leave_behind=3),
    )
    assert isinstance(answer, Decision)
    assert answer.actions == (Attack(Territory.ALASKA, Territory.KAMCHATKA, 2, 8, 3),)
    with pytest.raises(ValueError, match="at most"):
        parse_response(
            prompt_for([Attack(Territory.ALASKA, Territory.KAMCHATKA, 1, 1, 1)]),
            HumanResponse(request_id="abc", action_index=0, attacker_dice=2),
        )
    fortify = Fortify(Territory.ALASKA, Territory.ALBERTA, 6)
    answer = parse_response(prompt_for([fortify]), HumanResponse(request_id="abc", action_index=0, count=2))
    assert isinstance(answer, Decision)
    assert answer.actions == (Fortify(Territory.ALASKA, Territory.ALBERTA, 2),)
    card = TerritoryCard(CardDesign.INFANTRY, Territory.ALASKA)
    trade = TradeCards((card, card, card))
    for action in [trade, SkipFortify()]:
        answer = parse_response(prompt_for([action]), HumanResponse(request_id="abc", action_index=0))
        assert isinstance(answer, Decision)
        assert answer.actions == (action,)


def test_diplomacy_supports_silence_and_rejects_illegal_recipients():
    prompt = prompt_for([], kind="negotiation")
    silence = parse_response(prompt, HumanResponse(request_id="abc"))
    assert isinstance(silence, NegotiationTurn) and silence.messages == ()
    answer = parse_response(prompt, HumanResponse(request_id="abc", message="Hello"))
    assert isinstance(answer, NegotiationTurn)
    message = answer.messages[0]
    assert message.sender == "human"
    assert message.recipient is None
    for recipient in ["human", "missing"]:
        with pytest.raises(ValueError, match="recipient"):
            parse_response(prompt, HumanResponse(request_id="abc", message="Hi", recipient=recipient))
    prompt["view"]["private_messages_allowed"] = False
    with pytest.raises(ValueError, match="recipient"):
        parse_response(prompt, HumanResponse(request_id="abc", message="Hi", recipient="bot"))
    with pytest.raises(ValueError, match="Only a message"):
        parse_response(prompt, HumanResponse(request_id="abc", action_index=0))


def test_notes_allow_replacement_skip_and_clearing_without_public_messages():
    prompt = prompt_for([], kind="notes")
    assert parse_response(prompt, HumanResponse(request_id="abc", notes="  Keep this pact.  ")) == "Keep this pact."
    assert parse_response(prompt, HumanResponse(request_id="abc")) is None
    assert parse_response(prompt, HumanResponse(request_id="abc", notes=" ")) == ""
    for extra in [{"message": "Public"}, {"thought": "Reason"}, {"action_index": 0}, {"recipient": "bot"}]:
        with pytest.raises(ValueError, match="Only private notes"):
            parse_response(prompt, HumanResponse.model_validate({"request_id": "abc", "notes": "Private", **extra}))
    with pytest.raises(ValueError, match="only be saved"):
        parse_response(prompt_for([], kind="negotiation"), HumanResponse(request_id="abc", notes="Private"))


def test_human_notebook_is_prefilled_from_journal_after_restart(tmp_path):
    mailbox = tmp_path / "game.human"
    (tmp_path / "game.jsonl").write_text(
        json.dumps({"type": "note", "player": "human", "turn": 1, "text": "Saved pact"})
        + "\n"
        + json.dumps({"type": "note", "player": "bot", "turn": 1, "text": "Other player's notes"})
        + "\n"
    )
    player = HumanPlayer("human", mailbox)
    with ThreadPoolExecutor() as pool:
        answer = pool.submit(player.update_notes, decision_request().view)
        deadline = time.monotonic() + 3
        while (prompt := read_prompt(mailbox)) is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert prompt is not None
        submit_response(mailbox, HumanResponse(request_id=prompt["request_id"]))
        assert answer.result(timeout=3) is None
    assert prompt["kind"] == "notes" and prompt["actions"] == []
    assert prompt["view"]["notes"] == "Saved pact"


def test_resuming_discards_old_pending_requests(tmp_path):
    (tmp_path / "request.json").write_text(json.dumps(prompt_for([])))
    (tmp_path / "abc.json").write_text("{}")
    HumanPlayer("human", tmp_path)
    assert read_prompt(tmp_path) is None
    assert not (tmp_path / "abc.json").exists()


def test_a_complete_placement_plan_is_validated_before_any_actions_are_returned():
    prompt = prompt_for([PlaceArmies(Territory.ALASKA, 7), PlaceArmies(Territory.ALBERTA, 7)])
    response = HumanResponse.model_validate(
        {
            "request_id": "abc",
            "placements": [{"action_index": 0, "count": 2}, {"action_index": 1, "count": 5}],
            "message": "Reinforcing my borders",
            "thought": "Split the reserve",
        }
    )
    decision = parse_response(prompt, response)
    assert isinstance(decision, Decision)
    assert decision.actions == (PlaceArmies(Territory.ALASKA, 2), PlaceArmies(Territory.ALBERTA, 5))
    assert decision.chat_message is not None and decision.chat_message.text == "Reinforcing my borders"
    assert decision.thought == "Split the reserve"


@pytest.mark.parametrize(
    "placements",
    [
        [{"action_index": 0, "count": 6}],
        [{"action_index": 0, "count": 8}],
        [{"action_index": 0, "count": 2}, {"action_index": 0, "count": 5}],
        [{"action_index": 2, "count": 7}],
        [{"action_index": 1, "count": 7}],
    ],
)
def test_invalid_placement_plans_never_enter_the_mailbox(tmp_path, placements):
    prompt = prompt_for([PlaceArmies(Territory.ALASKA, 7), SkipFortify()])
    (tmp_path / "request.json").write_text(json.dumps(prompt))
    with pytest.raises(ValueError):
        submit_response(tmp_path, HumanResponse.model_validate({"request_id": "abc", "placements": placements}))
    assert not (tmp_path / "abc.json").exists()


def test_placement_plans_cannot_include_another_action_or_diplomacy_move():
    response = HumanResponse.model_validate({"request_id": "abc", "placements": [{"action_index": 0, "count": 7}]})
    with pytest.raises(ValueError, match="either"):
        parse_response(prompt_for([PlaceArmies(Territory.ALASKA, 7)]), response.model_copy(update={"count": 7}))
    with pytest.raises(ValueError, match="Only a message"):
        parse_response(prompt_for([], kind="negotiation"), response)
