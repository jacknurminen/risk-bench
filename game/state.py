from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from game.board import BOARD


class Phase(StrEnum):
    SETUP = "setup"
    REINFORCE = "reinforce"
    ATTACK = "attack"
    FORTIFY = "fortify"
    GAME_OVER = "game_over"


@dataclass
class TerritoryState:
    owner: str
    troops: int

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dictionary.

        Returns:
            Dict with 'owner' (str) and 'troops' (int) keys.
        """
        return {"owner": self.owner, "troops": self.troops}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TerritoryState:
        """Deserialize from a JSON-compatible dictionary.

        Args:
            data: Dict with 'owner' and 'troops' keys.

        Returns:
            New TerritoryState instance.
        """
        return cls(owner=data["owner"], troops=data["troops"])


@dataclass
class PlayerInfo:
    """Public info visible to all agents."""

    color: str
    territories: int
    total_troops: int
    cards_in_hand: int
    alive: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dictionary.

        Returns:
            Dict with territory count, troop count, card count, and alive status.
        """
        return {
            "territories": self.territories,
            "total_troops": self.total_troops,
            "cards_in_hand": self.cards_in_hand,
            "alive": self.alive,
        }


@dataclass
class ChatEntry:
    turn: int
    event: str  # e.g. "turn_start", "attack_result", "reaction_window"
    from_color: str
    text: str

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dictionary.

        Returns:
            Dict with turn, event, from (color), and text keys.
        """
        return {"turn": self.turn, "event": self.event, "from": self.from_color, "text": self.text}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChatEntry:
        """Deserialize from a JSON-compatible dictionary.

        Args:
            data: Dict with turn, event, from, and text keys.

        Returns:
            New ChatEntry instance.
        """
        return cls(turn=data["turn"], event=data["event"], from_color=data["from"], text=data["text"])


@dataclass
class TurnRecord:
    number: int
    player: str
    moves: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class GameState:
    game_id: str
    turn: int
    phase: Phase
    current_player: str
    players: list[str]  # ordered list of colors (turn order)

    # territory_name -> TerritoryState
    territories: dict[str, TerritoryState] = field(default_factory=dict)

    # player_color -> list of card strings e.g. "infantry:alaska"
    hands: dict[str, list[str]] = field(default_factory=dict)

    # player_color -> mission string (secret)
    missions: dict[str, str] = field(default_factory=dict)

    # append-only chat log
    chat_log: list[ChatEntry] = field(default_factory=list)

    # who won (None until game over)
    winner: str | None = None
    win_reason: str | None = None

    # card set bonus escalation counter
    card_set_count: int = 0

    # troops earned this turn (to enforce placement)
    troops_to_place: int = 0

    # whether the current player has captured a territory this turn (earns card)
    captured_this_turn: bool = False

    # turn history for storage
    turn_records: list[TurnRecord] = field(default_factory=list)

    # ---------------------------------------------------------------------------
    def player_info(self, color: str) -> PlayerInfo:
        """Return the public player information for the given color.

        Args:
            color: Player color to query.

        Returns:
            PlayerInfo with territory count, troop total, card count, and alive status.
        """
        owned = [name for name, territory_state in self.territories.items() if territory_state.owner == color]
        total_troops = sum(
            territory_state.troops for territory_state in self.territories.values() if territory_state.owner == color
        )
        return PlayerInfo(
            color=color,
            territories=len(owned),
            total_troops=total_troops,
            cards_in_hand=len(self.hands.get(color, [])),
            alive=len(owned) > 0,
        )

    def territories_of(self, player: str) -> list[str]:
        """Return the territory names owned by the given player.

        Args:
            player: Player color to query.

        Returns:
            List of territory name strings owned by the player.
        """
        return [name for name, territory_state in self.territories.items() if territory_state.owner == player]

    def alive_players(self) -> list[str]:
        """Return the colors of all players who still own at least one territory.

        Returns:
            List of player color strings who are still in the game.
        """
        return [player for player in self.players if self.player_info(color=player).alive]

    def to_agent_view(self, color: str) -> dict[str, Any]:
        """Serialize state as seen by the given agent.

        Args:
            color: The observing player's color; determines which hand and mission are revealed.

        Returns:
            JSON-compatible dict with board state, player summaries, and the agent's private data.
        """
        territory_view: dict[str, Any] = {}
        for name, territory_state in self.territories.items():
            territory_view[name] = {
                "owner": territory_state.owner,
                "troops": territory_state.troops,
                "adjacent": list(BOARD.territories[name].adjacent),
            }

        continent_bonuses = {continent: cont.bonus for continent, cont in BOARD.continents.items()}
        players_view = {player: self.player_info(color=player).to_dict() for player in self.players}

        return {
            "game_id": self.game_id,
            "turn": self.turn,
            "phase": str(self.phase),
            "your_color": color,
            "your_mission": self.missions.get(color, ""),
            "current_player": self.current_player,
            "territories": territory_view,
            "continent_bonuses": continent_bonuses,
            "your_cards": self.hands.get(color, []),
            "players": players_view,
            "chat_log": [entry.to_dict() for entry in self.chat_log[-40:]],  # last 40 messages
        }
