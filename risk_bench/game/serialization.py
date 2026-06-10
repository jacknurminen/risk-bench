import dataclasses
from typing import Any, Final

from risk_bench.chat.diplomacy import ChatMessage, MessageVisibility
from risk_bench.game.actions import (
    Action,
    Attack,
    EndAttackPhase,
    Fortify,
    Occupy,
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
from risk_bench.game.types import PlayerId, Territory

_ACTION_KINDS: Final[dict[type, str]] = {
    PlaceArmies: "place_armies",
    TradeCards: "trade_cards",
    Attack: "attack",
    Occupy: "occupy",
    Fortify: "fortify",
    EndAttackPhase: "end_attack_phase",
    SkipFortify: "skip_fortify",
}

_EVENT_KINDS: Final[dict[type, str]] = {
    TurnStarted: "turn_started",
    ArmiesPlaced: "armies_placed",
    CardsTraded: "cards_traded",
    BattleResolved: "battle_resolved",
    TerritoryConquered: "territory_conquered",
    Fortified: "fortified",
    CardDrawn: "card_drawn",
    PlayerEliminated: "player_eliminated",
    MissionCompleted: "mission_completed",
    TurnLimitReached: "turn_limit_reached",
}


def action_to_json(action: Action) -> dict[str, Any]:
    """Serialize an action to a JSON-compatible dict with a stable 'kind' discriminator.

    Args:
        action: The action to serialize.

    Returns:
        A dict that round-trips through ``action_from_json``.
    """
    return {"kind": _ACTION_KINDS[type(action)], **dataclasses.asdict(action)}


def action_from_json(data: dict[str, Any]) -> Action:
    """Reconstruct an action from its JSON form.

    Args:
        data: A dict produced by ``action_to_json``.

    Returns:
        The reconstructed action.

    Raises:
        ValueError: If the 'kind' discriminator is unknown.
    """
    match data["kind"]:
        case "place_armies":
            return PlaceArmies(territory=Territory(data["territory"]), count=int(data["count"]))
        case "trade_cards":
            first_card, second_card, third_card = (_card_from_json(data=card) for card in data["cards"])
            return TradeCards(cards=(first_card, second_card, third_card))
        case "attack":
            return Attack(
                from_territory=Territory(data["from_territory"]),
                to_territory=Territory(data["to_territory"]),
                attacker_dice=int(data["attacker_dice"]),
            )
        case "occupy":
            return Occupy(armies_to_move=int(data["armies_to_move"]))
        case "fortify":
            return Fortify(
                from_territory=Territory(data["from_territory"]),
                to_territory=Territory(data["to_territory"]),
                count=int(data["count"]),
            )
        case "end_attack_phase":
            return EndAttackPhase()
        case "skip_fortify":
            return SkipFortify()
        case unknown_kind:
            raise ValueError(f"Unknown action kind: {unknown_kind!r}")


def event_to_json(event: GameEvent) -> dict[str, Any]:
    """Serialize a game event to a JSON-compatible dict with a stable 'kind' discriminator.

    Args:
        event: The event to serialize.

    Returns:
        A dict that round-trips through ``event_from_json``.
    """
    return {"kind": _EVENT_KINDS[type(event)], **dataclasses.asdict(event)}


def event_from_json(data: dict[str, Any]) -> GameEvent:
    """Reconstruct a game event from its JSON form.

    Args:
        data: A dict produced by ``event_to_json``.

    Returns:
        The reconstructed event.

    Raises:
        ValueError: If the 'kind' discriminator is unknown.
    """
    match data["kind"]:
        case "turn_started":
            return TurnStarted(player_id=_player(data["player_id"]), turn_number=int(data["turn_number"]))
        case "armies_placed":
            return ArmiesPlaced(
                player_id=_player(data["player_id"]),
                territory=Territory(data["territory"]),
                count=int(data["count"]),
            )
        case "cards_traded":
            first_design, second_design, third_design = (CardDesign(design) for design in data["card_designs"])
            return CardsTraded(
                player_id=_player(data["player_id"]),
                card_designs=(first_design, second_design, third_design),
                bonus_armies=int(data["bonus_armies"]),
            )
        case "battle_resolved":
            return BattleResolved(
                attacker=_player(data["attacker"]),
                defender=_player(data["defender"]),
                from_territory=Territory(data["from_territory"]),
                to_territory=Territory(data["to_territory"]),
                outcome=_outcome_from_json(data=data["outcome"]),
            )
        case "territory_conquered":
            return TerritoryConquered(
                player_id=_player(data["player_id"]),
                territory=Territory(data["territory"]),
                from_territory=Territory(data["from_territory"]),
            )
        case "fortified":
            return Fortified(
                player_id=_player(data["player_id"]),
                from_territory=Territory(data["from_territory"]),
                to_territory=Territory(data["to_territory"]),
                count=int(data["count"]),
            )
        case "card_drawn":
            return CardDrawn(player_id=_player(data["player_id"]))
        case "player_eliminated":
            return PlayerEliminated(
                eliminated_player=_player(data["eliminated_player"]),
                eliminated_by=_player(data["eliminated_by"]),
            )
        case "mission_completed":
            return MissionCompleted(
                player_id=_player(data["player_id"]),
                mission_description=str(data["mission_description"]),
            )
        case "turn_limit_reached":
            return TurnLimitReached(turn_number=int(data["turn_number"]))
        case unknown_kind:
            raise ValueError(f"Unknown event kind: {unknown_kind!r}")


def chat_message_to_json(message: ChatMessage) -> dict[str, Any]:
    """Serialize a chat message to a JSON-compatible dict.

    Args:
        message: The message to serialize.

    Returns:
        A dict that round-trips through ``chat_message_from_json``.
    """
    return dataclasses.asdict(message)


def chat_message_from_json(data: dict[str, Any]) -> ChatMessage:
    """Reconstruct a chat message from its JSON form.

    Args:
        data: A dict produced by ``chat_message_to_json``.

    Returns:
        The reconstructed message.
    """
    recipient = data["recipient"]
    return ChatMessage(
        turn_number=int(data["turn_number"]),
        sender=_player(data["sender"]),
        visibility=MessageVisibility(data["visibility"]),
        recipient=_player(recipient) if recipient is not None else None,
        text=str(data["text"]),
    )


def _player(value: Any) -> PlayerId:
    return str(value)


def _card_from_json(data: dict[str, Any]) -> TerritoryCard:
    territory = data["territory"]
    return TerritoryCard(
        design=CardDesign(data["design"]),
        territory=Territory(territory) if territory is not None else None,
    )


def _outcome_from_json(data: dict[str, Any]) -> BattleOutcome:
    return BattleOutcome(
        attacker_dice=tuple(int(die) for die in data["attacker_dice"]),
        defender_dice=tuple(int(die) for die in data["defender_dice"]),
        attacker_losses=int(data["attacker_losses"]),
        defender_losses=int(data["defender_losses"]),
    )
