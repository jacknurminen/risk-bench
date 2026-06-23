import random

import pytest

from risk_bench.game.actions import Attack, PlaceArmies, SkipFortify, TradeCards
from risk_bench.game.board import BOARD
from risk_bench.game.cards import CardDesign, TerritoryCard
from risk_bench.game.engine import IllegalActionError, apply_action, setup_game
from risk_bench.game.events import (
    BattleResolved,
    CardDrawn,
    MissionCompleted,
    PlayerEliminated,
    TerritoryConquered,
    TurnStarted,
)
from risk_bench.game.rules import legal_actions, reinforcement_count
from risk_bench.game.state import GameState
from risk_bench.game.types import Continent, Phase, PlayerId, Territory
from tests.game.builders import PLACEHOLDER_MISSION, make_state, split_board_between
from tests.game.test_combat import ScriptedRandom

ALICE: PlayerId = "alice"
BOB: PlayerId = "bob"
CARLA: PlayerId = "carla"

CONQUERING_ROLLS = [6, 6, 6, 1]  # three attacker dice beat one defender die
LOSING_ROLLS = [1, 1, 1, 6]


def alaska_attack_state(alaska_armies: int) -> GameState:
    """Alice owns Alaska, Carla owns Northwest Territory, Bob owns the rest; attack phase."""
    owner = split_board_between(
        first_player=ALICE, first_player_territories=frozenset({Territory.ALASKA}), second_player=BOB
    )
    owner[Territory.NORTHWEST_TERRITORY] = CARLA
    armies = {territory: 1 for territory in Territory}
    armies[Territory.ALASKA] = alaska_armies
    state = make_state(
        owner=owner,
        armies=armies,
        missions={ALICE: PLACEHOLDER_MISSION, BOB: PLACEHOLDER_MISSION, CARLA: PLACEHOLDER_MISSION},
        eliminated_by={ALICE: None, BOB: None, CARLA: None},
    )
    state.phase = Phase.ATTACK
    return state


ATTACK_NORTHWEST = Attack(
    from_territory=Territory.ALASKA,
    to_territory=Territory.NORTHWEST_TERRITORY,
    attacker_dice=3,
    max_attacks=1,
    armies_to_leave_behind=1,
)


def test_setup_deals_all_territories_evenly_with_one_army_each():
    state = setup_game(player_ids=[ALICE, BOB, CARLA], rng=random.Random(1))

    assert state.phase is Phase.SETUP_PLACEMENT
    assert all(state.armies[territory] == 1 for territory in Territory)
    for player_id in (ALICE, BOB, CARLA):
        assert len(state.territories_owned_by(player_id=player_id)) == 14
    assert state.current_player == ALICE
    assert state.armies_to_place == 35 - 14
    assert state.winner is None


def test_setup_is_deterministic_by_seed():
    first = setup_game(player_ids=[ALICE, BOB, CARLA], rng=random.Random(5))
    second = setup_game(player_ids=[ALICE, BOB, CARLA], rng=random.Random(5))
    assert first.owner == second.owner
    assert {p: s.mission for p, s in first.players.items()} == {p: s.mission for p, s in second.players.items()}


def test_setup_rejects_invalid_player_counts_and_duplicate_ids():
    with pytest.raises(ValueError):
        setup_game(player_ids=[ALICE, BOB], rng=random.Random(0))
    with pytest.raises(ValueError):
        setup_game(player_ids=[ALICE, BOB, CARLA, "dan", "erin", "fred", "gina"], rng=random.Random(0))
    with pytest.raises(ValueError):
        setup_game(player_ids=[ALICE, ALICE, BOB], rng=random.Random(0))


def test_setup_placement_cycles_players_then_starts_turn_one():
    rng = random.Random(2)
    state = setup_game(player_ids=[ALICE, BOB, CARLA], rng=rng)

    for expected_player in (ALICE, BOB, CARLA):
        assert state.current_player == expected_player
        territory = sorted(state.territories_owned_by(player_id=expected_player))[0]
        action = PlaceArmies(territory=territory, count=state.armies_to_place)
        result = apply_action(state=state, action=action, rng=rng)
        state = result.state

    assert state.phase is Phase.REINFORCE
    assert state.current_player == ALICE
    assert state.turn_number == 1
    assert TurnStarted(player_id=ALICE, turn_number=1) in result.events
    assert state.armies_to_place == reinforcement_count(state=state, player_id=ALICE)
    for player_id in (ALICE, BOB, CARLA):
        assert state.army_count(player_id=player_id) == 35


def test_placing_all_reinforcements_moves_to_the_attack_phase():
    state = alaska_attack_state(alaska_armies=1)
    state.phase = Phase.REINFORCE
    state.armies_to_place = 5

    partial = apply_action(
        state=state, action=PlaceArmies(territory=Territory.ALASKA, count=2), rng=random.Random(0)
    ).state
    assert partial.phase is Phase.REINFORCE
    assert partial.armies_to_place == 3

    finished = apply_action(
        state=partial, action=PlaceArmies(territory=Territory.ALASKA, count=3), rng=random.Random(0)
    ).state
    assert finished.phase is Phase.ATTACK
    assert finished.armies[Territory.ALASKA] == 6


