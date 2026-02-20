from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Continent(StrEnum):
    NORTH_AMERICA = "north_america"
    SOUTH_AMERICA = "south_america"
    EUROPE = "europe"
    AFRICA = "africa"
    ASIA = "asia"
    OCEANIA = "oceania"


class Territory(StrEnum):
    # North America
    ALASKA = "alaska"
    NORTHWEST_TERRITORY = "northwest_territory"
    GREENLAND = "greenland"
    ALBERTA = "alberta"
    ONTARIO = "ontario"
    QUEBEC = "quebec"
    WESTERN_UNITED_STATES = "western_united_states"
    EASTERN_UNITED_STATES = "eastern_united_states"
    CENTRAL_AMERICA = "central_america"
    # South America
    VENEZUELA = "venezuela"
    PERU = "peru"
    BRAZIL = "brazil"
    ARGENTINA = "argentina"
    # Europe
    ICELAND = "iceland"
    GREAT_BRITAIN = "great_britain"
    SCANDINAVIA = "scandinavia"
    NORTHERN_EUROPE = "northern_europe"
    WESTERN_EUROPE = "western_europe"
    SOUTHERN_EUROPE = "southern_europe"
    UKRAINE = "ukraine"
    # Africa
    NORTH_AFRICA = "north_africa"
    EGYPT = "egypt"
    EAST_AFRICA = "east_africa"
    CENTRAL_AFRICA = "central_africa"
    SOUTH_AFRICA = "south_africa"
    MADAGASCAR = "madagascar"
    # Asia
    URAL = "ural"
    SIBERIA = "siberia"
    YAKUTSK = "yakutsk"
    KAMCHATKA = "kamchatka"
    IRKUTSK = "irkutsk"
    MONGOLIA = "mongolia"
    JAPAN = "japan"
    AFGHANISTAN = "afghanistan"
    CHINA = "china"
    MIDDLE_EAST = "middle_east"
    INDIA = "india"
    SOUTHEAST_ASIA = "southeast_asia"
    # Oceania
    INDONESIA = "indonesia"
    NEW_GUINEA = "new_guinea"
    WESTERN_AUSTRALIA = "western_australia"
    EASTERN_AUSTRALIA = "eastern_australia"


@dataclass(frozen=True)
class TerritoryNode:
    name: Territory
    continent: Continent
    adjacent: tuple[Territory, ...]


@dataclass(frozen=True)
class ContinentInfo:
    name: Continent
    bonus: int
    territories: tuple[Territory, ...]


# ---------------------------------------------------------------------------
# Short aliases for the data tables below (class references, not magic strings)
# ---------------------------------------------------------------------------
_T = Territory
_C = Continent

