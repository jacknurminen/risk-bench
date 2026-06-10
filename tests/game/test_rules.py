from risk_bench.game.actions import Attack, EndAttackPhase, Fortify, PlaceArmies, SkipFortify, TradeCards
from risk_bench.game.board import BOARD
from risk_bench.game.cards import CardDesign, TerritoryCard
from risk_bench.game.rules import legal_actions, must_trade_cards, reinforcement_count, validate_action
from risk_bench.game.state import GameState
from risk_bench.game.types import Continent, Phase, PlayerId, Territory
from tests.game.builders import PLACEHOLDER_MISSION, make_state, split_board_between

ALICE: PlayerId = "alice"
BOB: PlayerId = "bob"


def alaska_corner_state(alaska_armies: int) -> GameState:
    """Alice owns Alaska and Alberta; Bob owns the rest of the board with 1 army each."""
    owner = split_board_between(
        first_player=ALICE,
        first_player_territories=frozenset({Territory.ALASKA, Territory.ALBERTA}),
        second_player=BOB,
    )
    armies = {territory: 1 for territory in Territory}
    armies[Territory.ALASKA] = alaska_armies
    return make_state(
        owner=owner,
        armies=armies,
        missions={ALICE: PLACEHOLDER_MISSION, BOB: PLACEHOLDER_MISSION},
        eliminated_by={ALICE: None, BOB: None},
    )


def test_reinforcement_count_is_territories_over_three_with_a_minimum_of_three():
    state = alaska_corner_state(alaska_armies=1)
    assert reinforcement_count(state=state, player_id=ALICE) == 3  # 2 territories -> minimum

    forty_territories = frozenset(Territory) - {Territory.JAPAN, Territory.SIAM}
    owner = split_board_between(first_player=ALICE, first_player_territories=forty_territories, second_player=BOB)
    state = make_state(
        owner=owner,
        armies={territory: 1 for territory in Territory},
        missions={ALICE: PLACEHOLDER_MISSION, BOB: PLACEHOLDER_MISSION},
        eliminated_by={ALICE: None, BOB: None},
    )
    # 40 // 3 = 13, plus all continents except Asia: 5 + 2 + 5 + 3 + 2
    assert reinforcement_count(state=state, player_id=ALICE) == 13 + 17


def test_reinforcement_count_includes_continent_bonuses():
    south_america = BOARD.continent_info(Continent.SOUTH_AMERICA).territories
    owner = split_board_between(first_player=ALICE, first_player_territories=south_america, second_player=BOB)
    state = make_state(
        owner=owner,
        armies={territory: 1 for territory in Territory},
        missions={ALICE: PLACEHOLDER_MISSION, BOB: PLACEHOLDER_MISSION},
        eliminated_by={ALICE: None, BOB: None},
    )
    assert reinforcement_count(state=state, player_id=ALICE) == 3 + 2


def test_must_trade_cards_at_five_or_more():
    state = alaska_corner_state(alaska_armies=1)
    state.players[ALICE].cards = [
        TerritoryCard(design=CardDesign.INFANTRY, territory=territory)
        for territory in (Territory.PERU, Territory.JAPAN, Territory.EGYPT, Territory.URAL)
    ]
    assert not must_trade_cards(state=state, player_id=ALICE)

    state.players[ALICE].cards.append(TerritoryCard(design=CardDesign.WILD, territory=None))
    assert must_trade_cards(state=state, player_id=ALICE)


def test_reinforce_offers_placements_on_every_owned_territory():
    state = alaska_corner_state(alaska_armies=1)
    state.armies_to_place = 3

    actions = legal_actions(state=state)

    assert actions == [
        PlaceArmies(territory=Territory.ALASKA, count=3),
        PlaceArmies(territory=Territory.ALBERTA, count=3),
    ]


def test_forced_trade_offers_only_card_trades():
    state = alaska_corner_state(alaska_armies=1)
    state.armies_to_place = 3
    state.players[ALICE].cards = [
        TerritoryCard(design=CardDesign.INFANTRY, territory=territory)
        for territory in (Territory.PERU, Territory.JAPAN, Territory.EGYPT, Territory.URAL, Territory.SIAM)
    ]

    actions = legal_actions(state=state)

    assert all(isinstance(action, TradeCards) for action in actions)
    assert len(actions) == 10  # every 3-of-5 combination of same-design cards
    rejection = validate_action(state=state, action=PlaceArmies(territory=Territory.ALASKA, count=1))
    assert rejection is not None and "must trade" in rejection


