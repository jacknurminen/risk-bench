from enum import StrEnum

type PlayerId = str


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
    BRAZIL = "brazil"
    PERU = "peru"
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
    CONGO = "congo"
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
    MIDDLE_EAST = "middle_east"
    INDIA = "india"
    SIAM = "siam"
    CHINA = "china"
    # Oceania
    INDONESIA = "indonesia"
    NEW_GUINEA = "new_guinea"
    WESTERN_AUSTRALIA = "western_australia"
    EASTERN_AUSTRALIA = "eastern_australia"


class Phase(StrEnum):
    SETUP_PLACEMENT = "setup_placement"
    REINFORCE = "reinforce"
    ATTACK = "attack"
    FORTIFY = "fortify"
    GAME_OVER = "game_over"
