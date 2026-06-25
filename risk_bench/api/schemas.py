from datetime import datetime

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
    max_turns: int | None = None
    parallel_games: int = Field(default=1, ge=1, le=20)


class NewGameResponse(BaseModel):
    """The launched game or tournament id; games are watchable at /games/{id}."""

    id: str
    tournament: bool


class EvaluateRequest(BaseModel):
    """A request to run the whole-game evaluator panel over a stored game."""

    evaluators: list[str] = Field(default_factory=list)


class ModelCatalogResponse(BaseModel):
    """The choices the setup form can offer: a model per provider/tier, and the reasoning efforts."""

    tiers_by_provider: dict[str, dict[str, str]]
    efforts: list[str]
    baseline: str
