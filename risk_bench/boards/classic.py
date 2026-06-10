from __future__ import annotations

from boards.types import Continent, ContinentInfo, Territory, TerritoryNode

# ---------------------------------------------------------------------------
# Short aliases for the data tables below (class references, not magic strings)
# ---------------------------------------------------------------------------
_T = Territory
_C = Continent

# ---------------------------------------------------------------------------
# Territory definitions (42 canonical Risk territories)
# ---------------------------------------------------------------------------
TERRITORY_DATA: list[TerritoryNode] = [
    # North America (9)
    TerritoryNode(
        name=_T.ALASKA,
        continent=_C.NORTH_AMERICA,
        adjacent=(_T.NORTHWEST_TERRITORY, _T.ALBERTA, _T.KAMCHATKA),
    ),
    TerritoryNode(
        name=_T.NORTHWEST_TERRITORY,
        continent=_C.NORTH_AMERICA,
        adjacent=(_T.ALASKA, _T.ALBERTA, _T.ONTARIO, _T.GREENLAND),
    ),
    TerritoryNode(
        name=_T.GREENLAND,
        continent=_C.NORTH_AMERICA,
        adjacent=(_T.NORTHWEST_TERRITORY, _T.ONTARIO, _T.QUEBEC, _T.ICELAND),
    ),
    TerritoryNode(
        name=_T.ALBERTA,
        continent=_C.NORTH_AMERICA,
        adjacent=(_T.ALASKA, _T.NORTHWEST_TERRITORY, _T.ONTARIO, _T.WESTERN_UNITED_STATES),
    ),
    TerritoryNode(
        name=_T.ONTARIO,
        continent=_C.NORTH_AMERICA,
        adjacent=(
            _T.NORTHWEST_TERRITORY,
            _T.ALBERTA,
            _T.GREENLAND,
            _T.QUEBEC,
            _T.WESTERN_UNITED_STATES,
            _T.EASTERN_UNITED_STATES,
        ),
    ),
    TerritoryNode(
        name=_T.QUEBEC,
        continent=_C.NORTH_AMERICA,
        adjacent=(_T.GREENLAND, _T.ONTARIO, _T.EASTERN_UNITED_STATES),
    ),
    TerritoryNode(
        name=_T.WESTERN_UNITED_STATES,
        continent=_C.NORTH_AMERICA,
        adjacent=(_T.ALBERTA, _T.ONTARIO, _T.EASTERN_UNITED_STATES, _T.CENTRAL_AMERICA),
    ),
    TerritoryNode(
        name=_T.EASTERN_UNITED_STATES,
        continent=_C.NORTH_AMERICA,
        adjacent=(_T.ONTARIO, _T.QUEBEC, _T.WESTERN_UNITED_STATES, _T.CENTRAL_AMERICA),
    ),
    TerritoryNode(
        name=_T.CENTRAL_AMERICA,
        continent=_C.NORTH_AMERICA,
        adjacent=(_T.WESTERN_UNITED_STATES, _T.EASTERN_UNITED_STATES, _T.VENEZUELA),
    ),
    # South America (4)
    TerritoryNode(
        name=_T.VENEZUELA,
        continent=_C.SOUTH_AMERICA,
        adjacent=(_T.CENTRAL_AMERICA, _T.PERU, _T.BRAZIL),
    ),
    TerritoryNode(
        name=_T.PERU,
        continent=_C.SOUTH_AMERICA,
        adjacent=(_T.VENEZUELA, _T.BRAZIL, _T.ARGENTINA),
    ),
    TerritoryNode(
        name=_T.BRAZIL,
        continent=_C.SOUTH_AMERICA,
        adjacent=(_T.VENEZUELA, _T.PERU, _T.ARGENTINA, _T.NORTH_AFRICA),
    ),
    TerritoryNode(
        name=_T.ARGENTINA,
        continent=_C.SOUTH_AMERICA,
        adjacent=(_T.PERU, _T.BRAZIL),
    ),
    # Europe (7)
    TerritoryNode(
        name=_T.ICELAND,
        continent=_C.EUROPE,
        adjacent=(_T.GREENLAND, _T.GREAT_BRITAIN, _T.SCANDINAVIA),
    ),
    TerritoryNode(
        name=_T.GREAT_BRITAIN,
        continent=_C.EUROPE,
        adjacent=(_T.ICELAND, _T.SCANDINAVIA, _T.NORTHERN_EUROPE, _T.WESTERN_EUROPE),
    ),
    TerritoryNode(
        name=_T.SCANDINAVIA,
        continent=_C.EUROPE,
        adjacent=(_T.ICELAND, _T.GREAT_BRITAIN, _T.NORTHERN_EUROPE, _T.UKRAINE),
    ),
    TerritoryNode(
        name=_T.NORTHERN_EUROPE,
        continent=_C.EUROPE,
        adjacent=(_T.GREAT_BRITAIN, _T.SCANDINAVIA, _T.UKRAINE, _T.WESTERN_EUROPE, _T.SOUTHERN_EUROPE),
    ),
    TerritoryNode(
        name=_T.WESTERN_EUROPE,
        continent=_C.EUROPE,
        adjacent=(_T.GREAT_BRITAIN, _T.NORTHERN_EUROPE, _T.SOUTHERN_EUROPE, _T.NORTH_AFRICA),
    ),
    TerritoryNode(
        name=_T.SOUTHERN_EUROPE,
        continent=_C.EUROPE,
        adjacent=(
            _T.NORTHERN_EUROPE,
            _T.WESTERN_EUROPE,
            _T.UKRAINE,
            _T.NORTH_AFRICA,
            _T.EGYPT,
            _T.MIDDLE_EAST,
        ),
    ),
    TerritoryNode(
        name=_T.UKRAINE,
        continent=_C.EUROPE,
        adjacent=(
            _T.SCANDINAVIA,
            _T.NORTHERN_EUROPE,
            _T.SOUTHERN_EUROPE,
            _T.URAL,
            _T.AFGHANISTAN,
            _T.MIDDLE_EAST,
        ),
    ),
    # Africa (6)
    TerritoryNode(
        name=_T.NORTH_AFRICA,
        continent=_C.AFRICA,
        adjacent=(
            _T.BRAZIL,
            _T.WESTERN_EUROPE,
            _T.SOUTHERN_EUROPE,
            _T.EGYPT,
            _T.EAST_AFRICA,
            _T.CENTRAL_AFRICA,
        ),
    ),
    TerritoryNode(
        name=_T.EGYPT,
        continent=_C.AFRICA,
        adjacent=(_T.SOUTHERN_EUROPE, _T.NORTH_AFRICA, _T.EAST_AFRICA, _T.MIDDLE_EAST),
    ),
    TerritoryNode(
        name=_T.EAST_AFRICA,
        continent=_C.AFRICA,
        adjacent=(_T.NORTH_AFRICA, _T.EGYPT, _T.CENTRAL_AFRICA, _T.SOUTH_AFRICA, _T.MADAGASCAR, _T.MIDDLE_EAST),
    ),
    TerritoryNode(
        name=_T.CENTRAL_AFRICA,
        continent=_C.AFRICA,
        adjacent=(_T.NORTH_AFRICA, _T.EAST_AFRICA, _T.SOUTH_AFRICA),
    ),
    TerritoryNode(
        name=_T.SOUTH_AFRICA,
        continent=_C.AFRICA,
        adjacent=(_T.CENTRAL_AFRICA, _T.EAST_AFRICA, _T.MADAGASCAR),
    ),
    TerritoryNode(
        name=_T.MADAGASCAR,
        continent=_C.AFRICA,
        adjacent=(_T.EAST_AFRICA, _T.SOUTH_AFRICA),
    ),
    # Asia (12)
    TerritoryNode(
        name=_T.URAL,
        continent=_C.ASIA,
        adjacent=(_T.UKRAINE, _T.SIBERIA, _T.AFGHANISTAN, _T.CHINA),
    ),
    TerritoryNode(
        name=_T.SIBERIA,
        continent=_C.ASIA,
        adjacent=(_T.URAL, _T.YAKUTSK, _T.IRKUTSK, _T.MONGOLIA, _T.CHINA),
    ),
    TerritoryNode(
        name=_T.YAKUTSK,
        continent=_C.ASIA,
        adjacent=(_T.SIBERIA, _T.KAMCHATKA, _T.IRKUTSK),
    ),
    TerritoryNode(
        name=_T.KAMCHATKA,
        continent=_C.ASIA,
        adjacent=(_T.YAKUTSK, _T.IRKUTSK, _T.MONGOLIA, _T.JAPAN, _T.ALASKA),
    ),
    TerritoryNode(
        name=_T.IRKUTSK,
        continent=_C.ASIA,
        adjacent=(_T.SIBERIA, _T.YAKUTSK, _T.KAMCHATKA, _T.MONGOLIA),
    ),
    TerritoryNode(
        name=_T.MONGOLIA,
        continent=_C.ASIA,
        adjacent=(_T.SIBERIA, _T.IRKUTSK, _T.KAMCHATKA, _T.JAPAN, _T.CHINA),
    ),
    TerritoryNode(
        name=_T.JAPAN,
        continent=_C.ASIA,
        adjacent=(_T.KAMCHATKA, _T.MONGOLIA),
    ),
    TerritoryNode(
        name=_T.AFGHANISTAN,
        continent=_C.ASIA,
        adjacent=(_T.UKRAINE, _T.URAL, _T.CHINA, _T.INDIA, _T.MIDDLE_EAST),
    ),
    TerritoryNode(
        name=_T.CHINA,
        continent=_C.ASIA,
        adjacent=(_T.URAL, _T.SIBERIA, _T.MONGOLIA, _T.AFGHANISTAN, _T.INDIA, _T.SOUTHEAST_ASIA),
    ),
    TerritoryNode(
        name=_T.MIDDLE_EAST,
        continent=_C.ASIA,
        adjacent=(_T.SOUTHERN_EUROPE, _T.UKRAINE, _T.AFGHANISTAN, _T.INDIA, _T.EGYPT, _T.EAST_AFRICA),
    ),
    TerritoryNode(
        name=_T.INDIA,
        continent=_C.ASIA,
        adjacent=(_T.AFGHANISTAN, _T.CHINA, _T.MIDDLE_EAST, _T.SOUTHEAST_ASIA),
    ),
    TerritoryNode(
        name=_T.SOUTHEAST_ASIA,
        continent=_C.ASIA,
        adjacent=(_T.CHINA, _T.INDIA, _T.INDONESIA),
    ),
    # Oceania (4)
    TerritoryNode(
        name=_T.INDONESIA,
        continent=_C.OCEANIA,
        adjacent=(_T.SOUTHEAST_ASIA, _T.NEW_GUINEA, _T.WESTERN_AUSTRALIA),
    ),
    TerritoryNode(
        name=_T.NEW_GUINEA,
        continent=_C.OCEANIA,
        adjacent=(_T.INDONESIA, _T.WESTERN_AUSTRALIA, _T.EASTERN_AUSTRALIA),
    ),
    TerritoryNode(
        name=_T.WESTERN_AUSTRALIA,
        continent=_C.OCEANIA,
        adjacent=(_T.INDONESIA, _T.NEW_GUINEA, _T.EASTERN_AUSTRALIA),
    ),
    TerritoryNode(
        name=_T.EASTERN_AUSTRALIA,
        continent=_C.OCEANIA,
        adjacent=(_T.NEW_GUINEA, _T.WESTERN_AUSTRALIA),
    ),
]

