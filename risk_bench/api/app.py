import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from risk_bench import specs
from risk_bench.agents.base import ReasoningEffort
from risk_bench.agents.tracing import trace_path_for_journal, usage_by_caller
from risk_bench.api.processes import GameProcessManager
from risk_bench.api.schemas import (
    EvaluateRequest,
    GameListResponse,
    GameSummary,
    ModelCatalogResponse,
    NewGameRequest,
    NewGameResponse,
    TournamentSummary,
)
from risk_bench.config import load_settings
from risk_bench.game.engine import IllegalActionError
from risk_bench.runner.evaluation_store import evaluation_path_for_journal
from risk_bench.viewer.evaluation import build_evaluation_view_model
from risk_bench.viewer.html import (
    UI_DIRECTORY,
    render_evaluation_html,
    render_live_viewer_html,
    render_tournament_html,
    render_viewer_html,
)
from risk_bench.viewer.snapshots import build_view_model
from risk_bench.viewer.tournament import build_tournament_view_model

_GAME_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
_LAUNCH_TIMEOUT_SECONDS = 15.0


class _UsageTotals(NamedTuple):
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None


_NO_USAGE = _UsageTotals(input_tokens=None, output_tokens=None, cost_usd=None)


def create_app(games_directory: Path) -> FastAPI:
    """Create the web app: browse and watch games, and launch new ones.

    The journal files in the games directory are the only source of truth for
    game content; the app's only state is the process manager owning one
    daemonic worker per running game. Shutting the server down terminates the
    workers, which merely pauses their games — journals keep them resumable.

    Args:
        games_directory: Where game journals live and new ones are created.

    Returns:
        The configured FastAPI application.
    """
    app = FastAPI(title="risk-bench")
    manager = GameProcessManager()
    app.add_event_handler("shutdown", manager.shutdown)
    app.mount("/assets", StaticFiles(directory=UI_DIRECTORY), name="assets")
    # traces can be large and the list is polled; totals are recomputed only when a trace changes
    usage_totals_cache: dict[str, tuple[tuple[float, int], _UsageTotals]] = {}

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(UI_DIRECTORY / "index.html")

    @app.get("/games/{game_id}", include_in_schema=False)
    def game_page(game_id: str) -> HTMLResponse:
        view_model = _load_view_model(games_directory=games_directory, game_id=game_id)
        trace_url = f"/api/games/{game_id}/trace"
        if view_model["result"] is not None:
            return HTMLResponse(render_viewer_html(view_model=view_model, trace_url=trace_url))
        return HTMLResponse(
            render_live_viewer_html(view_model=view_model, poll_url=f"/api/games/{game_id}", trace_url=trace_url)
        )

    @app.get("/games/{game_id}/evaluation", include_in_schema=False)
    def evaluation_page(game_id: str) -> HTMLResponse:
        view_model = _load_evaluation_view_model(games_directory=games_directory, game_id=game_id, manager=manager)
        return HTMLResponse(
            render_evaluation_html(
                view_model=view_model,
                game_id=game_id,
                poll_url=f"/api/games/{game_id}/evaluation",
                evaluate_url=f"/api/games/{game_id}/evaluate",
            )
        )

    @app.get("/tournaments/{tournament_id}", include_in_schema=False)
    def tournament_page(tournament_id: str) -> HTMLResponse:
        view_model = _load_tournament_view_model(games_directory=games_directory, tournament_id=tournament_id)
        live = not all(game["finished"] for game in view_model["games"])
        return HTMLResponse(
            render_tournament_html(view_model=view_model, live=live, poll_url=f"/api/tournaments/{tournament_id}")
        )

    @app.get("/api/tournaments/{tournament_id}")
    def tournament_view_model(tournament_id: str) -> dict[str, Any]:
        return _load_tournament_view_model(games_directory=games_directory, tournament_id=tournament_id)

    @app.get("/api/games")
    def list_games() -> GameListResponse:
        games: list[GameSummary] = []
        tournaments: list[TournamentSummary] = []
        for entry in sorted(games_directory.iterdir(), reverse=True) if games_directory.is_dir() else []:
            if not entry.is_dir():
                continue
            manifest_path = entry / "tournament.json"
            if manifest_path.is_file():
                tournament = _tournament_summary(manifest_path=manifest_path)
                tournaments.append(tournament)
                member_directories = [entry / game_id for game_id in tournament.game_ids]
            else:
                member_directories = [entry]
            for game_directory in member_directories:
                summary = _summarize_game_directory(
                    game_directory=game_directory,
                    manager=manager,
                    tournament_id=tournament.id if manifest_path.is_file() else None,
                    usage_totals_cache=usage_totals_cache,
                )
                if summary is not None:
                    games.append(summary)
        return GameListResponse(games=games, tournaments=tournaments)

    @app.get("/api/games/{game_id}")
    def game_view_model(game_id: str) -> dict[str, Any]:
        return _load_view_model(games_directory=games_directory, game_id=game_id)

    @app.post("/api/games", status_code=201)
    def launch_game(request: NewGameRequest) -> NewGameResponse:
        return _launch_game(games_directory=games_directory, manager=manager, request=request)

    @app.post("/api/games/{game_id}/pause", status_code=204)
    def pause_game(game_id: str) -> None:
        _journal_path(games_directory=games_directory, game_id=game_id)
        if not manager.pause_game(game_id=game_id):
            raise HTTPException(status_code=409, detail="The game is not running.")

    @app.post("/api/games/{game_id}/resume", status_code=204)
    def resume_game(game_id: str) -> None:
        journal_path = _journal_path(games_directory=games_directory, game_id=game_id)
        _resume_game(games_directory=games_directory, manager=manager, game_id=game_id, journal_path=journal_path)

    @app.post("/api/games/{game_id}/evaluate", status_code=202)
    def evaluate_game(game_id: str, request: EvaluateRequest) -> dict[str, bool]:
        journal_path = _journal_path(games_directory=games_directory, game_id=game_id)
        _start_evaluation(manager=manager, game_id=game_id, journal_path=journal_path, request=request)
        return {"running": True}

    @app.get("/api/games/{game_id}/evaluation")
    def evaluation_view_model(game_id: str) -> dict[str, Any]:
        return _load_evaluation_view_model(games_directory=games_directory, game_id=game_id, manager=manager)

    @app.get("/api/games/{game_id}/trace")
    def trace_call_list(game_id: str) -> dict[str, Any]:
        journal_path = _journal_path(games_directory=games_directory, game_id=game_id)
        records = _read_trace_records(journal_path=journal_path)
        return {"calls": [_trace_call_summary(record=record) for record in records]}

    @app.get("/api/games/{game_id}/trace/{sequence_number}")
    def trace_call_detail(game_id: str, sequence_number: int) -> dict[str, Any]:
        journal_path = _journal_path(games_directory=games_directory, game_id=game_id)
        for record in _read_trace_records(journal_path=journal_path):
            if record.get("seq") == sequence_number:
                return record
        raise HTTPException(status_code=404, detail="No such trace record.")

    @app.get("/api/models")
    def model_catalog() -> ModelCatalogResponse:
        return ModelCatalogResponse(
            tiers_by_provider={
                provider: {tier.name.lower(): tier.value for tier in tier_models}
                for provider, tier_models in specs.TIER_MODELS_BY_PROVIDER.items()
            },
            efforts=[effort.value for effort in ReasoningEffort],
            baseline="random",
        )

    return app