def test_attack_phase_offers_attacks_on_adjacent_enemies_only():
    state = alaska_corner_state(alaska_armies=4)
    state.phase = Phase.ATTACK

    actions = legal_actions(state=state)

    # Alaska (4 armies) can attack its enemy neighbors with up to 3 dice; Alberta has 1 army and cannot attack.
    # Alberta is Alaska's neighbor but owned by Alice, so it is not a target.
    assert actions == [
        Attack(from_territory=Territory.ALASKA, to_territory=Territory.KAMCHATKA, attacker_dice=3),
        Attack(from_territory=Territory.ALASKA, to_territory=Territory.NORTHWEST_TERRITORY, attacker_dice=3),
        EndAttackPhase(),
    ]


def test_fortify_phase_offers_moves_between_adjacent_owned_territories():
    state = alaska_corner_state(alaska_armies=4)
    state.phase = Phase.FORTIFY

    actions = legal_actions(state=state)

    assert actions == [
        Fortify(from_territory=Territory.ALASKA, to_territory=Territory.ALBERTA, count=3),
        SkipFortify(),
    ]


def test_game_over_has_no_legal_actions():
    state = alaska_corner_state(alaska_armies=4)
    state.phase = Phase.GAME_OVER
    assert legal_actions(state=state) == []


def test_validate_rejects_attacking_own_territory():
    state = alaska_corner_state(alaska_armies=4)
    state.phase = Phase.ATTACK
    action = Attack(from_territory=Territory.ALASKA, to_territory=Territory.ALBERTA, attacker_dice=3)
    assert validate_action(state=state, action=action) is not None


def test_validate_rejects_attacking_non_adjacent_territory():
    state = alaska_corner_state(alaska_armies=4)
    state.phase = Phase.ATTACK
    action = Attack(from_territory=Territory.ALASKA, to_territory=Territory.JAPAN, attacker_dice=3)
    assert validate_action(state=state, action=action) is not None


def test_validate_rejects_attacking_with_too_many_dice():
    state = alaska_corner_state(alaska_armies=3)
    state.phase = Phase.ATTACK
    action = Attack(from_territory=Territory.ALASKA, to_territory=Territory.KAMCHATKA, attacker_dice=3)
    rejection = validate_action(state=state, action=action)
    assert rejection is not None and "between 1 and 2" in rejection


def test_validate_rejects_placing_on_enemy_territory():
    state = alaska_corner_state(alaska_armies=1)
    state.armies_to_place = 3
    action = PlaceArmies(territory=Territory.JAPAN, count=1)
    assert validate_action(state=state, action=action) is not None


def test_validate_rejects_placing_more_armies_than_available():
    state = alaska_corner_state(alaska_armies=1)
    state.armies_to_place = 3
    action = PlaceArmies(territory=Territory.ALASKA, count=4)
    assert validate_action(state=state, action=action) is not None


def test_validate_rejects_trading_cards_not_in_hand():
    state = alaska_corner_state(alaska_armies=1)
    state.armies_to_place = 3
    not_in_hand = (
        TerritoryCard(design=CardDesign.INFANTRY, territory=Territory.PERU),
        TerritoryCard(design=CardDesign.INFANTRY, territory=Territory.JAPAN),
        TerritoryCard(design=CardDesign.INFANTRY, territory=Territory.EGYPT),
    )
    assert validate_action(state=state, action=TradeCards(cards=not_in_hand)) is not None


def test_validate_rejects_actions_in_the_wrong_phase():
    state = alaska_corner_state(alaska_armies=4)
    state.phase = Phase.ATTACK
    assert validate_action(state=state, action=SkipFortify()) is not None
    assert validate_action(state=state, action=PlaceArmies(territory=Territory.ALASKA, count=1)) is not None
    state.phase = Phase.FORTIFY
    assert validate_action(state=state, action=EndAttackPhase()) is not None
