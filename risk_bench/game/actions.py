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
    """Roll dice against an adjacent enemy territory, optionally repeating until it falls.

    ``max_attacks`` is how many times to keep rolling against the target,
    stopping early on capture or when the source can no longer attack.
    ``armies_to_leave_behind`` is how many armies stay in the source if the
    target is captured; the rest advance into it (clamped so at least one army
    stays and at least one advances).
    """

    from_territory: Territory
    to_territory: Territory
    attacker_dice: int
    max_attacks: int
    armies_to_leave_behind: int


@dataclass(frozen=True)
class Fortify:
    """Move armies between two adjacent owned territories, ending the turn."""

    from_territory: Territory
    to_territory: Territory
    count: int


@dataclass(frozen=True)
class EndAttackPhase:
    """Stop attacking and proceed to the fortify phase."""


@dataclass(frozen=True)
class SkipFortify:
    """End the turn without fortifying."""


type Action = PlaceArmies | TradeCards | Attack | Fortify | EndAttackPhase | SkipFortify
