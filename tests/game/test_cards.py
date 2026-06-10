import random
from collections import Counter

import pytest

from risk_bench.game.cards import (
    CardDesign,
    TerritoryCard,
    create_shuffled_deck,
    find_tradeable_sets,
    territories_awarding_extra_armies,
    trade_in_bonus,
)
from risk_bench.game.types import Territory

INFANTRY_CARD = TerritoryCard(design=CardDesign.INFANTRY, territory=Territory.ALASKA)
CAVALRY_CARD = TerritoryCard(design=CardDesign.CAVALRY, territory=Territory.BRAZIL)
ARTILLERY_CARD = TerritoryCard(design=CardDesign.ARTILLERY, territory=Territory.EGYPT)
WILD_CARD = TerritoryCard(design=CardDesign.WILD, territory=None)


def drawn_full_deck() -> list[TerritoryCard]:
    deck = create_shuffled_deck(rng=random.Random(7))
    return [deck.draw() for _ in range(44)]


def test_deck_has_44_cards_with_14_of_each_design_and_2_wilds():
    design_counts = Counter(card.design for card in drawn_full_deck())
    assert design_counts == {
        CardDesign.INFANTRY: 14,
        CardDesign.CAVALRY: 14,
        CardDesign.ARTILLERY: 14,
        CardDesign.WILD: 2,
    }


def test_deck_covers_every_territory_exactly_once():
    territories = [card.territory for card in drawn_full_deck() if card.territory is not None]
    assert len(territories) == 42
    assert frozenset(territories) == frozenset(Territory)


def test_deck_reshuffles_discard_pile_when_draw_pile_is_empty():
    deck = create_shuffled_deck(rng=random.Random(7))
    drawn = [deck.draw() for _ in range(44)]
    deck.discard(cards=(drawn[0], drawn[1], drawn[2]))

    redrawn = [deck.draw() for _ in range(3)]

    assert frozenset(redrawn) == frozenset(drawn[:3])
    with pytest.raises(IndexError):
        deck.draw()


def test_three_of_a_kind_is_tradeable():
    hand = (
        INFANTRY_CARD,
        TerritoryCard(design=CardDesign.INFANTRY, territory=Territory.PERU),
        TerritoryCard(design=CardDesign.INFANTRY, territory=Territory.JAPAN),
    )
    assert find_tradeable_sets(cards=hand) == [hand]


def test_one_of_each_is_tradeable():
    hand = (INFANTRY_CARD, CAVALRY_CARD, ARTILLERY_CARD)
    assert find_tradeable_sets(cards=hand) == [hand]


def test_two_of_a_kind_plus_one_other_is_not_tradeable():
    hand = (
        INFANTRY_CARD,
        TerritoryCard(design=CardDesign.INFANTRY, territory=Territory.PERU),
        CAVALRY_CARD,
    )
    assert find_tradeable_sets(cards=hand) == []


def test_any_set_containing_a_wild_is_tradeable():
    hand = (
        INFANTRY_CARD,
        TerritoryCard(design=CardDesign.INFANTRY, territory=Territory.PERU),
        CAVALRY_CARD,
        WILD_CARD,
    )
    tradeable_sets = find_tradeable_sets(cards=hand)
    assert all(WILD_CARD in card_set for card_set in tradeable_sets)
    assert len(tradeable_sets) == 3


def test_hand_smaller_than_three_has_no_sets():
    assert find_tradeable_sets(cards=(INFANTRY_CARD, CAVALRY_CARD)) == []


def test_trade_in_bonuses_by_composition():
    infantry_set = (
        INFANTRY_CARD,
        TerritoryCard(design=CardDesign.INFANTRY, territory=Territory.PERU),
        TerritoryCard(design=CardDesign.INFANTRY, territory=Territory.JAPAN),
    )
    cavalry_set = (
        CAVALRY_CARD,
        TerritoryCard(design=CardDesign.CAVALRY, territory=Territory.PERU),
        TerritoryCard(design=CardDesign.CAVALRY, territory=Territory.JAPAN),
    )
    artillery_set = (
        ARTILLERY_CARD,
        TerritoryCard(design=CardDesign.ARTILLERY, territory=Territory.PERU),
        TerritoryCard(design=CardDesign.ARTILLERY, territory=Territory.JAPAN),
    )
    one_of_each_set = (INFANTRY_CARD, CAVALRY_CARD, ARTILLERY_CARD)

    assert trade_in_bonus(card_set=infantry_set) == 4
    assert trade_in_bonus(card_set=cavalry_set) == 6
    assert trade_in_bonus(card_set=artillery_set) == 8
    assert trade_in_bonus(card_set=one_of_each_set) == 10


def test_wild_counts_as_the_most_valuable_design():
    two_infantry_and_wild = (
        INFANTRY_CARD,
        TerritoryCard(design=CardDesign.INFANTRY, territory=Territory.PERU),
        WILD_CARD,
    )
    infantry_cavalry_and_wild = (INFANTRY_CARD, CAVALRY_CARD, WILD_CARD)
    one_card_and_two_wilds = (INFANTRY_CARD, WILD_CARD, WILD_CARD)

    assert trade_in_bonus(card_set=two_infantry_and_wild) == 4
    assert trade_in_bonus(card_set=infantry_cavalry_and_wild) == 10
    assert trade_in_bonus(card_set=one_card_and_two_wilds) == 10


def test_trade_in_bonus_rejects_untradeable_set():
    untradeable = (
        INFANTRY_CARD,
        TerritoryCard(design=CardDesign.INFANTRY, territory=Territory.PERU),
        CAVALRY_CARD,
    )
    with pytest.raises(ValueError):
        trade_in_bonus(card_set=untradeable)


def test_owned_card_territories_award_extra_armies():
    card_set = (INFANTRY_CARD, CAVALRY_CARD, WILD_CARD)
    owned = frozenset({Territory.ALASKA, Territory.EGYPT})
    assert territories_awarding_extra_armies(card_set=card_set, owned_territories=owned) == [Territory.ALASKA]
