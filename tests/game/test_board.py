from risk_bench.game.board import BOARD
from risk_bench.game.types import Continent, Territory

EXPECTED_CONTINENT_SIZES = {
    Continent.NORTH_AMERICA: 9,
    Continent.SOUTH_AMERICA: 4,
    Continent.EUROPE: 7,
    Continent.AFRICA: 6,
    Continent.ASIA: 12,
    Continent.OCEANIA: 4,
}

EXPECTED_CONTINENT_BONUSES = {
    Continent.NORTH_AMERICA: 5,
    Continent.SOUTH_AMERICA: 2,
    Continent.EUROPE: 5,
    Continent.AFRICA: 3,
    Continent.ASIA: 7,
    Continent.OCEANIA: 2,
}


def test_board_has_all_42_territories():
    assert BOARD.all_territories() == frozenset(Territory)
    assert len(BOARD.all_territories()) == 42


def test_adjacency_is_symmetric():
    for territory in BOARD.all_territories():
        for neighbor in BOARD.neighbors_of(territory):
            assert territory in BOARD.neighbors_of(neighbor), f"{territory} -> {neighbor} is not symmetric"


def test_no_territory_neighbors_itself():
    for territory in BOARD.all_territories():
        assert territory not in BOARD.neighbors_of(territory)


def test_every_territory_has_neighbors():
    for territory in BOARD.all_territories():
        assert len(BOARD.neighbors_of(territory)) >= 2


def test_continents_partition_the_territories():
    territories_from_continents = [
        territory for continent in Continent for territory in BOARD.continent_info(continent).territories
    ]
    assert len(territories_from_continents) == 42
    assert frozenset(territories_from_continents) == frozenset(Territory)


def test_continent_sizes():
    for continent, expected_size in EXPECTED_CONTINENT_SIZES.items():
        assert len(BOARD.continent_info(continent).territories) == expected_size


def test_continent_army_bonuses():
    for continent, expected_bonus in EXPECTED_CONTINENT_BONUSES.items():
        assert BOARD.continent_info(continent).army_bonus == expected_bonus


def test_continent_of_matches_continent_membership():
    for continent in Continent:
        for territory in BOARD.continent_info(continent).territories:
            assert BOARD.continent_of(territory) == continent


def test_known_intercontinental_crossings():
    crossings = [
        (Territory.ALASKA, Territory.KAMCHATKA),
        (Territory.GREENLAND, Territory.ICELAND),
        (Territory.CENTRAL_AMERICA, Territory.VENEZUELA),
        (Territory.BRAZIL, Territory.NORTH_AFRICA),
        (Territory.WESTERN_EUROPE, Territory.NORTH_AFRICA),
        (Territory.SOUTHERN_EUROPE, Territory.NORTH_AFRICA),
        (Territory.SOUTHERN_EUROPE, Territory.EGYPT),
        (Territory.SOUTHERN_EUROPE, Territory.MIDDLE_EAST),
        (Territory.UKRAINE, Territory.URAL),
        (Territory.UKRAINE, Territory.AFGHANISTAN),
        (Territory.UKRAINE, Territory.MIDDLE_EAST),
        (Territory.EGYPT, Territory.MIDDLE_EAST),
        (Territory.EAST_AFRICA, Territory.MIDDLE_EAST),
        (Territory.SIAM, Territory.INDONESIA),
    ]
    for territory, neighbor in crossings:
        assert neighbor in BOARD.neighbors_of(territory), f"{territory} should border {neighbor}"


def test_total_border_count_matches_classic_map():
    # the classic map has 83 borders; each border appears twice in the adjacency lists
    total_adjacency_entries = sum(len(BOARD.neighbors_of(territory)) for territory in BOARD.all_territories())
    assert total_adjacency_entries == 166
