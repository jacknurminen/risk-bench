"""GameState dataclass — fully serializable to/from JSON-compatible dicts."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


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
        return {"owner": self.owner, "troops": self.troops}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TerritoryState:
        return cls(owner=d["owner"], troops=d["troops"])


@dataclass
class PlayerInfo:
    """Public info visible to all agents."""

    color: str
    territories: int
    total_troops: int
    cards_in_hand: int
    alive: bool = True

    def to_dict(self) -> dict[str, Any]:
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
        return {"turn": self.turn, "event": self.event, "from": self.from_color, "text": self.text}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ChatEntry:
        return cls(turn=d["turn"], event=d["event"], from_color=d["from"], text=d["text"])


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
        owned = [n for n, ts in self.territories.items() if ts.owner == color]
        total_troops = sum(ts.troops for ts in self.territories.values() if ts.owner == color)
        return PlayerInfo(
            color=color,
            territories=len(owned),
            total_troops=total_troops,
            cards_in_hand=len(self.hands.get(color, [])),
            alive=len(owned) > 0,
        )

    def territories_of(self, color: str) -> list[str]:
        return [n for n, ts in self.territories.items() if ts.owner == color]

    def alive_players(self) -> list[str]:
        return [p for p in self.players if self.player_info(p).alive]

    def to_agent_view(self, color: str) -> dict[str, Any]:
        """Serialize state as seen by the given agent."""
        from game.board import BOARD

        terr_view: dict[str, Any] = {}
        for name, ts in self.territories.items():
            terr_view[name] = {
                "owner": ts.owner,
                "troops": ts.troops,
                "adjacent": list(BOARD.territories[name].adjacent),
            }

        cont_bonuses = {c: cont.bonus for c, cont in BOARD.continents.items()}
        players_view = {p: self.player_info(p).to_dict() for p in self.players}

        return {
            "game_id": self.game_id,
            "turn": self.turn,
            "phase": str(self.phase),
            "your_color": color,
            "your_mission": self.missions.get(color, ""),
            "current_player": self.current_player,
            "territories": terr_view,
            "continent_bonuses": cont_bonuses,
            "your_cards": self.hands.get(color, []),
            "players": players_view,
            "chat_log": [e.to_dict() for e in self.chat_log[-40:]],  # last 40 messages
        }
