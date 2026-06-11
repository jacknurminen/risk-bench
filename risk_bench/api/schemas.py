from datetime import datetime

from pydantic import BaseModel


class GameSummary(BaseModel):
    """One journal's listing entry: identity, players, and outcome so far."""

    id: str
    players: list[str]
    seed: int
    max_turns: int
    has_commentary: bool
    finished: bool
    winner: str | None
    turn_count: int | None
    modified_at: datetime


class GameListResponse(BaseModel):
    """All journals in the games directory, newest first."""

    games: list[GameSummary]


class NewGameRequest(BaseModel):
    """A request to launch a game: player specs plus optional knobs."""

    players: list[str]
    seed: int = 0
    commentator: str | None = None
    max_turns: int | None = None


class NewGameResponse(BaseModel):
    """The launched game's id, immediately watchable at /games/{id}."""

    id: str


class ModelCatalogResponse(BaseModel):
    """The player specs the setup form can offer."""

    tiers_by_provider: dict[str, dict[str, str]]
    baseline: str
