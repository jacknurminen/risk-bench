from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter
from storage.models import AgentRecord, GameRecord, Leaderboard, LeaderboardEntry

DATA_DIR = Path("data")
AGENTS_FILE = DATA_DIR / "agents.json"
GAMES_DIR = DATA_DIR / "games"
LEADERBOARD_FILE = DATA_DIR / "leaderboard.json"

_agents_adapter: TypeAdapter[list[AgentRecord]] = TypeAdapter(list[AgentRecord])


# ---------------------------------------------------------------------------
# Agent registry
# ---------------------------------------------------------------------------
def load_agents() -> list[AgentRecord]:
    """Load all registered agents from disk.

    Returns:
        List of AgentRecord objects, or an empty list if none are registered.
    """
    data = _load_json(path=AGENTS_FILE)
    if data is None:
        return []
    return _agents_adapter.validate_python(data)


def save_agents(agents: list[AgentRecord]) -> None:
    """Persist the full agent registry to disk, overwriting the existing file.

    Args:
        agents: Complete list of agent records to save.
    """
    _ensure_dirs()
    _save_json(path=AGENTS_FILE, data=[agent.model_dump() for agent in agents])


def get_agent(agent_id: str) -> AgentRecord | None:
    """Look up a single agent by its ID.

    Args:
        agent_id: The agent's unique identifier string.

    Returns:
        The matching AgentRecord, or None if not found.
    """
    for agent in load_agents():
        if agent.id == agent_id:
            return agent
    return None


def upsert_agent(agent: AgentRecord) -> None:
    """Insert or update an agent in the registry.

    If an agent with the same ID already exists it is replaced; otherwise the
    new record is appended.

    Args:
        agent: Agent record to insert or update.
    """
    agents = load_agents()
    for index, existing in enumerate(agents):
        if existing.id == agent.id:
            agents[index] = agent
            save_agents(agents=agents)
            return
    agents.append(agent)
    save_agents(agents=agents)


# ---------------------------------------------------------------------------
# Game records
# ---------------------------------------------------------------------------
def save_game(game: GameRecord) -> None:
    """Persist a game record to disk.

    Args:
        game: Game record to save; its ID is used as the filename.
    """
    _ensure_dirs()
    path = GAMES_DIR / f"{game.id}.json"
    _save_json(path=path, data=game.model_dump())


def load_game(game_id: str) -> GameRecord | None:
    """Load a single game record by its ID.

    Args:
        game_id: Unique game identifier.

    Returns:
        The matching GameRecord, or None if the file does not exist.
    """
    path = GAMES_DIR / f"{game_id}.json"
    data = _load_json(path=path)
    if data is None:
        return None
    return GameRecord.model_validate(data)


def list_games(limit: int) -> list[GameRecord]:
    """Load recent games sorted by modification time descending.

    Args:
        limit: Maximum number of game records to return.

    Returns:
        List of GameRecord objects, most recent first.
    """
    _ensure_dirs()
    games = []
    paths = sorted(GAMES_DIR.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    for path in paths[:limit]:
        data = _load_json(path=path)
        if data:
            try:
                games.append(GameRecord.model_validate(data))
            except Exception:
                continue
    return games


# ---------------------------------------------------------------------------
# Leaderboard
# ---------------------------------------------------------------------------
def save_leaderboard(leaderboard: Leaderboard) -> None:
    """Persist the leaderboard to disk.

    Args:
        leaderboard: Leaderboard object to save.
    """
    _ensure_dirs()
    _save_json(path=LEADERBOARD_FILE, data=leaderboard.model_dump())


def load_leaderboard() -> Leaderboard | None:
    """Load the leaderboard from disk.

    Returns:
        The current Leaderboard, or None if it has not been generated yet.
    """
    data = _load_json(path=LEADERBOARD_FILE)
    if data is None:
        return None
    return Leaderboard.model_validate(data)


def rebuild_leaderboard() -> Leaderboard:
    """Rebuild the leaderboard from agents.json and game history, then save it.

    Returns:
        The freshly computed Leaderboard.
    """
    agents = load_agents()
    games = list_games(limit=1000)

    # Compute avg game length per agent
    agent_game_lengths: dict[str, list[int]] = {agent.id: [] for agent in agents}
    for game in games:
        for agent_id in game.agents:
            if agent_id in agent_game_lengths:
                agent_game_lengths[agent_id].append(game.turn_count)

    entries = []
    for rank, agent in enumerate(sorted(agents, key=lambda agent: agent.elo, reverse=True), start=1):
        lengths = agent_game_lengths.get(agent.id, [])
        average_game_length = sum(lengths) / len(lengths) if lengths else 0.0
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
                avg_game_length=round(average_game_length, 1),
            )
        )

    leaderboard = Leaderboard(generated_at=datetime.utcnow(), entries=entries)
    save_leaderboard(leaderboard=leaderboard)
    return leaderboard


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _ensure_dirs() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    GAMES_DIR.mkdir(exist_ok=True)


def _load_json(path: Path) -> Any:
    if not path.exists():
        return None
    with path.open() as file:
        return json.load(file)


def _save_json(path: Path, data: Any) -> None:
    _ensure_dirs()
    with path.open("w") as file:
        json.dump(data, file, indent=2, default=str)