def test_trading_cards_grants_bonus_armies_and_territory_bonus():
    state = alaska_attack_state(alaska_armies=1)
    state.phase = Phase.REINFORCE
    state.armies_to_place = 3
    card_set = (
        TerritoryCard(design=CardDesign.INFANTRY, territory=Territory.ALASKA),  # owned -> +2 on Alaska
        TerritoryCard(design=CardDesign.INFANTRY, territory=Territory.JAPAN),
        TerritoryCard(design=CardDesign.INFANTRY, territory=Territory.EGYPT),
    )
    state.players[ALICE].cards = list(card_set)

    result = apply_action(state=state, action=TradeCards(cards=card_set), rng=random.Random(0))

    assert result.state.armies_to_place == 3 + 4
    assert result.state.armies[Territory.ALASKA] == 1 + 2
    assert result.state.players[ALICE].cards == []


def test_unsuccessful_attack_only_costs_armies():
    state = alaska_attack_state(alaska_armies=4)

    result = apply_action(state=state, action=ATTACK_NORTHWEST, rng=ScriptedRandom(list(LOSING_ROLLS)))

    assert result.state.armies[Territory.ALASKA] == 3
    assert result.state.armies[Territory.NORTHWEST_TERRITORY] == 1
    assert result.state.owner[Territory.NORTHWEST_TERRITORY] == CARLA
    assert result.state.phase is Phase.ATTACK


def test_conquering_a_territory_advances_armies_and_stays_in_the_attack_phase():
    state = alaska_attack_state(alaska_armies=4)

    result = apply_action(state=state, action=ATTACK_NORTHWEST, rng=ScriptedRandom(list(CONQUERING_ROLLS)))

    assert result.state.owner[Territory.NORTHWEST_TERRITORY] == ALICE
    # the default leave-behind of 1 advances all but one surviving attacker into the conquered territory
    assert result.state.armies[Territory.ALASKA] == 1
    assert result.state.armies[Territory.NORTHWEST_TERRITORY] == 3
    assert result.state.phase is Phase.ATTACK
    assert (
        TerritoryConquered(player_id=ALICE, territory=Territory.NORTHWEST_TERRITORY, from_territory=Territory.ALASKA)
        in result.events
    )


def test_armies_to_leave_behind_controls_how_many_advance():
    state = alaska_attack_state(alaska_armies=4)
    attack = Attack(
        from_territory=Territory.ALASKA,
        to_territory=Territory.NORTHWEST_TERRITORY,
        attacker_dice=3,
        max_attacks=1,
        armies_to_leave_behind=3,
    )

    result = apply_action(state=state, action=attack, rng=ScriptedRandom(list(CONQUERING_ROLLS)))

    assert result.state.armies[Territory.ALASKA] == 3
    assert result.state.armies[Territory.NORTHWEST_TERRITORY] == 1


def test_attack_repeats_until_the_target_falls_or_the_count_runs_out():
    state = alaska_attack_state(alaska_armies=5)
    state.armies[Territory.NORTHWEST_TERRITORY] = 2
    attack = Attack(
        from_territory=Territory.ALASKA,
        to_territory=Territory.NORTHWEST_TERRITORY,
        attacker_dice=3,
        max_attacks=3,
        armies_to_leave_behind=1,
    )
    # round one trades a loss each way (defender 2 -> 1, alaska 5 -> 4); round two captures it
    rolls = [6, 1, 1, 5, 2] + [6, 2, 2, 1]

    result = apply_action(state=state, action=attack, rng=ScriptedRandom(rolls))

    battles = [event for event in result.events if isinstance(event, BattleResolved)]
    assert len(battles) == 2
    assert result.state.owner[Territory.NORTHWEST_TERRITORY] == ALICE
    assert result.state.armies[Territory.ALASKA] == 1
    assert result.state.armies[Territory.NORTHWEST_TERRITORY] == 3
    assert result.state.phase is Phase.ATTACK


def test_eliminating_a_player_transfers_their_cards():
    state = alaska_attack_state(alaska_armies=4)
    carla_card = TerritoryCard(design=CardDesign.CAVALRY, territory=Territory.JAPAN)
    state.players[CARLA].cards = [carla_card]

    result = apply_action(state=state, action=ATTACK_NORTHWEST, rng=ScriptedRandom(list(CONQUERING_ROLLS)))

    assert result.state.players[CARLA].eliminated_by == ALICE
    assert result.state.players[CARLA].cards == []
    assert carla_card in result.state.players[ALICE].cards
    assert PlayerEliminated(eliminated_player=CARLA, eliminated_by=ALICE) in result.events
    assert result.state.winner is None  # bob is still alive