# ---------------------------------------------------------------------------
# Continent definitions
# ---------------------------------------------------------------------------
CONTINENT_DATA: list[ContinentInfo] = [
    ContinentInfo(
        name=_C.NORTH_AMERICA,
        bonus=5,
        territories=(
            _T.ALASKA,
            _T.NORTHWEST_TERRITORY,
            _T.GREENLAND,
            _T.ALBERTA,
            _T.ONTARIO,
            _T.QUEBEC,
            _T.WESTERN_UNITED_STATES,
            _T.EASTERN_UNITED_STATES,
            _T.CENTRAL_AMERICA,
        ),
    ),
    ContinentInfo(
        name=_C.SOUTH_AMERICA,
        bonus=2,
        territories=(_T.VENEZUELA, _T.PERU, _T.BRAZIL, _T.ARGENTINA),
    ),
    ContinentInfo(
        name=_C.EUROPE,
        bonus=5,
        territories=(
            _T.ICELAND,
            _T.GREAT_BRITAIN,
            _T.SCANDINAVIA,
            _T.NORTHERN_EUROPE,
            _T.WESTERN_EUROPE,
            _T.SOUTHERN_EUROPE,
            _T.UKRAINE,
        ),
    ),
    ContinentInfo(
        name=_C.AFRICA,
        bonus=3,
        territories=(
            _T.NORTH_AFRICA,
            _T.EGYPT,
            _T.EAST_AFRICA,
            _T.CENTRAL_AFRICA,
            _T.SOUTH_AFRICA,
            _T.MADAGASCAR,
        ),
    ),
    ContinentInfo(
        name=_C.ASIA,
        bonus=7,
        territories=(
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
        ),
    ),
    ContinentInfo(
        name=_C.OCEANIA,
        bonus=2,
        territories=(_T.INDONESIA, _T.NEW_GUINEA, _T.WESTERN_AUSTRALIA, _T.EASTERN_AUSTRALIA),
    ),
]
