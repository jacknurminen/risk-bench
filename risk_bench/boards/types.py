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
