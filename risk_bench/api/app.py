import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

from risk_bench import specs
from risk_bench.agents.tracing import trace_path_for_journal
from risk_bench.api.processes import GameProcessManager
from risk_bench.api.schemas import (
    GameListResponse,
    GameSummary,
    ModelCatalogResponse,
    NewGameRequest,
    NewGameResponse,
    TournamentSummary,
)
from risk_bench.config import load_settings
from risk_bench.viewer.html import UI_DIRECTORY, render_live_viewer_html, render_viewer_html
from risk_bench.viewer.snapshots import build_view_model

_GAME_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
_LAUNCH_TIMEOUT_SECONDS = 15.0


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

    @app.get("/api/games")
    def list_games() -> GameListResponse:
        tournaments = _load_tournaments(games_directory=games_directory)
        tournament_by_game = {game_id: t.id for t in tournaments for game_id in t.game_ids}
        summaries = [
            summary
            for path in sorted(games_directory.glob("*.jsonl"), reverse=True)
            if (
                summary := _summarize_journal(
                    path=path,
                    running=manager.is_running(game_id=path.stem),
                    tournament_id=tournament_by_game.get(path.stem),
                )
            )
            is not None
        ]
        return GameListResponse(games=summaries, tournaments=tournaments)

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
            baseline="random",
        )

    return app


def _journal_path(games_directory: Path, game_id: str) -> Path:
    if not _GAME_ID_PATTERN.match(game_id):
        raise HTTPException(status_code=404, detail="No such game.")
    path = games_directory / f"{game_id}.jsonl"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="No such game.")
    return path


def _load_view_model(games_directory: Path, game_id: str) -> dict[str, Any]:
    path = _journal_path(games_directory=games_directory, game_id=game_id)
    try:
        return build_view_model(journal_path=path)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=f"The game is not viewable yet: {error}") from None


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


def _load_tournaments(games_directory: Path) -> list[TournamentSummary]:
    tournaments = []
    for path in sorted(games_directory.glob("*.tournament.json"), reverse=True):
        data = json.loads(path.read_text(encoding="utf-8"))
        tournaments.append(
            TournamentSummary(
                id=data["id"],
                players=data["players"],
                game_ids=data["game_ids"],
                created_at=datetime.fromisoformat(data["created_at"]),
            )
        )
    return tournaments


def _summarize_journal(path: Path, running: bool, tournament_id: str | None) -> GameSummary | None:
    header: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    has_commentary = False
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            break  # a partial trailing line from a crash or an in-flight write
        match record.get("type"):
            case "header":
                header = record
            case "result":
                result = record
            case "commentary":
                has_commentary = True
    if header is None:
        return None  # not a game journal
    return GameSummary(
        id=path.stem,
        players=list(header["player_specs"]),
        seed=int(header["seed"]),
        max_turns=int(header["max_turns"]),
        has_commentary=has_commentary,
        finished=result is not None,
        running=running,
        winner=result["winner"] if result is not None else None,
        turn_count=int(result["turn_count"]) if result is not None else None,
        modified_at=datetime.fromtimestamp(path.stat().st_mtime, tz=UTC),
        tournament_id=tournament_id,
    )


def _launch_game(games_directory: Path, manager: GameProcessManager, request: NewGameRequest) -> NewGameResponse:
    _validate_launch_request(request=request)
    games_directory.mkdir(parents=True, exist_ok=True)
    base_id = _new_game_id(games_directory=games_directory, seed=request.seed)

    if request.parallel_games == 1:
        _start_game_worker(
            games_directory=games_directory, manager=manager, game_id=base_id, request=request, seed=request.seed
        )
        _wait_for_journal_header(games_directory=games_directory, manager=manager, game_id=base_id)
        return NewGameResponse(id=base_id, tournament=False)

    # a tournament: the same setup replicated across workers on consecutive seeds
    game_ids = [f"{base_id}-g{index}" for index in range(1, request.parallel_games + 1)]
    manifest = {
        "id": base_id,
        "players": request.players,
        "game_ids": game_ids,
        "created_at": datetime.now(tz=UTC).isoformat(),
    }
    (games_directory / f"{base_id}.tournament.json").write_text(json.dumps(manifest), encoding="utf-8")
    for index, game_id in enumerate(game_ids):
        _start_game_worker(
            games_directory=games_directory,
            manager=manager,
            game_id=game_id,
            request=request,
            seed=request.seed + index,
        )
    for game_id in game_ids:
        _wait_for_journal_header(games_directory=games_directory, manager=manager, game_id=game_id)
    return NewGameResponse(id=base_id, tournament=True)


