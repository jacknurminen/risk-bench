from __future__ import annotations

from typing import Any

from game.board import BOARD
from game.cards import is_valid_set
from game.state import GameState, Phase


class InvalidMove(Exception):
    pass


def validate_move(state: GameState, move: dict[str, Any]) -> None:
    """Raise InvalidMove if the move is not legal in the current state.

    Args:
        state: Current game state.
        move: Move dict to validate.

    Raises:
        InvalidMove: If the move violates any game rule for the current phase.
    """
    phase = state.phase
    action = move.get("action")
    player = state.current_player

    if phase == Phase.REINFORCE:
        _validate_reinforce(state=state, move=move, player=player)
    elif phase == Phase.ATTACK:
        if action == "end_attacks":
            return
        _validate_attack(state=state, move=move, player=player)
    elif phase == Phase.FORTIFY:
        if action == "skip":
            return
        _validate_fortify(state=state, move=move, player=player)
    elif phase == Phase.SETUP:
        _validate_setup(state=state, move=move, player=player)
    else:
        raise InvalidMove(f"Cannot make move in phase {phase}")


def validate_card_redeem(state: GameState, cards: list[str]) -> None:
    """Validate a card redemption during the reinforce phase.

    Args:
        state: Current game state.
        cards: List of exactly 3 card strings to redeem.

    Raises:
        InvalidMove: If the cards are not a valid set or not in the player's hand.
    """
    player = state.current_player
    hand = state.hands.get(player, [])
    if not is_valid_set(cards=cards):
        raise InvalidMove(f"card redeem: {cards} is not a valid set")
    for card in cards:
        if card not in hand:
            raise InvalidMove(f"card redeem: {card!r} not in hand")


# ---------------------------------------------------------------------------
def _validate_reinforce(state: GameState, move: dict[str, Any], player: str) -> None:
    territory = move.get("territory")
    count = move.get("count", 0)

    if not territory:
        raise InvalidMove("reinforce: missing territory")
    if territory not in state.territories:
        raise InvalidMove(f"reinforce: unknown territory {territory!r}")
    if state.territories[territory].owner != player:
        raise InvalidMove(f"reinforce: {territory!r} not owned by {player}")
    if not isinstance(count, int) or count < 1:
        raise InvalidMove(f"reinforce: count must be >= 1, got {count}")
    if count > state.troops_to_place:
        raise InvalidMove(f"reinforce: not enough troops (have {state.troops_to_place}, want {count})")


def _validate_attack(state: GameState, move: dict[str, Any], player: str) -> None:
    from_territory = move.get("from")
    to_territory = move.get("to")
    attackers = move.get("attackers", 0)

    if not from_territory or not to_territory:
        raise InvalidMove("attack: missing from/to")
    if from_territory not in state.territories:
        raise InvalidMove(f"attack: unknown territory {from_territory!r}")
    if to_territory not in state.territories:
        raise InvalidMove(f"attack: unknown territory {to_territory!r}")
    if state.territories[from_territory].owner != player:
        raise InvalidMove(f"attack: {from_territory!r} not owned by {player}")
    if state.territories[to_territory].owner == player:
        raise InvalidMove(f"attack: cannot attack own territory {to_territory!r}")
    if not BOARD.are_adjacent(territory=from_territory, other_territory=to_territory):
        raise InvalidMove(f"attack: {from_territory!r} not adjacent to {to_territory!r}")
    if not isinstance(attackers, int) or attackers < 1 or attackers > 3:
        raise InvalidMove(f"attack: attackers must be 1-3, got {attackers}")
    from_troops = state.territories[from_territory].troops
    if attackers >= from_troops:
        raise InvalidMove(
            f"attack: must keep 1 troop in {from_territory!r} (have {from_troops}, attacking with {attackers})"
        )


def _validate_fortify(state: GameState, move: dict[str, Any], player: str) -> None:
    from_territory = move.get("from")
    to_territory = move.get("to")
    count = move.get("count", 0)

    if not from_territory or not to_territory:
        raise InvalidMove("fortify: missing from/to")
    if from_territory not in state.territories:
        raise InvalidMove(f"fortify: unknown territory {from_territory!r}")
    if to_territory not in state.territories:
        raise InvalidMove(f"fortify: unknown territory {to_territory!r}")
    if state.territories[from_territory].owner != player:
        raise InvalidMove(f"fortify: {from_territory!r} not owned by {player}")
    if state.territories[to_territory].owner != player:
        raise InvalidMove(f"fortify: {to_territory!r} not owned by {player}")
    if not isinstance(count, int) or count < 1:
        raise InvalidMove(f"fortify: count must be >= 1, got {count}")
    from_troops = state.territories[from_territory].troops
    if count >= from_troops:
        raise InvalidMove(f"fortify: must keep at least 1 troop in {from_territory!r}")
    if not _connected_through_owned(start=from_territory, end=to_territory, player=player, state=state):
        raise InvalidMove(f"fortify: {from_territory!r} not connected to {to_territory!r} through owned territories")


def _validate_setup(state: GameState, move: dict[str, Any], player: str) -> None:
    territory = move.get("territory")
    if not territory:
        raise InvalidMove("setup: missing territory")
    if territory not in state.territories:
        raise InvalidMove(f"setup: unknown territory {territory!r}")
    territory_state = state.territories[territory]
    if territory_state.owner not in (player, ""):
        raise InvalidMove(f"setup: territory {territory!r} already owned by {territory_state.owner}")
    if state.troops_to_place < 1:
        raise InvalidMove("setup: no troops to place")


def _connected_through_owned(start: str, end: str, player: str, state: GameState) -> bool:
    if start == end:
        return True
    visited = {start}
    queue = [start]
    while queue:
        current = queue.pop(0)
        for neighbor in BOARD.territories[current].adjacent:
            if neighbor in visited:
                continue
            if state.territories.get(neighbor, None) is None:
                continue
            if state.territories[neighbor].owner != player:
                continue
            if neighbor == end:
                return True
            visited.add(neighbor)
            queue.append(neighbor)
    return False
