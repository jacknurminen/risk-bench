from dataclasses import dataclass
from typing import Final

from risk_bench.game.types import Continent, Territory


@dataclass(frozen=True)
class TerritoryNode:
    """A territory's static properties: its continent and which territories border it."""

    territory: Territory
    continent: Continent
    neighbors: frozenset[Territory]


@dataclass(frozen=True)
class ContinentInfo:
    """A continent's static properties: its member territories and reinforcement bonus."""

    continent: Continent
    territories: frozenset[Territory]
    army_bonus: int


class Board:
    """The static board graph: territory adjacency and continent membership.

    Holds no game state; ownership and army counts live in ``GameState``.
    """

    def __init__(
        self,
        territory_nodes: dict[Territory, TerritoryNode],
        continent_infos: dict[Continent, ContinentInfo],
    ) -> None:
        self._territory_nodes = territory_nodes
        self._continent_infos = continent_infos

    def neighbors_of(self, territory: Territory) -> frozenset[Territory]:
        """Return the territories adjacent to the given territory.

        Args:
            territory: The territory whose neighbors to look up.

        Returns:
            The set of bordering territories.
        """
        return self._territory_nodes[territory].neighbors

    def continent_of(self, territory: Territory) -> Continent:
        """Return the continent the given territory belongs to.

        Args:
            territory: The territory whose continent to look up.

        Returns:
            The containing continent.
        """
        return self._territory_nodes[territory].continent

    def continent_info(self, continent: Continent) -> ContinentInfo:
        """Return the static info (member territories, army bonus) for a continent.

        Args:
            continent: The continent to look up.

        Returns:
            The continent's static properties.
        """
        return self._continent_infos[continent]

    def all_territories(self) -> frozenset[Territory]:
        """Return all 42 territories on the board."""
        return frozenset(self._territory_nodes)


_CONTINENT_ARMY_BONUSES: Final[dict[Continent, int]] = {
    Continent.NORTH_AMERICA: 5,
    Continent.SOUTH_AMERICA: 2,
    Continent.EUROPE: 5,
    Continent.AFRICA: 3,
    Continent.ASIA: 7,
    Continent.OCEANIA: 2,
}

_CONTINENT_TERRITORIES: Final[dict[Continent, frozenset[Territory]]] = {
    Continent.NORTH_AMERICA: frozenset(
        {
            Territory.ALASKA,
            Territory.NORTHWEST_TERRITORY,
            Territory.GREENLAND,
            Territory.ALBERTA,
            Territory.ONTARIO,
            Territory.QUEBEC,
            Territory.WESTERN_UNITED_STATES,
            Territory.EASTERN_UNITED_STATES,
            Territory.CENTRAL_AMERICA,
        }
    ),
    Continent.SOUTH_AMERICA: frozenset(
        {
            Territory.VENEZUELA,
            Territory.BRAZIL,
            Territory.PERU,
            Territory.ARGENTINA,
        }
    ),
    Continent.EUROPE: frozenset(
        {
            Territory.ICELAND,
            Territory.GREAT_BRITAIN,
            Territory.SCANDINAVIA,
            Territory.NORTHERN_EUROPE,
            Territory.WESTERN_EUROPE,
            Territory.SOUTHERN_EUROPE,
            Territory.UKRAINE,
        }
    ),
    Continent.AFRICA: frozenset(
        {
            Territory.NORTH_AFRICA,
            Territory.EGYPT,
            Territory.EAST_AFRICA,
            Territory.CONGO,
            Territory.SOUTH_AFRICA,
            Territory.MADAGASCAR,
        }
    ),
    Continent.ASIA: frozenset(
        {
            Territory.URAL,
            Territory.SIBERIA,
            Territory.YAKUTSK,
            Territory.KAMCHATKA,
            Territory.IRKUTSK,
            Territory.MONGOLIA,
            Territory.JAPAN,
            Territory.AFGHANISTAN,
            Territory.MIDDLE_EAST,
            Territory.INDIA,
            Territory.SIAM,
            Territory.CHINA,
        }
    ),
    Continent.OCEANIA: frozenset(
        {
            Territory.INDONESIA,
            Territory.NEW_GUINEA,
            Territory.WESTERN_AUSTRALIA,
            Territory.EASTERN_AUSTRALIA,
        }
    ),
}