def test_completing_a_mission_by_conquest_wins_the_game():
    territories = (
        BOARD.continent_info(Continent.NORTH_AMERICA).territories
        | BOARD.continent_info(Continent.AFRICA).territories
        | BOARD.continent_info(Continent.SOUTH_AMERICA).territories
        | BOARD.continent_info(Continent.OCEANIA).territories
    )  # 23 territories; one more completes the conquer-24 mission
    owner = split_board_between(first_player=ALICE, first_player_territories=territories, second_player=BOB)
    armies = {territory: 1 for territory in Territory}
    armies[Territory.NORTH_AFRICA] = 4
    state = make_state(
        owner=owner,
        armies=armies,
        missions={ALICE: PLACEHOLDER_MISSION, BOB: PLACEHOLDER_MISSION, CARLA: PLACEHOLDER_MISSION},
        eliminated_by={ALICE: None, BOB: None, CARLA: None},
    )
    state.owner[Territory.JAPAN] = CARLA  # keep carla alive so this is a mission win, not last-standing
    state.phase = Phase.ATTACK
    attack = Attack(
        from_territory=Territory.NORTH_AFRICA,
        to_territory=Territory.WESTERN_EUROPE,
        attacker_dice=3,
        max_attacks=1,
        armies_to_leave_behind=1,
    )

    result = apply_action(state=state, action=attack, rng=ScriptedRandom(list(CONQUERING_ROLLS)))

    assert result.state.winner == ALICE
    assert result.state.phase is Phase.GAME_OVER
    assert any(isinstance(event, MissionCompleted) for event in result.events)
    assert legal_actions(state=result.state) == []


def test_fortifying_ends_the_turn_and_draws_a_card_after_a_conquest():
    state = alaska_attack_state(alaska_armies=4)
    state.phase = Phase.FORTIFY
    state.conquered_territory_this_turn = True
    state.owner[Territory.ALBERTA] = ALICE
    fortify = legal_actions(state=state)[0]

    result = apply_action(state=state, action=fortify, rng=random.Random(0))

    assert CardDrawn(player_id=ALICE) in result.events
    assert len(result.state.players[ALICE].cards) == 1
    assert result.state.current_player == BOB
    assert result.state.phase is Phase.REINFORCE
    assert result.state.turn_number == state.turn_number + 1


def test_skipping_fortify_without_a_conquest_draws_no_card():
    state = alaska_attack_state(alaska_armies=4)
    state.phase = Phase.FORTIFY

    result = apply_action(state=state, action=SkipFortify(), rng=random.Random(0))

    assert result.state.players[ALICE].cards == []
    assert result.state.current_player == BOB
    assert result.state.armies_to_place == reinforcement_count(state=result.state, player_id=BOB)


def test_turn_passes_over_eliminated_players():
    state = alaska_attack_state(alaska_armies=4)
    state.phase = Phase.FORTIFY
    state.players[BOB].eliminated_by = CARLA

    result = apply_action(state=state, action=SkipFortify(), rng=random.Random(0))

    assert result.state.current_player == CARLA


def test_illegal_actions_are_rejected_with_a_reason():
    state = alaska_attack_state(alaska_armies=4)
    with pytest.raises(IllegalActionError, match="not adjacent"):
        apply_action(
            state=state,
            action=Attack(
                from_territory=Territory.ALASKA,
                to_territory=Territory.JAPAN,
                attacker_dice=3,
                max_attacks=1,
                armies_to_leave_behind=1,
            ),
            rng=random.Random(0),
        )


def test_apply_action_does_not_mutate_the_input_state():
    state = alaska_attack_state(alaska_armies=4)

    apply_action(state=state, action=ATTACK_NORTHWEST, rng=ScriptedRandom(list(CONQUERING_ROLLS)))

    assert state.armies[Territory.NORTHWEST_TERRITORY] == 1
    assert state.owner[Territory.NORTHWEST_TERRITORY] == CARLA
    assert state.phase is Phase.ATTACK


def test_random_playthroughs_finish_with_a_winner_and_keep_invariants():
    for seed in range(5):
        rng = random.Random(seed)
        state = setup_game(player_ids=[ALICE, BOB, CARLA], rng=rng)
        for _ in range(20_000):
            if state.phase is Phase.GAME_OVER:
                break
            action = rng.choice(legal_actions(state=state))
            state = apply_action(state=state, action=action, rng=rng).state
            _assert_invariants(state=state)
        assert state.phase is Phase.GAME_OVER, f"seed {seed} did not finish"
        assert state.winner is not None


def _assert_invariants(state: GameState) -> None:
    for territory in Territory:
        owner = state.owner[territory]
        assert state.players[owner].eliminated_by is None, f"{territory} owned by eliminated player"
        assert state.armies[territory] >= 1, f"{territory} has {state.armies[territory]} armies"
    if state.winner is None:
        living_players = [p for p, player in state.players.items() if player.eliminated_by is None]
        assert len(living_players) >= 2
