from __future__ import annotations

from game.cards import is_valid_set
from game.state import GameState


class InvalidMove(Exception):
    pass


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
