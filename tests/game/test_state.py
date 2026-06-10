from risk_bench.game.board import BOARD
from risk_bench.game.types import Continent, Territory
from tests.game.builders import PLACEHOLDER_MISSION, make_state, split_board_between


def test_state_queries():
    south_america = BOARD.continent_info(Continent.SOUTH_AMERICA).territories
    owner = split_board_between(first_player="alice", first_player_territories=south_america, second_player="bob")
    state = make_state(
        owner=owner,
        armies={territory: 3 for territory in Territory},
        missions={"alice": PLACEHOLDER_MISSION, "bob": PLACEHOLDER_MISSION},
        eliminated_by={"alice": None, "bob": None},
    )

    assert state.territories_owned_by(player_id="alice") == south_america
    assert state.continents_owned_by(player_id="alice") == frozenset({Continent.SOUTH_AMERICA})
    assert state.continents_owned_by(player_id="bob") == frozenset(
        {Continent.NORTH_AMERICA, Continent.EUROPE, Continent.AFRICA, Continent.ASIA, Continent.OCEANIA}
    )
    assert state.army_count(player_id="alice") == 12
    assert state.army_count(player_id="bob") == 114