def _journal_path(games_directory: Path, game_id: str) -> Path:
    if not _GAME_ID_PATTERN.match(game_id):
        raise HTTPException(status_code=404, detail="No such game.")
    # a game folder lives at the top level (standalone) or inside its tournament's folder
    candidate_directories = [games_directory / game_id, *games_directory.glob(f"*/{game_id}")]
    for game_directory in candidate_directories:
        journal_path = game_directory / "game.jsonl"
        if journal_path.is_file():
            return journal_path
    raise HTTPException(status_code=404, detail="No such game.")


def _load_tournament_view_model(games_directory: Path, tournament_id: str) -> dict[str, Any]:
    if not _GAME_ID_PATTERN.match(tournament_id):
        raise HTTPException(status_code=404, detail="No such tournament.")
    tournament_directory = games_directory / tournament_id
    if not (tournament_directory / "tournament.json").is_file():
        raise HTTPException(status_code=404, detail="No such tournament.")
    try:
        return build_tournament_view_model(tournament_directory=tournament_directory)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=f"The tournament is not viewable yet: {error}") from None
    except IllegalActionError as error:
        raise HTTPException(status_code=409, detail=f"A member journal does not replay cleanly: {error}") from None


def _load_view_model(games_directory: Path, game_id: str) -> dict[str, Any]:
    path = _journal_path(games_directory=games_directory, game_id=game_id)
    try:
        return build_view_model(journal_path=path)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=f"The game is not viewable yet: {error}") from None
    except IllegalActionError as error:
        # the journal no longer replays: it was corrupted outside the engine's control
        raise HTTPException(status_code=409, detail=f"The journal does not replay cleanly: {error}") from None