def _start_game_worker(
    games_directory: Path, manager: GameProcessManager, game_id: str, request: NewGameRequest, seed: int
) -> None:
    # the launch record keeps the game's own seed so resume sees the game, not the tournament
    launch_record = request.model_copy(update={"seed": seed, "parallel_games": 1})
    (games_directory / f"{game_id}.launch.json").write_text(launch_record.model_dump_json(), encoding="utf-8")
    manager.launch_game(
        game_id=game_id,
        players=request.players,
        seed=seed,
        commentator_spec=request.commentator,
        max_turns=request.max_turns,
        journal_path=games_directory / f"{game_id}.jsonl",
        log_path=games_directory / f"{game_id}.log",
    )


def _resume_game(games_directory: Path, manager: GameProcessManager, game_id: str, journal_path: Path) -> None:
    if manager.is_running(game_id=game_id):
        raise HTTPException(status_code=409, detail="The game is already running.")
    summary = _summarize_journal(path=journal_path, running=False, tournament_id=None)
    if summary is None or summary.finished:
        raise HTTPException(status_code=409, detail="The game is already finished; nothing to resume.")
    manager.resume_game(
        game_id=game_id,
        journal_path=journal_path,
        commentator_spec=_launched_commentator(games_directory=games_directory, game_id=game_id),
        log_path=games_directory / f"{game_id}.log",
    )


def _launched_commentator(games_directory: Path, game_id: str) -> str | None:
    launch_path = games_directory / f"{game_id}.launch.json"
    if not launch_path.is_file():
        return None
    commentator = json.loads(launch_path.read_text(encoding="utf-8")).get("commentator")
    return str(commentator) if commentator is not None else None


def _validate_launch_request(request: NewGameRequest) -> None:
    if not 3 <= len(request.players) <= 6:
        raise HTTPException(status_code=400, detail="Risk seats 3-6 players per game.")
    settings = load_settings()
    try:
        specs.named_specs(specs=request.players)
        for spec in request.players:
            if spec != "random":
                specs.build_agent(spec=spec, settings=settings)
        specs.build_commentator(spec=request.commentator, settings=settings, trace=None)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from None


def _new_game_id(games_directory: Path, seed: int) -> str:
    base_id = f"{datetime.now(tz=UTC).strftime('%Y%m%d-%H%M%S')}-seed{seed}"
    game_id, suffix = base_id, 2
    while (games_directory / f"{game_id}.jsonl").exists() or (games_directory / f"{game_id}.tournament.json").exists():
        game_id = f"{base_id}-{suffix}"
        suffix += 1
    return game_id


def _wait_for_journal_header(games_directory: Path, manager: GameProcessManager, game_id: str) -> None:
    journal_path = games_directory / f"{game_id}.jsonl"
    log_path = games_directory / f"{game_id}.log"
    deadline = time.monotonic() + _LAUNCH_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if journal_path.is_file() and journal_path.read_text(encoding="utf-8").startswith('{"type": "header"'):
            return
        if not manager.is_running(game_id=game_id):
            log_tail = log_path.read_text(encoding="utf-8")[-2000:] if log_path.is_file() else ""
            raise HTTPException(status_code=500, detail=f"The game worker exited at startup: {log_tail}")
        time.sleep(0.1)
    raise HTTPException(status_code=500, detail="The game did not start within the launch timeout.")
