from __future__ import annotations

import random
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Final

from risk_bench.game.types import Continent, PlayerId

if TYPE_CHECKING:
    from risk_bench.game.state import GameState


class MissionKind(StrEnum):
    CONQUER_TWO_CONTINENTS = "conquer_two_continents"
    CONQUER_TWO_CONTINENTS_PLUS_ONE = "conquer_two_continents_plus_one"
    CONQUER_24_TERRITORIES = "conquer_24_territories"
    CONQUER_18_TERRITORIES_TWO_ARMIES = "conquer_18_territories_two_armies"
    ELIMINATE_PLAYER = "eliminate_player"


@dataclass(frozen=True)
class Mission:
    """A secret mission card: completing it wins the game.

    ``target_continents`` is empty and ``target_player`` is ``None`` when not
    applicable to the mission kind.
    """

    kind: MissionKind
    description: str
    target_continents: frozenset[Continent]
    target_player: PlayerId | None


def mission_deck(player_ids: list[PlayerId]) -> tuple[Mission, ...]:
    """The deck every mission is dealt from: the conquest cards plus one per player.

    Which card a player drew is secret, but the deck itself is not — it is printed in the
    rules of the physical game, so every player may be shown it and reason over it.

    Args:
        player_ids: The players in the game, in turn order.

    Returns:
        The deck in its canonical (unshuffled) order.
    """
    return (*_CONQUEST_MISSIONS, *(_eliminate_mission(target_player=player_id) for player_id in player_ids))


def deal_missions(player_ids: list[PlayerId], rng: random.Random) -> dict[PlayerId, Mission]:
    """Deal one random mission to each player from the classic mission deck.

    The deck holds the eight conquest missions plus one eliminate-player mission
    per player in the game. A player may draw the mission to eliminate
    themselves; ``effective_mission`` resolves it to the conquer-24 fallback.

    Args:
        player_ids: The players in the game, in turn order.
        rng: Source of randomness for the deal.

    Returns:
        The mission dealt to each player.
    """
    shuffled = list(mission_deck(player_ids=player_ids))
    rng.shuffle(shuffled)
    return {player_id: mission for player_id, mission in zip(player_ids, shuffled)}


def is_mission_complete(mission: Mission, player_id: PlayerId, state: GameState) -> bool:
    """Check whether the player has completed their mission in the given state.

    The mission is resolved through ``effective_mission`` first, so the
    eliminate-player fallback is applied automatically.

    Args:
        mission: The mission as dealt.
        player_id: The player holding the mission.
        state: The current game state.

    Returns:
        True if the (effective) mission's win condition is satisfied.
    """
    resolved = effective_mission(mission=mission, player_id=player_id, state=state)
    match resolved.kind:
        case MissionKind.CONQUER_TWO_CONTINENTS:
            return resolved.target_continents <= state.continents_owned_by(player_id=player_id)
        case MissionKind.CONQUER_TWO_CONTINENTS_PLUS_ONE:
            owned_continents = state.continents_owned_by(player_id=player_id)
            return resolved.target_continents <= owned_continents and len(owned_continents) >= 3
        case MissionKind.CONQUER_24_TERRITORIES:
            return len(state.territories_owned_by(player_id=player_id)) >= 24
        case MissionKind.CONQUER_18_TERRITORIES_TWO_ARMIES:
            fortified_territories = [
                territory
                for territory in state.territories_owned_by(player_id=player_id)
                if state.armies[territory] >= 2
            ]
            return len(fortified_territories) >= 18
        case MissionKind.ELIMINATE_PLAYER:
            assert resolved.target_player is not None
            return state.players[resolved.target_player].eliminated_by == player_id


def effective_mission(mission: Mission, player_id: PlayerId, state: GameState) -> Mission:
    """Resolve an ELIMINATE_PLAYER mission to its fallback when the target is invalid.

    If the target is the holder themselves, or was eliminated by another player,
    the mission becomes "conquer 24 territories" per the official rules. All
    other missions are returned unchanged.

    Args:
        mission: The mission as dealt.
        player_id: The player holding the mission.
        state: The current game state.

    Returns:
        The mission whose condition actually applies right now.
    """
    if mission.kind is not MissionKind.ELIMINATE_PLAYER:
        return mission
    if mission.target_player == player_id:
        return _CONQUER_24_FALLBACK
    assert mission.target_player is not None
    eliminated_by = state.players[mission.target_player].eliminated_by
    if eliminated_by is not None and eliminated_by != player_id:
        return _CONQUER_24_FALLBACK
    return mission


def _continent_name(continent: Continent) -> str:
    return continent.value.replace("_", " ").title()


def _two_continents_mission(first: Continent, second: Continent) -> Mission:
    return Mission(
        kind=MissionKind.CONQUER_TWO_CONTINENTS,
        description=f"Conquer the whole of {_continent_name(first)} and {_continent_name(second)}.",
        target_continents=frozenset({first, second}),
        target_player=None,
    )


def _two_continents_plus_one_mission(first: Continent, second: Continent) -> Mission:
    return Mission(
        kind=MissionKind.CONQUER_TWO_CONTINENTS_PLUS_ONE,
        description=(
            f"Conquer the whole of {_continent_name(first)} and {_continent_name(second)}, "
            "plus a third continent of your choice."
        ),
        target_continents=frozenset({first, second}),
        target_player=None,
    )


def _eliminate_mission(target_player: PlayerId) -> Mission:
    return Mission(
        kind=MissionKind.ELIMINATE_PLAYER,
        description=(
            f"Eliminate the player '{target_player}'. If that is you, or another player eliminates "
            "them, your mission becomes: conquer 24 territories."
        ),
        target_continents=frozenset(),
        target_player=target_player,
    )


_CONQUER_24_FALLBACK: Final[Mission] = Mission(
    kind=MissionKind.CONQUER_24_TERRITORIES,
    description="Conquer 24 territories of your choice.",
    target_continents=frozenset(),
    target_player=None,
)

_CONQUEST_MISSIONS: Final[tuple[Mission, ...]] = (
    _two_continents_mission(first=Continent.NORTH_AMERICA, second=Continent.AFRICA),
    _two_continents_mission(first=Continent.NORTH_AMERICA, second=Continent.OCEANIA),
    _two_continents_mission(first=Continent.ASIA, second=Continent.SOUTH_AMERICA),
    _two_continents_mission(first=Continent.ASIA, second=Continent.AFRICA),
    _two_continents_plus_one_mission(first=Continent.EUROPE, second=Continent.SOUTH_AMERICA),
    _two_continents_plus_one_mission(first=Continent.EUROPE, second=Continent.OCEANIA),
    _CONQUER_24_FALLBACK,
    Mission(
        kind=MissionKind.CONQUER_18_TERRITORIES_TWO_ARMIES,
        description="Conquer 18 territories of your choice and occupy each with at least two armies.",
        target_continents=frozenset(),
        target_player=None,
    ),
)