def _load_evaluation_view_model(games_directory: Path, game_id: str, manager: GameProcessManager) -> dict[str, Any]:
    path = _journal_path(games_directory=games_directory, game_id=game_id)
    try:
        view_model = build_evaluation_view_model(journal_path=path)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=f"The game is not viewable yet: {error}") from None
    view_model["running"] = manager.is_evaluating(game_id=game_id)
    return view_model


def _start_evaluation(manager: GameProcessManager, game_id: str, journal_path: Path, request: EvaluateRequest) -> None:
    if manager.is_evaluating(game_id=game_id):
        raise HTTPException(status_code=409, detail="An evaluation is already running for this game.")
    if not request.evaluators:
        raise HTTPException(status_code=400, detail="Provide at least one evaluator panel member.")
    # build every member up front so a bad spec or missing key fails fast, before the worker starts
    try:
        specs.build_evaluators(evaluator_specs=request.evaluators, settings=load_settings(), trace=None)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from None
    manager.evaluate_game(
        game_id=game_id,
        journal_path=journal_path,
        evaluator_specs=request.evaluators,
        log_path=journal_path.parent / "evaluation.log",
    )


def _read_trace_records(journal_path: Path) -> list[dict[str, Any]]:
    trace_path = trace_path_for_journal(journal_path=journal_path)
    if not trace_path.is_file():
        return []
    records = []
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            break  # a partial trailing line from a crash or an in-flight write
    return records


def _trace_call_summary(record: dict[str, Any]) -> dict[str, Any]:
    response = record.get("response")
    tool_call_names = [call["name"] for call in response["tool_calls"]] if response else []
    return {
        "seq": record.get("seq"),
        "time": record.get("time"),
        "turn": record.get("turn"),
        "kind": record.get("kind"),
        "caller": record.get("caller"),
        "model": record.get("model"),
        "message_count": len(record.get("messages", [])),
        "tool_call_names": tool_call_names,
        "input_tokens": record.get("input_tokens"),
        "output_tokens": record.get("output_tokens"),
        "latency_ms": record.get("latency_ms"),
        "error": record.get("error"),
    }


def _tournament_summary(manifest_path: Path) -> TournamentSummary:
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    return TournamentSummary(
        id=data["id"],
        players=data["players"],
        game_ids=data["game_ids"],
        created_at=datetime.fromisoformat(data["created_at"]),
    )


