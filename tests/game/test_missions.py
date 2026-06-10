import random

from risk_bench.game.board import BOARD
from risk_bench.game.missions import Mission, MissionKind, deal_missions, effective_mission, is_mission_complete
from risk_bench.game.state import GameState
from risk_bench.game.types import Continent, PlayerId, Territory
from tests.game.builders import PLACEHOLDER_MISSION, make_state, split_board_between

ALICE: PlayerId = "alice"
BOB: PlayerId = "bob"
CARLA: PlayerId = "carla"


def continents(*members: Continent) -> frozenset[Territory]:
    return frozenset(territory for continent in members for territory in BOARD.continent_info(continent).territories)


def two_player_state(
    alice_territories: frozenset[Territory],
    alice_mission: Mission,
    armies_per_territory: int,
) -> GameState:
    owner = split_board_between(first_player=ALICE, first_player_territories=alice_territories, second_player=BOB)
    return make_state(
        owner=owner,
        armies={territory: armies_per_territory for territory in Territory},
        missions={ALICE: alice_mission, BOB: PLACEHOLDER_MISSION},
        eliminated_by={ALICE: None, BOB: None},
    )


def eliminate_mission(target: PlayerId) -> Mission:
    return Mission(
        kind=MissionKind.ELIMINATE_PLAYER,
        description=f"Eliminate {target}.",
        target_continents=frozenset(),
        target_player=target,
    )


TWO_CONTINENTS_MISSION = Mission(
    kind=MissionKind.CONQUER_TWO_CONTINENTS,
    description="Conquer North America and Africa.",
    target_continents=frozenset({Continent.NORTH_AMERICA, Continent.AFRICA}),
    target_player=None,
)

PLUS_ONE_MISSION = Mission(
    kind=MissionKind.CONQUER_TWO_CONTINENTS_PLUS_ONE,
    description="Conquer Europe, South America, and a third continent.",
    target_continents=frozenset({Continent.EUROPE, Continent.SOUTH_AMERICA}),
    target_player=None,
)

EIGHTEEN_TERRITORIES_MISSION = Mission(
    kind=MissionKind.CONQUER_18_TERRITORIES_TWO_ARMIES,
    description="Conquer 18 territories with two armies each.",
    target_continents=frozenset(),
    target_player=None,
)


def test_two_continents_mission_completes_when_both_are_owned():
    state = two_player_state(
        alice_territories=continents(Continent.NORTH_AMERICA, Continent.AFRICA),
        alice_mission=TWO_CONTINENTS_MISSION,
        armies_per_territory=1,
    )
    assert is_mission_complete(mission=TWO_CONTINENTS_MISSION, player_id=ALICE, state=state)


def test_two_continents_mission_incomplete_when_one_territory_is_missing():
    territories = continents(Continent.NORTH_AMERICA, Continent.AFRICA) - {Territory.EGYPT}
    state = two_player_state(
        alice_territories=territories, alice_mission=TWO_CONTINENTS_MISSION, armies_per_territory=1
    )
    assert not is_mission_complete(mission=TWO_CONTINENTS_MISSION, player_id=ALICE, state=state)


def test_plus_one_mission_requires_a_third_continent():
    two_continents_only = two_player_state(
        alice_territories=continents(Continent.EUROPE, Continent.SOUTH_AMERICA),
        alice_mission=PLUS_ONE_MISSION,
        armies_per_territory=1,
    )
    with_third_continent = two_player_state(
        alice_territories=continents(Continent.EUROPE, Continent.SOUTH_AMERICA, Continent.OCEANIA),
        alice_mission=PLUS_ONE_MISSION,
        armies_per_territory=1,
    )
    assert not is_mission_complete(mission=PLUS_ONE_MISSION, player_id=ALICE, state=two_continents_only)
    assert is_mission_complete(mission=PLUS_ONE_MISSION, player_id=ALICE, state=with_third_continent)


