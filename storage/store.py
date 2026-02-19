"""JSON file helpers — read/write for agents, games, and leaderboard."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from storage.models import AgentRecord, GameRecord, Leaderboard

DATA_DIR = Path("data")
AGENTS_FILE = DATA_DIR / "agents.json"
GAMES_DIR = DATA_DIR / "games"
LEADERBOARD_FILE = DATA_DIR / "leaderboard.json"


def _ensure_dirs() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    GAMES_DIR.mkdir(exist_ok=True)


def _load_json(path: Path) -> Any:
    if not path.exists():
        return None
    with path.open() as f:
        return json.load(f)


def _save_json(path: Path, data: Any) -> None:
    _ensure_dirs()
    with path.open("w") as f:
        json.dump(data, f, indent=2, default=str)


# ---------------------------------------------------------------------------
# Agent registry
# ---------------------------------------------------------------------------
_agents_adapter: TypeAdapter[list[AgentRecord]] = TypeAdapter(list[AgentRecord])


def load_agents() -> list[AgentRecord]:
    data = _load_json(AGENTS_FILE)
    if data is None:
        return []
    return _agents_adapter.validate_python(data)


def save_agents(agents: list[AgentRecord]) -> None:
    _ensure_dirs()
    _save_json(AGENTS_FILE, [a.model_dump() for a in agents])


def get_agent(agent_id: str) -> AgentRecord | None:
    for a in load_agents():
        if a.id == agent_id:
            return a
    return None


def upsert_agent(agent: AgentRecord) -> None:
    agents = load_agents()
    for i, a in enumerate(agents):
        if a.id == agent.id:
            agents[i] = agent
            save_agents(agents)
            return
    agents.append(agent)
    save_agents(agents)


# ---------------------------------------------------------------------------
# Game records
# ---------------------------------------------------------------------------
def save_game(game: GameRecord) -> None:
    _ensure_dirs()
    path = GAMES_DIR / f"{game.id}.json"
    _save_json(path, game.model_dump())


def load_game(game_id: str) -> GameRecord | None:
    path = GAMES_DIR / f"{game_id}.json"
    data = _load_json(path)
    if data is None:
        return None
    return GameRecord.model_validate(data)


def list_games(limit: int = 50) -> list[GameRecord]:
    """Load recent games sorted by started_at descending."""
    _ensure_dirs()
    games = []
    paths = sorted(GAMES_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in paths[:limit]:
        data = _load_json(path)
        if data:
            try:
                games.append(GameRecord.model_validate(data))
            except Exception:
                continue
    return games


# ---------------------------------------------------------------------------
# Leaderboard
# ---------------------------------------------------------------------------
def save_leaderboard(lb: Leaderboard) -> None:
    _ensure_dirs()
    _save_json(LEADERBOARD_FILE, lb.model_dump())


def load_leaderboard() -> Leaderboard | None:
    data = _load_json(LEADERBOARD_FILE)
    if data is None:
        return None
    return Leaderboard.model_validate(data)


def rebuild_leaderboard() -> Leaderboard:
    """Rebuild leaderboard from agents.json and game history."""
    from storage.models import Leaderboard, LeaderboardEntry

    agents = load_agents()
    games = list_games(limit=1000)

    # Compute avg game length per agent
    agent_game_lengths: dict[str, list[int]] = {a.id: [] for a in agents}
    for game in games:
        for agent_id in game.agents:
            if agent_id in agent_game_lengths:
                agent_game_lengths[agent_id].append(game.turn_count)

    entries = []
    for rank, agent in enumerate(sorted(agents, key=lambda a: a.elo, reverse=True), start=1):
        lengths = agent_game_lengths.get(agent.id, [])
        avg_len = sum(lengths) / len(lengths) if lengths else 0.0
        entries.append(
            LeaderboardEntry(
                rank=rank,
                agent_id=agent.id,
                name=agent.name,
                model=agent.model,
                provider=agent.provider,
                elo=round(agent.elo, 1),
                wins=agent.wins,
                losses=agent.losses,
                games_played=agent.games_played,
                win_rate=round(agent.win_rate, 3),
                avg_game_length=round(avg_len, 1),
            )
        )

    lb = Leaderboard(generated_at=datetime.utcnow(), entries=entries)
    save_leaderboard(lb)
    return lb
