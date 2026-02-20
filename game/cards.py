from __future__ import annotations

import random

from game.board import BOARD
from game.state import GameState

# ---------------------------------------------------------------------------
# Territory card types
# ---------------------------------------------------------------------------
CARD_TYPES = ["infantry", "cavalry", "artillery"]
WILDCARD_PREFIX = "wildcard"

# Escalating card-set bonus schedule: 4, 6, 8, 10, 12, 15, 20, 25, ...
_SET_BONUSES = [4, 6, 8, 10, 12, 15, 20, 25, 30, 35, 40, 45]


def card_set_bonus(set_count: int) -> int:
    """Return the troop bonus for the nth card set redeemed (0-indexed).

    Args:
        set_count: Number of card sets already redeemed this game (0-indexed).

    Returns:
        Number of bonus troops awarded for redeeming the next set.
    """
    if set_count < len(_SET_BONUSES):
        return _SET_BONUSES[set_count]
    # After the table runs out, continue incrementing by 5
    return _SET_BONUSES[-1] + (set_count - len(_SET_BONUSES) + 1) * 5


# ---------------------------------------------------------------------------
# Deck helpers
# ---------------------------------------------------------------------------
def build_deck() -> list[str]:
    """Build a shuffled deck of 44 cards.

    The deck contains 42 territory cards (one per territory, each assigned
    infantry/cavalry/artillery) and 2 wildcards.

    Returns:
        Shuffled list of card strings in the format "type:territory".
    """
    types = CARD_TYPES * (42 // 3) + CARD_TYPES[: 42 % 3]  # cycle through types
    random.shuffle(types)
    deck = [f"{card_type}:{territory}" for card_type, territory in zip(types, BOARD.all_territory_names())]
    deck += [f"{WILDCARD_PREFIX}:1", f"{WILDCARD_PREFIX}:2"]
    random.shuffle(deck)
    return deck


def card_type(card: str) -> str:
    """Return the type portion of a card string (e.g. 'infantry' from 'infantry:alaska').

    Args:
        card: Card string in the format "type:territory".

    Returns:
        The card type string.
    """
    return card.split(":")[0]


def is_wildcard(card: str) -> bool:
    """Return True if the card is a wildcard.

    Args:
        card: Card string to check.

    Returns:
        True if the card starts with the wildcard prefix.
    """
    return card.startswith(WILDCARD_PREFIX)


def is_valid_set(cards: list[str]) -> bool:
    """Return True if the three cards form a valid set for redemption.

    A valid set is exactly 3 cards that are either all the same type
    (infantry/cavalry/artillery) or all different types. Wildcards count as any type.

    Args:
        cards: List of exactly 3 card strings.

    Returns:
        True if the cards form a valid redeemable set.
    """
    if len(cards) != 3:
        return False
    types = [card_type(card) for card in cards]
    wildcards = sum(1 for card_type_value in types if card_type_value == WILDCARD_PREFIX)
    non_wild = [card_type_value for card_type_value in types if card_type_value != WILDCARD_PREFIX]

    if wildcards >= 2:
        return True  # 2+ wildcards with anything = valid
    if wildcards == 1:
        # 1 wildcard + 2 others: valid if the 2 others are the same or different
        return len(set(non_wild)) in (1, 2)
    # No wildcards: must be all-same or all-different
    return len(set(non_wild)) == 1 or len(set(non_wild)) == 3


def territory_match_bonus(cards: list[str], owned_territories: list[str]) -> int:
    """Return the bonus troops if any redeemed card's territory is owned by the player.

    Args:
        cards: The three cards being redeemed.
        owned_territories: List of territory names owned by the redeeming player.

    Returns:
        2 if any card matches an owned territory, 0 otherwise.
    """
    for card in cards:
        if not is_wildcard(card=card):
            territory = card.split(":")[1]
            if territory in owned_territories:
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
    """Assign one mission per player, handling the kill-a-player special case.

    Args:
        players: List of player color strings.

    Returns:
        Dict mapping player color to their secret mission string.
    """
    shuffled = list(MISSIONS)
    random.shuffle(shuffled)
    result: dict[str, str] = {}

    for i, player in enumerate(players):
        mission = shuffled[i % len(shuffled)]
        if "{target}" in mission:
            # Pick a different player as the target
            others = [other_player for other_player in players if other_player != player]
            target = random.choice(others) if others else player
            mission = mission.format(target=target)
        result[player] = mission

    return result


# ---------------------------------------------------------------------------
# Mission completion checks
# ---------------------------------------------------------------------------
def check_mission(player: str, mission: str, state: GameState) -> bool:
    """Return True if the player has completed their secret mission.

    Args:
        player: Color of the player to check.
        mission: The player's mission string.
        state: Current game state.

    Returns:
        True if the mission conditions are satisfied.
    """
    owner_map = {name: territory_state.owner for name, territory_state in state.territories.items()}
    continent_control = BOARD.continent_controller(owner_map=owner_map)

    owned = state.territories_of(player=player)
    mission_lower = mission.lower()

    if "asia and south america" in mission_lower:
        return continent_control.get("asia") == player and continent_control.get("south_america") == player
    if "asia and africa" in mission_lower:
        return continent_control.get("asia") == player and continent_control.get("africa") == player
    if "north america and africa" in mission_lower:
        return continent_control.get("north_america") == player and continent_control.get("africa") == player
    if "north america and australia" in mission_lower:
        return continent_control.get("north_america") == player and continent_control.get("oceania") == player
    if "europe and south america" in mission_lower:
        extras = [
            c
            for c, controller in continent_control.items()
            if controller == player and c not in ("europe", "south_america")
        ]
        return (
            continent_control.get("europe") == player
            and continent_control.get("south_america") == player
            and len(extras) >= 1
        )
    if "europe and australia" in mission_lower:
        extras = [
            c for c, controller in continent_control.items() if controller == player and c not in ("europe", "oceania")
        ]
        return (
            continent_control.get("europe") == player
            and continent_control.get("oceania") == player
            and len(extras) >= 1
        )
    if "24 territories of your choice" in mission_lower:
        return len(owned) >= 24
    if "destroy the" in mission_lower and "army" in mission_lower:
        # Parse target color from e.g. "destroy the red army"
        words = mission_lower.split()
        target = words[words.index("the") + 1]
        target_alive = any(territory_state.owner == target for territory_state in state.territories.values())
        if not target_alive or target == player:
            return len(owned) >= 24
        return not target_alive
    if "18 territories" in mission_lower and "2 troops" in mission_lower:
        qualifying = [territory for territory in owned if state.territories[territory].troops >= 2]
        return len(qualifying) >= 18
    if "south america and australia" in mission_lower:
        return continent_control.get("south_america") == player and continent_control.get("oceania") == player

    # Fallback: never complete
    return False