def test_24_territories_mission():
    twenty_six_territories = continents(
        Continent.NORTH_AMERICA, Continent.AFRICA, Continent.SOUTH_AMERICA, Continent.EUROPE
    )
    complete = two_player_state(
        alice_territories=twenty_six_territories, alice_mission=PLACEHOLDER_MISSION, armies_per_territory=1
    )
    incomplete = two_player_state(
        alice_territories=continents(Continent.NORTH_AMERICA, Continent.AFRICA),
        alice_mission=PLACEHOLDER_MISSION,
        armies_per_territory=1,
    )
    assert is_mission_complete(mission=PLACEHOLDER_MISSION, player_id=ALICE, state=complete)
    assert not is_mission_complete(mission=PLACEHOLDER_MISSION, player_id=ALICE, state=incomplete)


def test_18_territories_mission_requires_two_armies_on_each():
    eighteen_territories = continents(Continent.ASIA, Continent.AFRICA)  # 12 + 6
    with_two_armies_each = two_player_state(
        alice_territories=eighteen_territories,
        alice_mission=EIGHTEEN_TERRITORIES_MISSION,
        armies_per_territory=2,
    )
    with_single_armies = two_player_state(
        alice_territories=eighteen_territories,
        alice_mission=EIGHTEEN_TERRITORIES_MISSION,
        armies_per_territory=1,
    )
    assert is_mission_complete(mission=EIGHTEEN_TERRITORIES_MISSION, player_id=ALICE, state=with_two_armies_each)
    assert not is_mission_complete(mission=EIGHTEEN_TERRITORIES_MISSION, player_id=ALICE, state=with_single_armies)


def three_player_state(eliminated_by: dict[PlayerId, PlayerId | None]) -> GameState:
    owner = split_board_between(
        first_player=ALICE, first_player_territories=frozenset({Territory.ALASKA}), second_player=BOB
    )
    return make_state(
        owner=owner,
        armies={territory: 1 for territory in Territory},
        missions={ALICE: eliminate_mission(target=CARLA), BOB: PLACEHOLDER_MISSION, CARLA: PLACEHOLDER_MISSION},
        eliminated_by=eliminated_by,
    )


def test_eliminate_mission_completes_only_for_the_killer():
    mission = eliminate_mission(target=CARLA)
    killed_by_holder = three_player_state(eliminated_by={ALICE: None, BOB: None, CARLA: ALICE})
    still_alive = three_player_state(eliminated_by={ALICE: None, BOB: None, CARLA: None})

    assert is_mission_complete(mission=mission, player_id=ALICE, state=killed_by_holder)
    assert not is_mission_complete(mission=mission, player_id=ALICE, state=still_alive)


def test_eliminate_mission_falls_back_when_someone_else_kills_the_target():
    mission = eliminate_mission(target=CARLA)
    killed_by_other = three_player_state(eliminated_by={ALICE: None, BOB: None, CARLA: BOB})

    resolved = effective_mission(mission=mission, player_id=ALICE, state=killed_by_other)
    assert resolved.kind is MissionKind.CONQUER_24_TERRITORIES
    assert not is_mission_complete(mission=mission, player_id=ALICE, state=killed_by_other)


def test_eliminate_mission_targeting_yourself_falls_back():
    mission = eliminate_mission(target=ALICE)
    state = three_player_state(eliminated_by={ALICE: None, BOB: None, CARLA: None})

    resolved = effective_mission(mission=mission, player_id=ALICE, state=state)
    assert resolved.kind is MissionKind.CONQUER_24_TERRITORIES


def test_deal_missions_gives_every_player_a_mission_deterministically():
    player_ids = [ALICE, BOB, CARLA]
    first_deal = deal_missions(player_ids=player_ids, rng=random.Random(3))
    second_deal = deal_missions(player_ids=player_ids, rng=random.Random(3))

    assert list(first_deal) == player_ids
    assert first_deal == second_deal


def test_deal_missions_only_targets_players_in_the_game():
    deals = [deal_missions(player_ids=[ALICE, BOB, CARLA], rng=random.Random(seed)) for seed in range(30)]
    targets = {
        mission.target_player for deal in deals for mission in deal.values() if mission.target_player is not None
    }
    assert targets <= {ALICE, BOB, CARLA}