# ---------------------------------------------------------------------------
# Territory definitions (42 canonical Risk territories)
# ---------------------------------------------------------------------------
_TERRITORY_DATA: list[tuple[Territory, Continent, list[Territory]]] = [
    # North America (9)
    (_T.ALASKA, _C.NORTH_AMERICA, [_T.NORTHWEST_TERRITORY, _T.ALBERTA, _T.KAMCHATKA]),
    (_T.NORTHWEST_TERRITORY, _C.NORTH_AMERICA, [_T.ALASKA, _T.ALBERTA, _T.ONTARIO, _T.GREENLAND]),
    (_T.GREENLAND, _C.NORTH_AMERICA, [_T.NORTHWEST_TERRITORY, _T.ONTARIO, _T.QUEBEC, _T.ICELAND]),
    (_T.ALBERTA, _C.NORTH_AMERICA, [_T.ALASKA, _T.NORTHWEST_TERRITORY, _T.ONTARIO, _T.WESTERN_UNITED_STATES]),
    (
        _T.ONTARIO,
        _C.NORTH_AMERICA,
        [
            _T.NORTHWEST_TERRITORY,
            _T.ALBERTA,
            _T.GREENLAND,
            _T.QUEBEC,
            _T.WESTERN_UNITED_STATES,
            _T.EASTERN_UNITED_STATES,
        ],
    ),
    (_T.QUEBEC, _C.NORTH_AMERICA, [_T.GREENLAND, _T.ONTARIO, _T.EASTERN_UNITED_STATES]),
    (
        _T.WESTERN_UNITED_STATES,
        _C.NORTH_AMERICA,
        [_T.ALBERTA, _T.ONTARIO, _T.EASTERN_UNITED_STATES, _T.CENTRAL_AMERICA],
    ),
    (
        _T.EASTERN_UNITED_STATES,
        _C.NORTH_AMERICA,
        [_T.ONTARIO, _T.QUEBEC, _T.WESTERN_UNITED_STATES, _T.CENTRAL_AMERICA],
    ),
    (_T.CENTRAL_AMERICA, _C.NORTH_AMERICA, [_T.WESTERN_UNITED_STATES, _T.EASTERN_UNITED_STATES, _T.VENEZUELA]),
    # South America (4)
    (_T.VENEZUELA, _C.SOUTH_AMERICA, [_T.CENTRAL_AMERICA, _T.PERU, _T.BRAZIL]),
    (_T.PERU, _C.SOUTH_AMERICA, [_T.VENEZUELA, _T.BRAZIL, _T.ARGENTINA]),
    (_T.BRAZIL, _C.SOUTH_AMERICA, [_T.VENEZUELA, _T.PERU, _T.ARGENTINA, _T.NORTH_AFRICA]),
    (_T.ARGENTINA, _C.SOUTH_AMERICA, [_T.PERU, _T.BRAZIL]),
    # Europe (7)
    (_T.ICELAND, _C.EUROPE, [_T.GREENLAND, _T.GREAT_BRITAIN, _T.SCANDINAVIA]),
    (_T.GREAT_BRITAIN, _C.EUROPE, [_T.ICELAND, _T.SCANDINAVIA, _T.NORTHERN_EUROPE, _T.WESTERN_EUROPE]),
    (_T.SCANDINAVIA, _C.EUROPE, [_T.ICELAND, _T.GREAT_BRITAIN, _T.NORTHERN_EUROPE, _T.UKRAINE]),
    (
        _T.NORTHERN_EUROPE,
        _C.EUROPE,
        [_T.GREAT_BRITAIN, _T.SCANDINAVIA, _T.UKRAINE, _T.WESTERN_EUROPE, _T.SOUTHERN_EUROPE],
    ),
    (_T.WESTERN_EUROPE, _C.EUROPE, [_T.GREAT_BRITAIN, _T.NORTHERN_EUROPE, _T.SOUTHERN_EUROPE, _T.NORTH_AFRICA]),
    (
        _T.SOUTHERN_EUROPE,
        _C.EUROPE,
        [_T.NORTHERN_EUROPE, _T.WESTERN_EUROPE, _T.UKRAINE, _T.NORTH_AFRICA, _T.EGYPT, _T.MIDDLE_EAST],
    ),
    (
        _T.UKRAINE,
        _C.EUROPE,
        [_T.SCANDINAVIA, _T.NORTHERN_EUROPE, _T.SOUTHERN_EUROPE, _T.URAL, _T.AFGHANISTAN, _T.MIDDLE_EAST],
    ),
    # Africa (6)
    (
        _T.NORTH_AFRICA,
        _C.AFRICA,
        [_T.BRAZIL, _T.WESTERN_EUROPE, _T.SOUTHERN_EUROPE, _T.EGYPT, _T.EAST_AFRICA, _T.CENTRAL_AFRICA],
    ),
    (_T.EGYPT, _C.AFRICA, [_T.SOUTHERN_EUROPE, _T.NORTH_AFRICA, _T.EAST_AFRICA, _T.MIDDLE_EAST]),
    (
        _T.EAST_AFRICA,
        _C.AFRICA,
        [_T.NORTH_AFRICA, _T.EGYPT, _T.CENTRAL_AFRICA, _T.SOUTH_AFRICA, _T.MADAGASCAR, _T.MIDDLE_EAST],
    ),
    (_T.CENTRAL_AFRICA, _C.AFRICA, [_T.NORTH_AFRICA, _T.EAST_AFRICA, _T.SOUTH_AFRICA]),
    (_T.SOUTH_AFRICA, _C.AFRICA, [_T.CENTRAL_AFRICA, _T.EAST_AFRICA, _T.MADAGASCAR]),
    (_T.MADAGASCAR, _C.AFRICA, [_T.EAST_AFRICA, _T.SOUTH_AFRICA]),
    # Asia (12)
    (_T.URAL, _C.ASIA, [_T.UKRAINE, _T.SIBERIA, _T.AFGHANISTAN, _T.CHINA]),
    (_T.SIBERIA, _C.ASIA, [_T.URAL, _T.YAKUTSK, _T.IRKUTSK, _T.MONGOLIA, _T.CHINA]),
    (_T.YAKUTSK, _C.ASIA, [_T.SIBERIA, _T.KAMCHATKA, _T.IRKUTSK]),
    (_T.KAMCHATKA, _C.ASIA, [_T.YAKUTSK, _T.IRKUTSK, _T.MONGOLIA, _T.JAPAN, _T.ALASKA]),
    (_T.IRKUTSK, _C.ASIA, [_T.SIBERIA, _T.YAKUTSK, _T.KAMCHATKA, _T.MONGOLIA]),
    (_T.MONGOLIA, _C.ASIA, [_T.SIBERIA, _T.IRKUTSK, _T.KAMCHATKA, _T.JAPAN, _T.CHINA]),
    (_T.JAPAN, _C.ASIA, [_T.KAMCHATKA, _T.MONGOLIA]),
    (_T.AFGHANISTAN, _C.ASIA, [_T.UKRAINE, _T.URAL, _T.CHINA, _T.INDIA, _T.MIDDLE_EAST]),
    (_T.CHINA, _C.ASIA, [_T.URAL, _T.SIBERIA, _T.MONGOLIA, _T.AFGHANISTAN, _T.INDIA, _T.SOUTHEAST_ASIA]),
    (_T.MIDDLE_EAST, _C.ASIA, [_T.SOUTHERN_EUROPE, _T.UKRAINE, _T.AFGHANISTAN, _T.INDIA, _T.EGYPT, _T.EAST_AFRICA]),
    (_T.INDIA, _C.ASIA, [_T.AFGHANISTAN, _T.CHINA, _T.MIDDLE_EAST, _T.SOUTHEAST_ASIA]),
    (_T.SOUTHEAST_ASIA, _C.ASIA, [_T.CHINA, _T.INDIA, _T.INDONESIA]),
    # Oceania (4)
    (_T.INDONESIA, _C.OCEANIA, [_T.SOUTHEAST_ASIA, _T.NEW_GUINEA, _T.WESTERN_AUSTRALIA]),
    (_T.NEW_GUINEA, _C.OCEANIA, [_T.INDONESIA, _T.WESTERN_AUSTRALIA, _T.EASTERN_AUSTRALIA]),
    (_T.WESTERN_AUSTRALIA, _C.OCEANIA, [_T.INDONESIA, _T.NEW_GUINEA, _T.EASTERN_AUSTRALIA]),
    (_T.EASTERN_AUSTRALIA, _C.OCEANIA, [_T.NEW_GUINEA, _T.WESTERN_AUSTRALIA]),
]

