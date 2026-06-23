import json

import pytest

from risk_bench.chat.diplomacy import ChatMessage, MessageVisibility
from risk_bench.game.actions import (
    Action,
    Attack,
    EndAttackPhase,
    Fortify,
    PlaceArmies,
    SkipFortify,
    TradeCards,
)
from risk_bench.game.cards import CardDesign, TerritoryCard
from risk_bench.game.combat import BattleOutcome
from risk_bench.game.events import (
    ArmiesPlaced,
    BattleResolved,
    CardDrawn,
    CardsTraded,
    Fortified,
    GameEvent,
    MissionCompleted,
    PlayerEliminated,
    TerritoryConquered,
    TurnLimitReached,
    TurnStarted,
)
from risk_bench.game.serialization import (
    action_from_json,
    action_to_json,
    chat_message_from_json,
    chat_message_to_json,
    event_from_json,
    event_to_json,
)
from risk_bench.game.types import Territory

ALL_ACTIONS: list[Action] = [
    PlaceArmies(territory=Territory.ALASKA, count=5),
    TradeCards(
        cards=(
            TerritoryCard(design=CardDesign.INFANTRY, territory=Territory.PERU),
            TerritoryCard(design=CardDesign.CAVALRY, territory=Territory.JAPAN),
            TerritoryCard(design=CardDesign.WILD, territory=None),
        )
    ),
    Attack(
        from_territory=Territory.EGYPT,
        to_territory=Territory.MIDDLE_EAST,
        attacker_dice=3,
        max_attacks=4,
        armies_to_leave_behind=2,
    ),
    Fortify(from_territory=Territory.BRAZIL, to_territory=Territory.PERU, count=2),
    EndAttackPhase(),
    SkipFortify(),
]

ALL_EVENTS: list[GameEvent] = [
    TurnStarted(player_id="alice", turn_number=3),
    ArmiesPlaced(player_id="alice", territory=Territory.ALASKA, count=5),
    CardsTraded(
        player_id="alice",
        card_designs=(CardDesign.CAVALRY, CardDesign.INFANTRY, CardDesign.WILD),
        bonus_armies=10,
    ),
    BattleResolved(
        attacker="alice",
        defender="bob",
        from_territory=Territory.EGYPT,
        to_territory=Territory.MIDDLE_EAST,
        outcome=BattleOutcome(attacker_dice=(6, 4, 1), defender_dice=(5, 5), attacker_losses=2, defender_losses=0),
    ),
    TerritoryConquered(player_id="alice", territory=Territory.MIDDLE_EAST, from_territory=Territory.EGYPT),
    Fortified(player_id="alice", from_territory=Territory.BRAZIL, to_territory=Territory.PERU, count=2),
    CardDrawn(player_id="alice"),
    PlayerEliminated(eliminated_player="bob", eliminated_by="alice"),
    MissionCompleted(player_id="alice", mission_description="Conquer 24 territories."),
    TurnLimitReached(turn_number=150),
]

ALL_MESSAGES = [
    ChatMessage(turn_number=1, sender="alice", visibility=MessageVisibility.PUBLIC, recipient=None, text="Hi all"),
    ChatMessage(turn_number=2, sender="bob", visibility=MessageVisibility.PRIVATE, recipient="carla", text="Psst"),
]


@pytest.mark.parametrize("action", ALL_ACTIONS, ids=lambda action: type(action).__name__)
def test_every_action_round_trips_through_json(action: Action):
    serialized = json.loads(json.dumps(action_to_json(action)))
    assert action_from_json(serialized) == action


@pytest.mark.parametrize("event", ALL_EVENTS, ids=lambda event: type(event).__name__)
def test_every_event_round_trips_through_json(event: GameEvent):
    serialized = json.loads(json.dumps(event_to_json(event)))
    assert event_from_json(serialized) == event


@pytest.mark.parametrize("message", ALL_MESSAGES, ids=lambda message: message.visibility)
def test_chat_messages_round_trip_through_json(message: ChatMessage):
    serialized = json.loads(json.dumps(chat_message_to_json(message)))
    assert chat_message_from_json(serialized) == message


def test_round_trip_restores_enum_types_not_plain_strings():
    action = action_from_json(json.loads(json.dumps(action_to_json(ALL_ACTIONS[0]))))
    assert isinstance(action, PlaceArmies)
    assert isinstance(action.territory, Territory)


def test_unknown_kinds_are_rejected():
    with pytest.raises(ValueError, match="action kind"):
        action_from_json({"kind": "launch_nukes"})
    with pytest.raises(ValueError, match="event kind"):
        event_from_json({"kind": "meteor_strike"})