def _summarize_game_directory(
    game_directory: Path,
    manager: GameProcessManager,
    tournament_id: str | None,
    usage_totals_cache: dict[str, tuple[tuple[float, int], _UsageTotals]],
) -> GameSummary | None:
    journal_path = game_directory / "game.jsonl"
    if not journal_path.is_file():
        return None
    return _summarize_journal(
        path=journal_path,
        game_id=game_directory.name,
        running=manager.is_running(game_id=game_directory.name),
        tournament_id=tournament_id,
        usage=_usage_totals(journal_path=journal_path, cache=usage_totals_cache),
    )


def _usage_totals(journal_path: Path, cache: dict[str, tuple[tuple[float, int], _UsageTotals]]) -> _UsageTotals:
    trace_path = trace_path_for_journal(journal_path=journal_path)
    if not trace_path.is_file():
        return _NO_USAGE
    stat = trace_path.stat()
    signature = (stat.st_mtime, stat.st_size)
    cached = cache.get(str(trace_path))
    if cached is not None and cached[0] == signature:
        return cached[1]
    usage = usage_by_caller(trace_path=trace_path)
    known_costs = [entry["cost_usd"] for entry in usage.values() if entry["cost_usd"] is not None]
    totals = _UsageTotals(
        input_tokens=sum(entry["input_tokens"] for entry in usage.values()),
        output_tokens=sum(entry["output_tokens"] for entry in usage.values()),
        cost_usd=sum(known_costs) if known_costs else None,
    )
    cache[str(trace_path)] = (signature, totals)
    return totals


def _summarize_journal(
    path: Path, game_id: str, running: bool, tournament_id: str | None, usage: _UsageTotals
) -> GameSummary | None:
    header: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    has_commentary = False
    latest_turn = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            break  # a partial trailing line from a crash or an in-flight write
        match record.get("type"):
            case "header":
                header = record
            case "events":
                for event in record["events"]:
                    if event.get("kind") == "turn_started":
                        latest_turn = max(latest_turn, int(event["turn_number"]))
            case "result":
                result = record
            case "commentary":
                has_commentary = True
    if header is None:
        return None  # not a game journal
    final_turn = int(result["turn_count"]) if result is not None else None
    return GameSummary(
        id=game_id,
        players=list(header["player_specs"]),
        seed=int(header["seed"]),
        max_turns=int(header["max_turns"]),
        has_commentary=has_commentary,
        has_evaluation=evaluation_path_for_journal(journal_path=path).is_file(),
        finished=result is not None,
        running=running,
        winner=result["winner"] if result is not None else None,
        turn_count=final_turn if final_turn is not None else (latest_turn or None),
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cost_usd=usage.cost_usd,
        modified_at=datetime.fromtimestamp(path.stat().st_mtime, tz=UTC),
        tournament_id=tournament_id,
    )


def _launch_game(games_directory: Path, manager: GameProcessManager, request: NewGameRequest) -> NewGameResponse:
    _validate_launch_request(request=request)
    games_directory.mkdir(parents=True, exist_ok=True)
    base_id = _new_game_id(games_directory=games_directory, seed=request.seed)

    if request.parallel_games == 1:
        game_directory = games_directory / base_id
        _start_game_worker(game_directory=game_directory, manager=manager, request=request, seed=request.seed)
        _wait_for_journal_header(game_directory=game_directory, manager=manager)
        return NewGameResponse(id=base_id, tournament=False)

    # a tournament: the same setup replicated across workers on consecutive seeds,
    # each game in its own folder inside the tournament's folder
    tournament_directory = games_directory / base_id
    tournament_directory.mkdir(parents=True, exist_ok=True)
    game_ids = [f"{base_id}-g{index}" for index in range(1, request.parallel_games + 1)]
    manifest = {
        "id": base_id,
        "players": request.players,
        "game_ids": game_ids,
        "created_at": datetime.now(tz=UTC).isoformat(),
    }
    (tournament_directory / "tournament.json").write_text(json.dumps(manifest), encoding="utf-8")
    for index, game_id in enumerate(game_ids):
        _start_game_worker(
            game_directory=tournament_directory / game_id, manager=manager, request=request, seed=request.seed + index
        )
    for game_id in game_ids:
        _wait_for_journal_header(game_directory=tournament_directory / game_id, manager=manager)
    return NewGameResponse(id=base_id, tournament=True)