# ---------------------------------------------------------------------------
# Continent definitions
# ---------------------------------------------------------------------------
_CONTINENT_DATA: list[tuple[Continent, int, list[Territory]]] = [
    (
        _C.NORTH_AMERICA,
        5,
        [
            _T.ALASKA,
            _T.NORTHWEST_TERRITORY,
            _T.GREENLAND,
            _T.ALBERTA,
            _T.ONTARIO,
            _T.QUEBEC,
            _T.WESTERN_UNITED_STATES,
            _T.EASTERN_UNITED_STATES,
            _T.CENTRAL_AMERICA,
        ],
    ),
    (_C.SOUTH_AMERICA, 2, [_T.VENEZUELA, _T.PERU, _T.BRAZIL, _T.ARGENTINA]),
    (
        _C.EUROPE,
        5,
        [
            _T.ICELAND,
            _T.GREAT_BRITAIN,
            _T.SCANDINAVIA,
            _T.NORTHERN_EUROPE,
            _T.WESTERN_EUROPE,
            _T.SOUTHERN_EUROPE,
            _T.UKRAINE,
        ],
    ),
    (_C.AFRICA, 3, [_T.NORTH_AFRICA, _T.EGYPT, _T.EAST_AFRICA, _T.CENTRAL_AFRICA, _T.SOUTH_AFRICA, _T.MADAGASCAR]),
    (
        _C.ASIA,
        7,
        [
            _T.URAL,
            _T.SIBERIA,
            _T.YAKUTSK,
            _T.KAMCHATKA,
            _T.IRKUTSK,
            _T.MONGOLIA,
            _T.JAPAN,
            _T.AFGHANISTAN,
            _T.CHINA,
            _T.MIDDLE_EAST,
            _T.INDIA,
            _T.SOUTHEAST_ASIA,
        ],
    ),
    (_C.OCEANIA, 2, [_T.INDONESIA, _T.NEW_GUINEA, _T.WESTERN_AUSTRALIA, _T.EASTERN_AUSTRALIA]),
]


