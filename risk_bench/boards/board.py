from __future__ import annotations

from boards.types import Continent, ContinentInfo, Territory, TerritoryNode


class Board:
    def __init__(self, territory_data: list[TerritoryNode], continent_data: list[ContinentInfo]) -> None:
        self.territories: dict[Territory, TerritoryNode] = {node.name: node for node in territory_data}
        self.continents: dict[Continent, ContinentInfo] = {info.name: info for info in continent_data}
        self._territory_continent: dict[Territory, Continent] = {node.name: node.continent for node in territory_data}

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