def _start_game_worker(game_directory: Path, manager: GameProcessManager, request: NewGameRequest, seed: int) -> None:
    game_directory.mkdir(parents=True, exist_ok=True)
    # the launch record keeps the game's own seed so resume sees the game, not the tournament
    launch_record = request.model_copy(update={"seed": seed, "parallel_games": 1})
    (game_directory / "launch.json").write_text(launch_record.model_dump_json(), encoding="utf-8")
    manager.launch_game(
        game_id=game_directory.name,
        players=request.players,
        seed=seed,
        commentators=request.commentators,
        max_turns=request.max_turns,
        journal_path=game_directory / "game.jsonl",
        log_path=game_directory / "worker.log",
    )


def _resume_game(games_directory: Path, manager: GameProcessManager, game_id: str, journal_path: Path) -> None:
    if manager.is_running(game_id=game_id):
        raise HTTPException(status_code=409, detail="The game is already running.")
    summary = _summarize_journal(
        path=journal_path, game_id=game_id, running=False, tournament_id=None, usage=_NO_USAGE
    )
    if summary is None or summary.finished:
        raise HTTPException(status_code=409, detail="The game is already finished; nothing to resume.")
    game_directory = journal_path.parent
    manager.resume_game(
        game_id=game_id,
        journal_path=journal_path,
        commentators=_launched_commentators(game_directory=game_directory),
        log_path=game_directory / "worker.log",
    )


def _launched_commentators(game_directory: Path) -> list[str]:
    launch_path = game_directory / "launch.json"
    if not launch_path.is_file():
        return []
    launch_record = json.loads(launch_path.read_text(encoding="utf-8"))
    if launch_record.get("commentators") is not None:
        return [str(spec) for spec in launch_record["commentators"]]
    # launches predating the panel carried a single 'commentator' spec
    legacy = launch_record.get("commentator")
    return [str(legacy)] if legacy else []


def _validate_launch_request(request: NewGameRequest) -> None:
    if not 3 <= len(request.players) <= 6:
        raise HTTPException(status_code=400, detail="Risk seats 3-6 players per game.")
    settings = load_settings()
    try:
        specs.named_specs(specs=request.players)
        for spec in request.players:
            if spec != "random":
                specs.build_agent(spec=spec, settings=settings)
        # mirror player validation: build every panel member up front so a bad spec fails fast
        specs.build_commentators(commentator_specs=request.commentators, settings=settings, trace=None)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from None


def _new_game_id(games_directory: Path, seed: int) -> str:
    base_id = f"{datetime.now(tz=UTC).strftime('%Y%m%d-%H%M%S')}-seed{seed}"
    game_id, suffix = base_id, 2
    while (games_directory / game_id).exists():
        game_id = f"{base_id}-{suffix}"
        suffix += 1
    return game_id


def _wait_for_journal_header(game_directory: Path, manager: GameProcessManager) -> None:
    journal_path = game_directory / "game.jsonl"
    log_path = game_directory / "worker.log"
    deadline = time.monotonic() + _LAUNCH_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if journal_path.is_file() and journal_path.read_text(encoding="utf-8").startswith('{"type": "header"'):
            return
        if not manager.is_running(game_id=game_directory.name):
            log_tail = log_path.read_text(encoding="utf-8")[-2000:] if log_path.is_file() else ""
            raise HTTPException(status_code=500, detail=f"The game worker exited at startup: {log_tail}")
        time.sleep(0.1)
    raise HTTPException(status_code=500, detail="The game did not start within the launch timeout.")
