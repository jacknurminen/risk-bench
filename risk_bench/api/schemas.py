from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, StrictInt, model_validator

from risk_bench.players.profiles import ExperimentPlayer


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
    queued: bool = False
    started: bool = True
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
    parallel_games: int = 1


class GameListResponse(BaseModel):
    """All journals in the games directory, newest first, with their tournament groupings."""

    games: list[GameSummary]
    tournaments: list[TournamentSummary]


class NewGameRequest(BaseModel):
    """A request to launch a game: player specs plus optional knobs.

    ``games`` sets the total tournament size; ``parallel_games`` bounds how many
    game workers can run at once, on consecutive seeds.
    """

    players: list[str]
    seed: int = 0
    commentators: list[str] = Field(default_factory=list)
    evaluators: list[str] = Field(default_factory=list)
    evaluation_method: Literal["live_rounds", "whole_game"] = "live_rounds"
    max_turns: int | None = None
    games: int = Field(default=1, ge=1, le=1000)
    parallel_games: int = Field(default=1, ge=1, le=20)
    seating_mode: Literal["random", "cycle"] = "random"
    seating_orders: list[list[StrictInt]] | None = Field(
        default=None,
        description="Resolved orders saved for queue resumption; generated from seating_mode at launch.",
    )
    # extra information the players start the game knowing: one line every player is given, plus
    # an optional per-player line aligned with ``players``
    common_context: str = ""
    player_context: list[str] = Field(default_factory=list)
    experiment_player: ExperimentPlayer | None = None

    @model_validator(mode="after")
    def validate_seating_orders(self):
        if self.experiment_player is not None:
            position = self.experiment_player.player
            if position > len(self.players) or self.players[position - 1] in {"random", "human"}:
                raise ValueError("The experiment player must select a model-backed player in the roster.")
        if self.seating_orders is not None:
            if len(self.seating_orders) != self.games:
                raise ValueError("Provide exactly one seating order per game.")
            expected = list(range(1, len(self.players) + 1))
            if any(sorted(order) != expected for order in self.seating_orders):
                raise ValueError("Each seating order must contain every player number exactly once.")
        return self


class NewGameResponse(BaseModel):
    """The launched game or tournament id; games are watchable at /games/{id}."""

    id: str
    tournament: bool


class ContinueTournamentRequest(BaseModel):
    """Append games using the tournament's saved launch configuration."""

    games: int = Field(ge=1, le=1000, strict=True)
    expected_total_games: int = Field(ge=1, strict=True)


class EvaluateRequest(BaseModel):
    """A panel run, optionally reviewing each completed round during play."""

    evaluators: list[str] = Field(default_factory=list)
    live: bool = False
    restart: bool = False


class UnitVerificationBody(BaseModel):
    """A human review of one research unit within a selected evaluation run."""

    evaluator_index: int
    unit_type: Literal["agreement", "episode", "claim", "statement", "commitment", "deception", "deception_candidate"]
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
