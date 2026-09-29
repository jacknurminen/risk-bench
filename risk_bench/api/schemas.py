from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class GameSummary(BaseModel):
    """One journal's listing entry: identity, players, progress, and API spend so far.

    ``turn_count`` is the latest turn reached (final when finished). Token and
    cost totals come from the game's trace; all None when there is no trace.
    """

    id: str
    players: list[str]
    seed: int
    max_turns: int
    has_commentary: bool
    has_evaluation: bool
    finished: bool
    running: bool
    winner: str | None
    turn_count: int | None
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None
    modified_at: datetime
    tournament_id: str | None


class TournamentSummary(BaseModel):
    """One tournament's listing entry; its games appear in the games list with this id."""

    id: str
    players: list[str]
    game_ids: list[str]
    created_at: datetime


class GameListResponse(BaseModel):
    """All journals in the games directory, newest first, with their tournament groupings."""

    games: list[GameSummary]
    tournaments: list[TournamentSummary]


class NewGameRequest(BaseModel):
    """A request to launch a game: player specs plus optional knobs.

    ``parallel_games`` above 1 turns the launch into a tournament: that many
    games with the same setup run in parallel on consecutive seeds.
    """

    players: list[str]
    seed: int = 0
    commentators: list[str] = Field(default_factory=list)
    evaluators: list[str] = Field(default_factory=list)
    max_turns: int | None = None
    parallel_games: int = Field(default=1, ge=1, le=20)
    # extra information the players start the game knowing: one line every player is given, plus
    # an optional per-player line aligned with ``players``
    common_context: str = ""
    player_context: list[str] = Field(default_factory=list)


class NewGameResponse(BaseModel):
    """The launched game or tournament id; games are watchable at /games/{id}."""

    id: str
    tournament: bool


class EvaluateRequest(BaseModel):
    """A panel run, optionally reviewing each completed round during play."""

    evaluators: list[str] = Field(default_factory=list)
    live: bool = False


class UnitVerificationBody(BaseModel):
    """A human review of one research unit within a selected evaluation run."""

    evaluator_index: int
    unit_type: Literal["agreement", "episode", "claim", "statement", "commitment"]
    unit_id: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    verified: bool
    version: str | None = None


class CatalogModel(BaseModel):
    """One selectable model, with the prices the setup form shows beside it."""

    id: str
    input_usd_per_million: float
    output_usd_per_million: float


class ModelCatalogResponse(BaseModel):
    """The choices the setup form can offer: the models each provider serves, and the efforts."""

    models_by_provider: dict[str, list[CatalogModel]]
    efforts: list[str]
    baseline: str


class ForkRequest(BaseModel):
    cursor: str
    instructions: dict[str, str] = Field(default_factory=dict)
    controllers: dict[str, str] = Field(default_factory=dict)
    redecide: bool = False
    action: dict | None = None
    message: dict | None = None
    context: dict[str, str] = Field(default_factory=dict)
    continuation_seed: int | None = None
    start: bool = True


class PlayerContextRequest(BaseModel):
    context: dict[str, str] = Field(default_factory=dict)
    # a fork adds to what a player already knows; an exact edit replaces it
    append: bool = False
