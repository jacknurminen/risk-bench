"""Territory cards, card sets, and secret mission cards."""

from __future__ import annotations

import random

from game.board import BOARD

# ---------------------------------------------------------------------------
# Territory card types
# ---------------------------------------------------------------------------
CARD_TYPES = ["infantry", "cavalry", "artillery"]
WILDCARD_PREFIX = "wildcard"

# Escalating card-set bonus schedule: 4, 6, 8, 10, 12, 15, 20, 25, ...
_SET_BONUSES = [4, 6, 8, 10, 12, 15, 20, 25, 30, 35, 40, 45]


def card_set_bonus(set_count: int) -> int:
    """Return the troop bonus for the nth card set redeemed (0-indexed)."""
    if set_count < len(_SET_BONUSES):
        return _SET_BONUSES[set_count]
    # After the table runs out, continue incrementing by 5
    return _SET_BONUSES[-1] + (set_count - len(_SET_BONUSES) + 1) * 5


# ---------------------------------------------------------------------------
# Deck helpers
# ---------------------------------------------------------------------------
def build_deck() -> list[str]:
    """
    Build a shuffled deck of 44 cards:
    - 42 territory cards (one per territory), each assigned infantry/cavalry/artillery
    - 2 wildcards
    """
    types = CARD_TYPES * (42 // 3) + CARD_TYPES[: 42 % 3]  # cycle through types
    random.shuffle(types)
    deck = [f"{card_type}:{territory}" for card_type, territory in zip(types, BOARD.all_territory_names())]
    deck += [f"{WILDCARD_PREFIX}:1", f"{WILDCARD_PREFIX}:2"]
    random.shuffle(deck)
    return deck


def card_type(card: str) -> str:
    return card.split(":")[0]


def is_wildcard(card: str) -> bool:
    return card.startswith(WILDCARD_PREFIX)


def is_valid_set(cards: list[str]) -> bool:
    """
    A valid set is exactly 3 cards that are either:
    - All the same type (infantry/cavalry/artillery, wildcards count as any)
    - All different types (one of each, wildcards count as any)
    """
    if len(cards) != 3:
        return False
    types = [card_type(c) for c in cards]
    wildcards = sum(1 for t in types if t == WILDCARD_PREFIX)
    non_wild = [t for t in types if t != WILDCARD_PREFIX]

    if wildcards == 3:
        return True
    if wildcards == 2:
        return True  # 2 wildcards + anything = valid
    if wildcards == 1:
        # 1 wildcard + 2 others: valid if the 2 others are the same or different
        return len(set(non_wild)) in (1, 2)
    # no wildcards
    return len(set(non_wild)) == 1 or len(set(non_wild)) == 3


def territory_match_bonus(cards: list[str], owned_territories: list[str]) -> int:
    """Extra 2 troops if any card's territory is owned by the player."""
    for card in cards:
        if not is_wildcard(card):
            terr = card.split(":")[1]
            if terr in owned_territories:
                return 2
    return 0


# ---------------------------------------------------------------------------
# Secret Mission cards (10 classic missions)
# ---------------------------------------------------------------------------
MISSIONS: list[str] = [
    "Conquer Asia and South America",
    "Conquer Asia and Africa",
    "Conquer North America and Africa",
    "Conquer North America and Australia",
    "Conquer Europe and South America and one other continent",
    "Conquer Europe and Australia and one other continent",
    "Conquer 24 territories of your choice",
    "Destroy the {target} army (or conquer 24 territories if you are {target})",
    "Conquer 18 territories and occupy each with at least 2 troops",
    "Conquer South America and Australia",
]

_PLAYER_KILL_MISSIONS = [7]  # indices with {target} placeholder


def assign_missions(players: list[str]) -> dict[str, str]:
    """Assign one mission per player. Handles the kill-a-player special case."""
    shuffled = list(MISSIONS)
    random.shuffle(shuffled)
    result: dict[str, str] = {}

    for i, player in enumerate(players):
        mission = shuffled[i % len(shuffled)]
        if "{target}" in mission:
            # Pick a different player as the target
            others = [p for p in players if p != player]
            target = random.choice(others) if others else player
            mission = mission.format(target=target)
        result[player] = mission

    return result


# ---------------------------------------------------------------------------
# Mission completion checks
# ---------------------------------------------------------------------------
def check_mission(player: str, mission: str, state: object) -> bool:
    """Return True if the player has completed their mission."""
    from game.board import BOARD
    from game.state import GameState

    assert isinstance(state, GameState)

    owned = state.territories_of(player)
    owner_map = {n: ts.owner for n, ts in state.territories.items()}
    cont_ctrl = BOARD.continent_controller(owner_map)

    mission_lower = mission.lower()

    if "asia and south america" in mission_lower:
        return cont_ctrl.get("asia") == player and cont_ctrl.get("south_america") == player
    if "asia and africa" in mission_lower:
        return cont_ctrl.get("asia") == player and cont_ctrl.get("africa") == player
    if "north america and africa" in mission_lower:
        return cont_ctrl.get("north_america") == player and cont_ctrl.get("africa") == player
    if "north america and australia" in mission_lower:
        return cont_ctrl.get("north_america") == player and cont_ctrl.get("oceania") == player
    if "europe and south america" in mission_lower:
        extras = [c for c, ctrl in cont_ctrl.items() if ctrl == player and c not in ("europe", "south_america")]
        return cont_ctrl.get("europe") == player and cont_ctrl.get("south_america") == player and len(extras) >= 1
    if "europe and australia" in mission_lower:
        extras = [c for c, ctrl in cont_ctrl.items() if ctrl == player and c not in ("europe", "oceania")]
        return cont_ctrl.get("europe") == player and cont_ctrl.get("oceania") == player and len(extras) >= 1
    if "24 territories of your choice" in mission_lower:
        return len(owned) >= 24
    if "destroy the" in mission_lower and "army" in mission_lower:
        # Parse target color: "destroy the red army"
        words = mission_lower.split()
        idx = words.index("the")
        target = words[idx + 1]
        target_alive = any(ts.owner == target for ts in state.territories.values())
        if not target_alive or target == player:
            return len(owned) >= 24
        return not target_alive
    if "18 territories" in mission_lower and "2 troops" in mission_lower:
        qualifying = [t for t in owned if state.territories[t].troops >= 2]
        return len(qualifying) >= 18
    if "south america and australia" in mission_lower:
        return cont_ctrl.get("south_america") == player and cont_ctrl.get("oceania") == player

    # Fallback: never complete
    return False