_ADJACENCY: Final[dict[Territory, frozenset[Territory]]] = {
    # North America
    Territory.ALASKA: frozenset({Territory.NORTHWEST_TERRITORY, Territory.ALBERTA, Territory.KAMCHATKA}),
    Territory.NORTHWEST_TERRITORY: frozenset(
        {Territory.ALASKA, Territory.ALBERTA, Territory.ONTARIO, Territory.GREENLAND}
    ),
    Territory.GREENLAND: frozenset(
        {Territory.NORTHWEST_TERRITORY, Territory.ONTARIO, Territory.QUEBEC, Territory.ICELAND}
    ),
    Territory.ALBERTA: frozenset(
        {Territory.ALASKA, Territory.NORTHWEST_TERRITORY, Territory.ONTARIO, Territory.WESTERN_UNITED_STATES}
    ),
    Territory.ONTARIO: frozenset(
        {
            Territory.NORTHWEST_TERRITORY,
            Territory.ALBERTA,
            Territory.GREENLAND,
            Territory.QUEBEC,
            Territory.WESTERN_UNITED_STATES,
            Territory.EASTERN_UNITED_STATES,
        }
    ),
    Territory.QUEBEC: frozenset({Territory.GREENLAND, Territory.ONTARIO, Territory.EASTERN_UNITED_STATES}),
    Territory.WESTERN_UNITED_STATES: frozenset(
        {Territory.ALBERTA, Territory.ONTARIO, Territory.EASTERN_UNITED_STATES, Territory.CENTRAL_AMERICA}
    ),
    Territory.EASTERN_UNITED_STATES: frozenset(
        {Territory.WESTERN_UNITED_STATES, Territory.ONTARIO, Territory.QUEBEC, Territory.CENTRAL_AMERICA}
    ),
    Territory.CENTRAL_AMERICA: frozenset(
        {Territory.WESTERN_UNITED_STATES, Territory.EASTERN_UNITED_STATES, Territory.VENEZUELA}
    ),
    # South America
    Territory.VENEZUELA: frozenset({Territory.CENTRAL_AMERICA, Territory.BRAZIL, Territory.PERU}),
    Territory.BRAZIL: frozenset({Territory.VENEZUELA, Territory.PERU, Territory.ARGENTINA, Territory.NORTH_AFRICA}),
    Territory.PERU: frozenset({Territory.VENEZUELA, Territory.BRAZIL, Territory.ARGENTINA}),
    Territory.ARGENTINA: frozenset({Territory.PERU, Territory.BRAZIL}),
    # Europe
    Territory.ICELAND: frozenset({Territory.GREENLAND, Territory.GREAT_BRITAIN, Territory.SCANDINAVIA}),
    Territory.GREAT_BRITAIN: frozenset(
        {Territory.ICELAND, Territory.SCANDINAVIA, Territory.NORTHERN_EUROPE, Territory.WESTERN_EUROPE}
    ),
    Territory.SCANDINAVIA: frozenset(
        {Territory.ICELAND, Territory.GREAT_BRITAIN, Territory.NORTHERN_EUROPE, Territory.UKRAINE}
    ),
    Territory.NORTHERN_EUROPE: frozenset(
        {
            Territory.GREAT_BRITAIN,
            Territory.SCANDINAVIA,
            Territory.UKRAINE,
            Territory.SOUTHERN_EUROPE,
            Territory.WESTERN_EUROPE,
        }
    ),
    Territory.WESTERN_EUROPE: frozenset(
        {Territory.GREAT_BRITAIN, Territory.NORTHERN_EUROPE, Territory.SOUTHERN_EUROPE, Territory.NORTH_AFRICA}
    ),
    Territory.SOUTHERN_EUROPE: frozenset(
        {
            Territory.WESTERN_EUROPE,
            Territory.NORTHERN_EUROPE,
            Territory.UKRAINE,
            Territory.MIDDLE_EAST,
            Territory.EGYPT,
            Territory.NORTH_AFRICA,
        }
    ),
    Territory.UKRAINE: frozenset(
        {
            Territory.SCANDINAVIA,
            Territory.NORTHERN_EUROPE,
            Territory.SOUTHERN_EUROPE,
            Territory.URAL,
            Territory.AFGHANISTAN,
            Territory.MIDDLE_EAST,
        }
    ),
    # Africa
    Territory.NORTH_AFRICA: frozenset(
        {
            Territory.BRAZIL,
            Territory.WESTERN_EUROPE,
            Territory.SOUTHERN_EUROPE,
            Territory.EGYPT,
            Territory.EAST_AFRICA,
            Territory.CONGO,
        }
    ),
    Territory.EGYPT: frozenset(
        {Territory.NORTH_AFRICA, Territory.SOUTHERN_EUROPE, Territory.MIDDLE_EAST, Territory.EAST_AFRICA}
    ),
    Territory.EAST_AFRICA: frozenset(
        {
            Territory.EGYPT,
            Territory.NORTH_AFRICA,
            Territory.CONGO,
            Territory.SOUTH_AFRICA,
            Territory.MADAGASCAR,
            Territory.MIDDLE_EAST,
        }
    ),
    Territory.CONGO: frozenset({Territory.NORTH_AFRICA, Territory.EAST_AFRICA, Territory.SOUTH_AFRICA}),
    Territory.SOUTH_AFRICA: frozenset({Territory.CONGO, Territory.EAST_AFRICA, Territory.MADAGASCAR}),
    Territory.MADAGASCAR: frozenset({Territory.EAST_AFRICA, Territory.SOUTH_AFRICA}),
    # Asia
    Territory.URAL: frozenset({Territory.UKRAINE, Territory.SIBERIA, Territory.CHINA, Territory.AFGHANISTAN}),
    Territory.SIBERIA: frozenset(
        {Territory.URAL, Territory.YAKUTSK, Territory.IRKUTSK, Territory.MONGOLIA, Territory.CHINA}
    ),
    Territory.YAKUTSK: frozenset({Territory.SIBERIA, Territory.KAMCHATKA, Territory.IRKUTSK}),
    Territory.KAMCHATKA: frozenset(
        {Territory.YAKUTSK, Territory.IRKUTSK, Territory.MONGOLIA, Territory.JAPAN, Territory.ALASKA}
    ),
    Territory.IRKUTSK: frozenset({Territory.SIBERIA, Territory.YAKUTSK, Territory.KAMCHATKA, Territory.MONGOLIA}),
    Territory.MONGOLIA: frozenset(
        {Territory.SIBERIA, Territory.IRKUTSK, Territory.KAMCHATKA, Territory.JAPAN, Territory.CHINA}
    ),
    Territory.JAPAN: frozenset({Territory.KAMCHATKA, Territory.MONGOLIA}),
    Territory.AFGHANISTAN: frozenset(
        {Territory.UKRAINE, Territory.URAL, Territory.CHINA, Territory.INDIA, Territory.MIDDLE_EAST}
    ),
    Territory.MIDDLE_EAST: frozenset(
        {
            Territory.UKRAINE,
            Territory.SOUTHERN_EUROPE,
            Territory.EGYPT,
            Territory.EAST_AFRICA,
            Territory.AFGHANISTAN,
            Territory.INDIA,
        }
    ),
    Territory.INDIA: frozenset({Territory.MIDDLE_EAST, Territory.AFGHANISTAN, Territory.CHINA, Territory.SIAM}),
    Territory.SIAM: frozenset({Territory.INDIA, Territory.CHINA, Territory.INDONESIA}),
    Territory.CHINA: frozenset(
        {
            Territory.URAL,
            Territory.SIBERIA,
            Territory.MONGOLIA,
            Territory.AFGHANISTAN,
            Territory.INDIA,
            Territory.SIAM,
        }
    ),
    # Oceania
    Territory.INDONESIA: frozenset({Territory.SIAM, Territory.NEW_GUINEA, Territory.WESTERN_AUSTRALIA}),
    Territory.NEW_GUINEA: frozenset({Territory.INDONESIA, Territory.WESTERN_AUSTRALIA, Territory.EASTERN_AUSTRALIA}),
    Territory.WESTERN_AUSTRALIA: frozenset({Territory.INDONESIA, Territory.NEW_GUINEA, Territory.EASTERN_AUSTRALIA}),
    Territory.EASTERN_AUSTRALIA: frozenset({Territory.NEW_GUINEA, Territory.WESTERN_AUSTRALIA}),
}


def _build_classic_board() -> Board:
    continent_by_territory = {
        territory: continent for continent, territories in _CONTINENT_TERRITORIES.items() for territory in territories
    }
    territory_nodes = {
        territory: TerritoryNode(
            territory=territory,
            continent=continent_by_territory[territory],
            neighbors=neighbors,
        )
        for territory, neighbors in _ADJACENCY.items()
    }
    continent_infos = {
        continent: ContinentInfo(
            continent=continent,
            territories=territories,
            army_bonus=_CONTINENT_ARMY_BONUSES[continent],
        )
        for continent, territories in _CONTINENT_TERRITORIES.items()
    }
    return Board(territory_nodes=territory_nodes, continent_infos=continent_infos)


BOARD: Final[Board] = _build_classic_board()
