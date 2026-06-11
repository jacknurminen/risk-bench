from datetime import datetime
from pathlib import Path

from pydantic import BaseModel


class GameResult(BaseModel):
    """Summary of one finished game, suitable for the tournament report."""

    seed: int
    player_names: list[str]
    missions: dict[str, str]
    winner: str | None
    turn_count: int
    invalid_action_counts: dict[str, int]
    chat_message_counts: dict[str, int]
    journal_path: str | None


class AgentStanding(BaseModel):
    """One agent's aggregate performance across the tournament."""

    name: str
    rating: float
    games_played: int
    wins: int
    draws: int
    invalid_actions: int


class TournamentReport(BaseModel):
    """The full outcome of a tournament run — the system's only file-output boundary."""

    created_at: datetime
    games: int
    players_per_game: int
    k_factor: float
    base_seed: int
    max_turns: int
    allow_private_messages: bool
    max_negotiation_rounds: int
    standings: list[AgentStanding]
    results: list[GameResult]


def write_report(report: TournamentReport, path: Path) -> None:
    """Serialize the report as JSON to the given path, creating parent directories.

    Args:
        report: The finished tournament report.
        path: Destination file path.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
