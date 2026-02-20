from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class AgentRecord(BaseModel):
    id: str
    name: str
    model: str
    provider: str
    elo: float = 1000.0
    wins: int = 0
    losses: int = 0
    draws: int = 0
    games_played: int = 0

    @property
    def win_rate(self) -> float:
        if self.games_played == 0:
            return 0.0
        return self.wins / self.games_played


class MoveRecord(BaseModel):
    turn: int
    player: str
    phase: str
    move: dict[str, Any]


class ChatRecord(BaseModel):
    turn: int
    event: str
    from_color: str = Field(alias="from")
    text: str

    model_config = {"populate_by_name": True}


class GameRecord(BaseModel):
    id: str
    agents: list[str]  # agent IDs in turn order
    colors: list[str]  # color per agent (parallel)
    started_at: datetime
    finished_at: datetime | None = None
    winner_color: str | None = None
    winner_agent_id: str | None = None
    turn_count: int = 0
    win_reason: str | None = None
    moves: list[MoveRecord] = Field(default_factory=list)
    chat_log: list[ChatRecord] = Field(default_factory=list)


class LeaderboardEntry(BaseModel):
    rank: int
    agent_id: str
    name: str
    model: str
    provider: str
    elo: float
    wins: int
    losses: int
    games_played: int
    win_rate: float
    avg_game_length: float = 0.0


class Leaderboard(BaseModel):
    generated_at: datetime
    entries: list[LeaderboardEntry]
