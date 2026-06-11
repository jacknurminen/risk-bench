import json
import re
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

from risk_bench import specs
from risk_bench.api.schemas import (
    GameListResponse,
    GameSummary,
    ModelCatalogResponse,
    NewGameRequest,
    NewGameResponse,
)
from risk_bench.config import load_settings
from risk_bench.viewer.html import UI_DIRECTORY, render_live_viewer_html, render_viewer_html
from risk_bench.viewer.snapshots import build_view_model

_GAME_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
_LAUNCH_TIMEOUT_SECONDS = 15.0


def create_app(games_directory: Path) -> FastAPI:
    """Create the web app: browse and watch games, and launch new ones.

    The app is stateless: the games directory's journal files are the only
    source of truth, and new games run as detached subprocesses of the CLI so
    a server restart never kills a running game.

    Args:
        games_directory: Where game journals live and new ones are created.

    Returns:
        The configured FastAPI application.
    """
    app = FastAPI(title="risk-bench")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(UI_DIRECTORY / "index.html")

    @app.get("/games/{game_id}", include_in_schema=False)
    def game_page(game_id: str) -> HTMLResponse:
        view_model = _load_view_model(games_directory=games_directory, game_id=game_id)
        if view_model["result"] is not None:
            return HTMLResponse(render_viewer_html(view_model=view_model))
        return HTMLResponse(render_live_viewer_html(view_model=view_model, poll_url=f"/api/games/{game_id}"))

    @app.get("/api/games")
    def list_games() -> GameListResponse:
        summaries = [
            summary
            for path in sorted(games_directory.glob("*.jsonl"), reverse=True)
            if (summary := _summarize_journal(path=path)) is not None
        ]
        return GameListResponse(games=summaries)

    @app.get("/api/games/{game_id}")
    def game_view_model(game_id: str) -> dict[str, Any]:
        return _load_view_model(games_directory=games_directory, game_id=game_id)

    @app.post("/api/games", status_code=201)
    def launch_game(request: NewGameRequest) -> NewGameResponse:
        return _launch_game(games_directory=games_directory, request=request)

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


def _summarize_journal(path: Path) -> GameSummary | None:
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
        winner=result["winner"] if result is not None else None,
        turn_count=int(result["turn_count"]) if result is not None else None,
        modified_at=datetime.fromtimestamp(path.stat().st_mtime, tz=UTC),
    )


def _launch_game(games_directory: Path, request: NewGameRequest) -> NewGameResponse:
    _validate_launch_request(request=request)
    game_id = _new_game_id(games_directory=games_directory, seed=request.seed)
    journal_path = games_directory / f"{game_id}.jsonl"
    games_directory.mkdir(parents=True, exist_ok=True)

    command = [sys.executable, "-m", "risk_bench.main", "game", *request.players]
    command += ["--seed", str(request.seed), "--journal", str(journal_path)]
    if request.commentator is not None:
        command += ["--commentator", request.commentator]
    if request.max_turns is not None:
        command += ["--max-turns", str(request.max_turns)]

    log_path = games_directory / f"{game_id}.log"
    with log_path.open("w", encoding="utf-8") as log_file:
        process = subprocess.Popen(command, stdout=log_file, stderr=subprocess.STDOUT, start_new_session=True)
    _wait_for_journal_header(journal_path=journal_path, process=process, log_path=log_path)
    return NewGameResponse(id=game_id)


def _validate_launch_request(request: NewGameRequest) -> None:
    if not 3 <= len(request.players) <= 6:
        raise HTTPException(status_code=400, detail="Risk seats 3-6 players per game.")
    settings = load_settings()
    try:
        specs.named_specs(specs=request.players)
        for spec in request.players:
            if spec != "random":
                specs.build_agent(spec=spec, settings=settings)
        specs.build_commentator(spec=request.commentator, settings=settings)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from None


def _new_game_id(games_directory: Path, seed: int) -> str:
    base_id = f"{datetime.now(tz=UTC).strftime('%Y%m%d-%H%M%S')}-seed{seed}"
    game_id, suffix = base_id, 2
    while (games_directory / f"{game_id}.jsonl").exists():
        game_id = f"{base_id}-{suffix}"
        suffix += 1
    return game_id


def _wait_for_journal_header(journal_path: Path, process: subprocess.Popen[bytes], log_path: Path) -> None:
    deadline = time.monotonic() + _LAUNCH_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if journal_path.is_file() and journal_path.read_text(encoding="utf-8").startswith('{"type": "header"'):
            return
        if process.poll() is not None:
            log_tail = log_path.read_text(encoding="utf-8")[-2000:]
            raise HTTPException(status_code=500, detail=f"The game process exited at startup: {log_tail}")
        time.sleep(0.1)
    raise HTTPException(status_code=500, detail="The game did not start within the launch timeout.")
