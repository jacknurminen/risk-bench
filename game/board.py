"""Immutable Risk board: 42 territories, adjacency lists, continents."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Territory:
    name: str
    continent: str
    adjacent: tuple[str, ...]


@dataclass(frozen=True)
class Continent:
    name: str
    bonus: int
    territories: tuple[str, ...]


# ---------------------------------------------------------------------------
# Territory definitions (42 canonical Risk territories)
# ---------------------------------------------------------------------------
_NA = "north_america"
_SA = "south_america"
_EU = "europe"
_AF = "africa"
_AS = "asia"
_OC = "oceania"

_TERRITORY_DATA: list[tuple[str, str, list[str]]] = [
    # North America (9)
    ("alaska", _NA, ["northwest_territory", "alberta", "kamchatka"]),
    ("northwest_territory", _NA, ["alaska", "alberta", "ontario", "greenland"]),
    ("greenland", _NA, ["northwest_territory", "ontario", "quebec", "iceland"]),
    ("alberta", _NA, ["alaska", "northwest_territory", "ontario", "western_united_states"]),
    (
        "ontario",
        _NA,
        ["northwest_territory", "alberta", "greenland", "quebec", "western_united_states", "eastern_united_states"],
    ),
    ("quebec", _NA, ["greenland", "ontario", "eastern_united_states"]),
    ("western_united_states", _NA, ["alberta", "ontario", "eastern_united_states", "central_america"]),
    ("eastern_united_states", _NA, ["ontario", "quebec", "western_united_states", "central_america"]),
    ("central_america", _NA, ["western_united_states", "eastern_united_states", "venezuela"]),
    # South America (4)
    ("venezuela", _SA, ["central_america", "peru", "brazil"]),
    ("peru", _SA, ["venezuela", "brazil", "argentina"]),
    ("brazil", _SA, ["venezuela", "peru", "argentina", "north_africa"]),
    ("argentina", _SA, ["peru", "brazil"]),
    # Europe (7)
    ("iceland", _EU, ["greenland", "great_britain", "scandinavia"]),
    ("great_britain", _EU, ["iceland", "scandinavia", "northern_europe", "western_europe"]),
    ("scandinavia", _EU, ["iceland", "great_britain", "northern_europe", "ukraine"]),
    ("northern_europe", _EU, ["great_britain", "scandinavia", "ukraine", "western_europe", "southern_europe"]),
    ("western_europe", _EU, ["great_britain", "northern_europe", "southern_europe", "north_africa"]),
    (
        "southern_europe",
        _EU,
        ["northern_europe", "western_europe", "ukraine", "north_africa", "egypt", "middle_east"],
    ),
    ("ukraine", _EU, ["scandinavia", "northern_europe", "southern_europe", "ural", "afghanistan", "middle_east"]),
    # Africa (6)
    (
        "north_africa",
        _AF,
        ["brazil", "western_europe", "southern_europe", "egypt", "east_africa", "central_africa"],
    ),
    ("egypt", _AF, ["southern_europe", "north_africa", "east_africa", "middle_east"]),
    (
        "east_africa",
        _AF,
        ["north_africa", "egypt", "central_africa", "south_africa", "madagascar", "middle_east"],
    ),
    ("central_africa", _AF, ["north_africa", "east_africa", "south_africa"]),
    ("south_africa", _AF, ["central_africa", "east_africa", "madagascar"]),
    ("madagascar", _AF, ["east_africa", "south_africa"]),
    # Asia (12)
    ("ural", _AS, ["ukraine", "siberia", "afghanistan", "china"]),
    ("siberia", _AS, ["ural", "yakutsk", "irkutsk", "mongolia", "china"]),
    ("yakutsk", _AS, ["siberia", "kamchatka", "irkutsk"]),
    ("kamchatka", _AS, ["yakutsk", "irkutsk", "mongolia", "japan", "alaska"]),
    ("irkutsk", _AS, ["siberia", "yakutsk", "kamchatka", "mongolia"]),
    ("mongolia", _AS, ["siberia", "irkutsk", "kamchatka", "japan", "china"]),
    ("japan", _AS, ["kamchatka", "mongolia"]),
    ("afghanistan", _AS, ["ukraine", "ural", "china", "india", "middle_east"]),
    ("china", _AS, ["ural", "siberia", "mongolia", "afghanistan", "india", "southeast_asia"]),
    ("middle_east", _AS, ["southern_europe", "ukraine", "afghanistan", "india", "egypt", "east_africa"]),
    ("india", _AS, ["afghanistan", "china", "middle_east", "southeast_asia"]),
    ("southeast_asia", _AS, ["china", "india", "indonesia"]),
    # Oceania (4)
    ("indonesia", _OC, ["southeast_asia", "new_guinea", "western_australia"]),
    ("new_guinea", _OC, ["indonesia", "western_australia", "eastern_australia"]),
    ("western_australia", _OC, ["indonesia", "new_guinea", "eastern_australia"]),
    ("eastern_australia", _OC, ["new_guinea", "western_australia"]),
]

# ---------------------------------------------------------------------------
# Continent definitions
# ---------------------------------------------------------------------------
_CONTINENT_DATA: list[tuple[str, int, list[str]]] = [
    (
        _NA,
        5,
        [
            "alaska",
            "northwest_territory",
            "greenland",
            "alberta",
            "ontario",
            "quebec",
            "western_united_states",
            "eastern_united_states",
            "central_america",
        ],
    ),
    (_SA, 2, ["venezuela", "peru", "brazil", "argentina"]),
    (
        _EU,
        5,
        ["iceland", "great_britain", "scandinavia", "northern_europe", "western_europe", "southern_europe", "ukraine"],
    ),
    (_AF, 3, ["north_africa", "egypt", "east_africa", "central_africa", "south_africa", "madagascar"]),
    (
        _AS,
        7,
        [
            "ural",
            "siberia",
            "yakutsk",
            "kamchatka",
            "irkutsk",
            "mongolia",
            "japan",
            "afghanistan",
            "china",
            "middle_east",
            "india",
            "southeast_asia",
        ],
    ),
    (_OC, 2, ["indonesia", "new_guinea", "western_australia", "eastern_australia"]),
]


class Board:
    """Immutable board loaded once at startup."""

    def __init__(self) -> None:
        self.territories: dict[str, Territory] = {}
        for name, continent, adjacent in _TERRITORY_DATA:
            self.territories[name] = Territory(name=name, continent=continent, adjacent=tuple(adjacent))

        self.continents: dict[str, Continent] = {}
        for name, bonus, terrs in _CONTINENT_DATA:
            self.continents[name] = Continent(name=name, bonus=bonus, territories=tuple(terrs))

        # quick-lookup: territory -> continent
        self._territory_continent: dict[str, str] = {t.name: t.continent for t in self.territories.values()}

    # ------------------------------------------------------------------
    def are_adjacent(self, a: str, b: str) -> bool:
        return b in self.territories[a].adjacent

    def continent_of(self, territory: str) -> str:
        return self._territory_continent[territory]

    def continent_bonus(self, continent: str) -> int:
        return self.continents[continent].bonus

    def territories_of(self, continent: str) -> tuple[str, ...]:
        return self.continents[continent].territories

    def all_territory_names(self) -> list[str]:
        return list(self.territories.keys())

    def continent_controller(self, owner_map: dict[str, str]) -> dict[str, str | None]:
        """Return {continent: player} if a player controls the whole continent, else None."""
        result: dict[str, str | None] = {}
        for cname, cont in self.continents.items():
            owners = {owner_map[t] for t in cont.territories if t in owner_map}
            result[cname] = owners.pop() if len(owners) == 1 else None
        return result


# Module-level singleton
BOARD = Board()
