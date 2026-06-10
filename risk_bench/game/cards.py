import itertools
import random
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from risk_bench.game.types import Territory


class CardDesign(StrEnum):
    INFANTRY = "infantry"
    CAVALRY = "cavalry"
    ARTILLERY = "artillery"
    WILD = "wild"


@dataclass(frozen=True)
class TerritoryCard:
    """A territory card: a design plus a territory (``None`` for wild cards)."""

    design: CardDesign
    territory: Territory | None


type CardSet = tuple[TerritoryCard, TerritoryCard, TerritoryCard]


class Deck:
    """The territory card deck: a draw pile and a discard pile.

    When the draw pile is exhausted, the discard pile is reshuffled into it.
    """

    def __init__(self, draw_pile: list[TerritoryCard], rng: random.Random) -> None:
        self._draw_pile = draw_pile
        self._discard_pile: list[TerritoryCard] = []
        self._rng = rng

    def draw(self) -> TerritoryCard:
        """Draw the top card, reshuffling the discard pile into the draw pile if needed.

        Returns:
            The drawn card.

        Raises:
            IndexError: If both the draw pile and the discard pile are empty.
        """
        if not self._draw_pile:
            self._draw_pile = self._discard_pile
            self._discard_pile = []
            self._rng.shuffle(self._draw_pile)
        return self._draw_pile.pop()

    def discard(self, cards: CardSet) -> None:
        """Return a traded-in set to the discard pile.

        Args:
            cards: The three cards that were traded in.
        """
        self._discard_pile.extend(cards)


_DESIGN_BONUSES: Final[dict[CardDesign, int]] = {
    CardDesign.INFANTRY: 4,
    CardDesign.CAVALRY: 6,
    CardDesign.ARTILLERY: 8,
}
_ONE_OF_EACH_BONUS: Final[int] = 10
_WILD_CARD_COUNT: Final[int] = 2


def create_shuffled_deck(rng: random.Random) -> Deck:
    """Create the full classic deck (42 territory cards + 2 wilds), shuffled.

    Designs are assigned by cycling infantry/cavalry/artillery over the
    territories, giving 14 cards of each design.

    Args:
        rng: Source of randomness for the shuffle; the deck keeps it for reshuffles.

    Returns:
        A freshly shuffled deck.
    """
    designs = itertools.cycle([CardDesign.INFANTRY, CardDesign.CAVALRY, CardDesign.ARTILLERY])
    cards = [TerritoryCard(design=design, territory=territory) for territory, design in zip(Territory, designs)]
    cards.extend(TerritoryCard(design=CardDesign.WILD, territory=None) for _ in range(_WILD_CARD_COUNT))
    rng.shuffle(cards)
    return Deck(draw_pile=cards, rng=rng)


def find_tradeable_sets(cards: tuple[TerritoryCard, ...]) -> list[CardSet]:
    """Find every distinct set of three cards that may be traded in.

    A set is tradeable when the three designs are all the same or all different;
    wild cards substitute for any design.

    Args:
        cards: The cards in a player's hand.

    Returns:
        All tradeable three-card combinations, empty if none exist.
    """
    return [card_set for card_set in itertools.combinations(cards, 3) if is_tradeable_set(card_set=card_set)]


def is_tradeable_set(card_set: CardSet) -> bool:
    """Check whether three cards form a valid trade-in set.

    Args:
        card_set: The three cards to check.

    Returns:
        True if the designs are all the same or all different, with wilds
        substituting for any design.
    """
    non_wild_designs = [card.design for card in card_set if card.design is not CardDesign.WILD]
    if len(non_wild_designs) < 3:
        return True
    distinct_count = len(set(non_wild_designs))
    return distinct_count == 1 or distinct_count == 3


def trade_in_bonus(card_set: CardSet) -> int:
    """Return the army bonus for trading in the given set.

    Fixed values by composition: three infantry = 4, three cavalry = 6,
    three artillery = 8, one of each = 10. Wild cards count as the design
    that yields the highest bonus for the set.

    Args:
        card_set: A tradeable three-card set.

    Returns:
        The number of bonus armies awarded.

    Raises:
        ValueError: If the set is not tradeable.
    """
    non_wild_designs = [card.design for card in card_set if card.design is not CardDesign.WILD]
    distinct_designs = set(non_wild_designs)

    candidate_bonuses = []
    if len(distinct_designs) == 1:
        candidate_bonuses.append(_DESIGN_BONUSES[next(iter(distinct_designs))])
    if len(distinct_designs) == 0:
        candidate_bonuses.append(max(_DESIGN_BONUSES.values()))
    if len(distinct_designs) == len(non_wild_designs):
        candidate_bonuses.append(_ONE_OF_EACH_BONUS)

    if not candidate_bonuses:
        raise ValueError(f"Not a tradeable set: {card_set}")
    return max(candidate_bonuses)


def territories_awarding_extra_armies(card_set: CardSet, owned_territories: frozenset[Territory]) -> list[Territory]:
    """Return the traded cards' territories that the player owns.

    Each match awards two extra armies that must be placed on that territory.

    Args:
        card_set: The set being traded in.
        owned_territories: Territories currently owned by the trading player.

    Returns:
        The owned territories pictured on the traded cards.
    """
    return [card.territory for card in card_set if card.territory is not None and card.territory in owned_territories]
