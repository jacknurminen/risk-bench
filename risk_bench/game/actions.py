from dataclasses import dataclass

from risk_bench.game.cards import CardSet
from risk_bench.game.types import Territory


@dataclass(frozen=True)
class PlaceArmies:
    """Place armies on an owned territory (setup and reinforce phases)."""

    territory: Territory
    count: int


@dataclass(frozen=True)
class TradeCards:
    """Trade in a set of three territory cards for bonus armies."""

    cards: CardSet


@dataclass(frozen=True)
class Attack:
    """Roll dice against an adjacent enemy territory."""

    from_territory: Territory
    to_territory: Territory
    attacker_dice: int


@dataclass(frozen=True)
class Occupy:
    """Move armies into a just-conquered territory."""

    armies_to_move: int


@dataclass(frozen=True)
class Fortify:
    """Move armies between two connected owned territories, ending the turn."""

    from_territory: Territory
    to_territory: Territory
    count: int


@dataclass(frozen=True)
class EndAttackPhase:
    """Stop attacking and proceed to the fortify phase."""


@dataclass(frozen=True)
class SkipFortify:
    """End the turn without fortifying."""


type Action = PlaceArmies | TradeCards | Attack | Occupy | Fortify | EndAttackPhase | SkipFortify
