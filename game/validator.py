"""Move validation against the current GameState."""

from __future__ import annotations

from typing import Any

from game.board import BOARD
from game.cards import is_valid_set
from game.state import GameState, Phase


class InvalidMove(Exception):
    pass


def validate_move(state: GameState, move: dict[str, Any]) -> None:
    """Raise InvalidMove if the move is not legal in the current state."""
    phase = state.phase
    action = move.get("action")
    player = state.current_player

    if phase == Phase.REINFORCE:
        _validate_reinforce(state, move, player)
    elif phase == Phase.ATTACK:
        if action == "end_attacks":
            return
        _validate_attack(state, move, player)
    elif phase == Phase.FORTIFY:
        if action == "skip":
            return
        _validate_fortify(state, move, player)
    elif phase == Phase.SETUP:
        _validate_setup(state, move, player)
    else:
        raise InvalidMove(f"Cannot make move in phase {phase}")


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
    from_t = move.get("from")
    to_t = move.get("to")
    attackers = move.get("attackers", 0)

    if not from_t or not to_t:
        raise InvalidMove("attack: missing from/to")
    if from_t not in state.territories:
        raise InvalidMove(f"attack: unknown territory {from_t!r}")
    if to_t not in state.territories:
        raise InvalidMove(f"attack: unknown territory {to_t!r}")
    if state.territories[from_t].owner != player:
        raise InvalidMove(f"attack: {from_t!r} not owned by {player}")
    if state.territories[to_t].owner == player:
        raise InvalidMove(f"attack: cannot attack own territory {to_t!r}")
    if not BOARD.are_adjacent(from_t, to_t):
        raise InvalidMove(f"attack: {from_t!r} not adjacent to {to_t!r}")
    if not isinstance(attackers, int) or attackers < 1 or attackers > 3:
        raise InvalidMove(f"attack: attackers must be 1-3, got {attackers}")
    from_troops = state.territories[from_t].troops
    if attackers >= from_troops:
        raise InvalidMove(f"attack: must keep 1 troop in {from_t!r} (have {from_troops}, attacking with {attackers})")


def _validate_fortify(state: GameState, move: dict[str, Any], player: str) -> None:
    from_t = move.get("from")
    to_t = move.get("to")
    count = move.get("count", 0)

    if not from_t or not to_t:
        raise InvalidMove("fortify: missing from/to")
    if from_t not in state.territories:
        raise InvalidMove(f"fortify: unknown territory {from_t!r}")
    if to_t not in state.territories:
        raise InvalidMove(f"fortify: unknown territory {to_t!r}")
    if state.territories[from_t].owner != player:
        raise InvalidMove(f"fortify: {from_t!r} not owned by {player}")
    if state.territories[to_t].owner != player:
        raise InvalidMove(f"fortify: {to_t!r} not owned by {player}")
    if not isinstance(count, int) or count < 1:
        raise InvalidMove(f"fortify: count must be >= 1, got {count}")
    from_troops = state.territories[from_t].troops
    if count >= from_troops:
        raise InvalidMove(f"fortify: must keep at least 1 troop in {from_t!r}")
    # Must be connected through owned territories (simple: adjacent for now, engine can expand)
    if not _connected_through_owned(from_t, to_t, player, state):
        raise InvalidMove(f"fortify: {from_t!r} not connected to {to_t!r} through owned territories")


def _validate_setup(state: GameState, move: dict[str, Any], player: str) -> None:
    territory = move.get("territory")
    if not territory:
        raise InvalidMove("setup: missing territory")
    if territory not in state.territories:
        raise InvalidMove(f"setup: unknown territory {territory!r}")
    ts = state.territories[territory]
    if ts.owner not in (player, ""):
        raise InvalidMove(f"setup: territory {territory!r} already owned by {ts.owner}")
    if state.troops_to_place < 1:
        raise InvalidMove("setup: no troops to place")


def _connected_through_owned(start: str, end: str, player: str, state: GameState) -> bool:
    """BFS to check if start->end is connected through player-owned territories."""
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


def validate_card_redeem(state: GameState, cards: list[str]) -> None:
    """Validate a card redemption during the reinforce phase."""
    player = state.current_player
    hand = state.hands.get(player, [])
    if not is_valid_set(cards):
        raise InvalidMove(f"card redeem: {cards} is not a valid set")
    for card in cards:
        if card not in hand:
            raise InvalidMove(f"card redeem: {card!r} not in hand")
