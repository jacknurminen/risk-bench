from typing import Any

from risk_bench.chat.diplomacy import ChatMessage, MessageVisibility
from risk_bench.game.actions import PlaceArmies
from risk_bench.game.events import ArmiesPlaced, PlayerEliminated, TurnStarted
from risk_bench.game.serialization import action_to_json, chat_message_to_json, event_to_json
from risk_bench.game.types import Territory
from risk_bench.runner.trace_index import build_trace_index, living_players_by_turn


def _records() -> list[dict[str, Any]]:
    message = ChatMessage(
        turn_number=1, sender="alice", visibility=MessageVisibility.PUBLIC, recipient=None, text="hello bob"
    )
    return [
        {"type": "header", "seed": 1},
        {"type": "events", "events": [event_to_json(TurnStarted(player_id="alice", turn_number=1))]},
        {"type": "thought", "player": "alice", "text": "I will attack bob this turn"},
        {"type": "message", "message": chat_message_to_json(message)},
        {
            "type": "action",
            "player": "alice",
            "action": action_to_json(PlaceArmies(territory=Territory.BRAZIL, count=3)),
        },
        {
            "type": "events",
            "events": [event_to_json(ArmiesPlaced(player_id="alice", territory=Territory.BRAZIL, count=3))],
        },
        {
            "type": "commentary",
            "turn": 1,
            "commentator": "anthropic:hi",
            "commentator_index": 0,
            "text": "x",
            "tags": [],
        },
        {"type": "note", "player": "alice", "turn": 1, "text": "bob is my real target"},
        {"type": "events", "events": [event_to_json(TurnStarted(player_id="bob", turn_number=2))]},
        {"type": "thought", "player": "bob", "text": "alice looks aggressive"},
        {"type": "result", "winner": None, "turn_count": 2},
    ]


def test_every_citable_layer_becomes_a_span_in_order_with_turn_and_step():
    spans = build_trace_index(records=_records())

    # one span per citable line: 2 events, 2 thoughts, 1 message, 1 action, 1 note — commentary/header/result excluded
    assert [span.layer for span in spans] == [
        "event",
        "thought",
        "message",
        "action",
        "event",
        "note",
        "event",
        "thought",
    ]
    assert [span.span for span in spans] == [0, 1, 2, 3, 4, 5, 6, 7]
    # turn comes from TurnStarted tracking (and each note's / message's own turn); step is the running action count
    assert [span.turn for span in spans] == [1, 1, 1, 1, 1, 1, 2, 2]
    assert [span.step for span in spans] == [0, 0, 0, 1, 1, 1, 1, 1]
    assert spans[1].player == "alice" and "attack bob" in spans[1].text
    assert spans[5].layer == "note" and spans[5].text == "bob is my real target"


def test_inline_commentary_is_never_indexed():
    spans = build_trace_index(records=_records())
    assert all(span.layer != "commentary" for span in spans)
    assert all("anthropic:hi" not in span.text for span in spans)


def test_appending_records_leaves_earlier_span_handles_unchanged():
    records = _records()
    # a paused game evaluated at a prefix, then resumed: earlier handles must not move
    prefix_spans = build_trace_index(records=records[:6])
    full_spans = build_trace_index(records=records)

    assert full_spans[: len(prefix_spans)] == prefix_spans


def test_living_players_by_turn_drops_a_player_the_turn_after_elimination():
    records = [
        {"type": "header", "seed": 1},
        {"type": "events", "events": [event_to_json(TurnStarted(player_id="alice", turn_number=1))]},
        {"type": "events", "events": [event_to_json(TurnStarted(player_id="alice", turn_number=2))]},
        # bob loses their last territory during turn 2; they are still "alive on turn 2", gone from turn 3
        {
            "type": "events",
            "events": [event_to_json(PlayerEliminated(eliminated_player="bob", eliminated_by="alice"))],
        },
        {"type": "events", "events": [event_to_json(TurnStarted(player_id="alice", turn_number=3))]},
    ]

    living = living_players_by_turn(records=records, player_ids={"alice", "bob", "carla"}, turns=[1, 2, 3])

    assert living[1] == frozenset({"alice", "bob", "carla"})
    assert living[2] == frozenset({"alice", "bob", "carla"})  # eliminated during turn 2, still counted that turn
    assert living[3] == frozenset({"alice", "carla"})  # gone from the next turn on