class Board:
    """Immutable board loaded once at startup."""

    def __init__(self) -> None:
        self.territories: dict[Territory, TerritoryNode] = {}
        for name, continent, adjacent in _TERRITORY_DATA:
            self.territories[name] = TerritoryNode(name=name, continent=continent, adjacent=tuple(adjacent))

        self.continents: dict[Continent, ContinentInfo] = {}
        for name, bonus, territories in _CONTINENT_DATA:
            self.continents[name] = ContinentInfo(name=name, bonus=bonus, territories=tuple(territories))

        # quick-lookup: territory -> continent
        self._territory_continent: dict[Territory, Continent] = {
            t.name: t.continent for t in self.territories.values()
        }

    # ------------------------------------------------------------------
    def are_adjacent(self, territory: Territory, other_territory: Territory) -> bool:
        """Return True if the two territories share a border.

        Args:
            territory: The first territory.
            other_territory: The territory to check adjacency against.

        Returns:
            True if the territories are adjacent.
        """
        return other_territory in self.territories[territory].adjacent

    def continent_of(self, territory: Territory) -> Continent:
        """Return the continent that contains the given territory.

        Args:
            territory: Territory to look up.

        Returns:
            Continent enum value.
        """
        return self._territory_continent[territory]

    def continent_bonus(self, continent: Continent) -> int:
        """Return the reinforcement bonus for controlling the given continent.

        Args:
            continent: Continent to look up.

        Returns:
            Number of bonus troops awarded for full continent control.
        """
        return self.continents[continent].bonus

    def territories_of(self, continent: Continent) -> tuple[Territory, ...]:
        """Return the territories that make up the given continent.

        Args:
            continent: Continent to look up.

        Returns:
            Tuple of Territory enum values.
        """
        return self.continents[continent].territories

    def all_territory_names(self) -> list[Territory]:
        """Return a list of all 42 territories on the board.

        Returns:
            List of Territory enum values.
        """
        return list(self.territories.keys())

    def continent_controller(self, owner_map: dict[Territory, str]) -> dict[Continent, str | None]:
        """Return the sole controller of each continent, or None if contested.

        Args:
            owner_map: Mapping of territory to owning player color.

        Returns:
            Dict mapping continent to the controlling player color, or None if no
            single player owns all territories in that continent.
        """
        result: dict[Continent, str | None] = {}
        for continent_name, continent in self.continents.items():
            owners = {owner_map[territory] for territory in continent.territories if territory in owner_map}
            result[continent_name] = owners.pop() if len(owners) == 1 else None
        return result


# Module-level singleton
BOARD = Board()
