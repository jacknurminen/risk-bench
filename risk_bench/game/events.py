from dataclasses import dataclass

from risk_bench.game.cards import CardDesign
from risk_bench.game.combat import BattleOutcome
from risk_bench.game.types import PlayerId, Territory


@dataclass(frozen=True)
class TurnStarted:
    """A new turn began for the given player."""

    player_id: PlayerId
    turn_number: int


@dataclass(frozen=True)
class ArmiesPlaced:
    """A player placed reinforcement armies on a territory."""

    player_id: PlayerId
    territory: Territory
    count: int


@dataclass(frozen=True)
class CardsTraded:
    """A player traded in a card set; only designs are public, not territories."""

    player_id: PlayerId
    card_designs: tuple[CardDesign, CardDesign, CardDesign]
    bonus_armies: int


@dataclass(frozen=True)
class BattleResolved:
    """One dice battle was fought between two territories."""

    attacker: PlayerId
    defender: PlayerId
    from_territory: Territory
    to_territory: Territory
    outcome: BattleOutcome


@dataclass(frozen=True)
class TerritoryConquered:
    """An attack removed the last defender and the territory changed owner."""

    player_id: PlayerId
    territory: Territory
    from_territory: Territory


@dataclass(frozen=True)
class Fortified:
    """A player moved armies between owned territories at the end of their turn."""

    player_id: PlayerId
    from_territory: Territory
    to_territory: Territory
    count: int


@dataclass(frozen=True)
class CardDrawn:
    """A player earned a territory card; the card itself stays private."""

    player_id: PlayerId


@dataclass(frozen=True)
class PlayerEliminated:
    """A player lost their last territory; their cards pass to the eliminator."""

    eliminated_player: PlayerId
    eliminated_by: PlayerId


@dataclass(frozen=True)
class MissionCompleted:
    """A player completed their secret mission and won; the mission is revealed."""

    player_id: PlayerId
    mission_description: str


@dataclass(frozen=True)
class TurnLimitReached:
    """The game hit the turn limit without a completed mission; it is a draw."""

    turn_number: int


type GameEvent = (
    TurnStarted
    | ArmiesPlaced
    | CardsTraded
    | BattleResolved
    | TerritoryConquered
    | Fortified
    | CardDrawn
    | PlayerEliminated
    | MissionCompleted
    | TurnLimitReached
)
