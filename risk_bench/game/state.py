from dataclasses import dataclass

from risk_bench.game.board import BOARD
from risk_bench.game.cards import Deck, TerritoryCard
from risk_bench.game.missions import Mission
from risk_bench.game.types import Continent, Phase, PlayerId, Territory


@dataclass
class PlayerState:
    """One player's private state: their mission, hand of cards, and elimination status.

    ``eliminated_by`` is ``None`` while the player lives; who dealt the final blow
    matters because the eliminate-player mission only counts a kill by its holder.
    """

    player_id: PlayerId
    mission: Mission
    cards: list[TerritoryCard]
    eliminated_by: PlayerId | None


@dataclass
class GameState:
    """The complete state of a game in progress.

    ``players`` preserves turn order (dict insertion order). The query methods
    are strictly read-only derived values; all mutation goes through
    ``engine.apply_action``.
    """

    armies: dict[Territory, int]
    owner: dict[Territory, PlayerId]
    players: dict[PlayerId, PlayerState]
    deck: Deck
    phase: Phase
    current_player: PlayerId
    armies_to_place: int
    conquered_territory_this_turn: bool
    turn_number: int
    winner: PlayerId | None

    def territories_owned_by(self, player_id: PlayerId) -> frozenset[Territory]:
        """Return all territories currently owned by the given player.

        Args:
            player_id: The player to look up.

        Returns:
            The player's territories, empty if eliminated.
        """
        return frozenset(territory for territory, owner in self.owner.items() if owner == player_id)

    def continents_owned_by(self, player_id: PlayerId) -> frozenset[Continent]:
        """Return the continents the given player owns in full.

        Args:
            player_id: The player to look up.

        Returns:
            The fully-owned continents.
        """
        owned_territories = self.territories_owned_by(player_id=player_id)
        return frozenset(
            continent for continent in Continent if BOARD.continent_info(continent).territories <= owned_territories
        )

    def army_count(self, player_id: PlayerId) -> int:
        """Return the total armies the given player has on the board.

        Args:
            player_id: The player to look up.

        Returns:
            The sum of armies across the player's territories.
        """
        return sum(self.armies[territory] for territory in self.territories_owned_by(player_id=player_id))
