import json
import re
import shutil
import time
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.types import Scope

from risk_bench import analysis, specs
from risk_bench.agents.base import ReasoningEffort
from risk_bench.agents.models import MODELS, Provider
from risk_bench.agents.tracing import trace_path_for_journal, usage_by_caller
from risk_bench.api.human import install_human_routes
from risk_bench.api.processes import GameProcessManager
from risk_bench.api.schemas import (
    CatalogModel,
    EvaluateRequest,
    ForkRequest,
    GameListResponse,
    GameSummary,
    ModelCatalogResponse,
    NewGameRequest,
    NewGameResponse,
    PlayerContextRequest,
    TournamentSummary,
    UnitVerificationBody,
)
from risk_bench.config import load_settings
from risk_bench.game.engine import IllegalActionError
from risk_bench.runner.control import journal_control_lock
from risk_bench.runner.evaluation_store import (
    evaluation_path_for_journal,
    evaluation_trace_path_for_journal,
    read_evaluation,
)
from risk_bench.runner.evaluation_versions import EvaluationVersion, version_for
from risk_bench.runner.forking import apply_additional_context, fork_game
from risk_bench.runner.journal import read_journal, restore_game
from risk_bench.runner.verification_store import delete_verification, write_verification
from risk_bench.version import require_v3
from risk_bench.viewer.evaluation import build_evaluation_view_model
from risk_bench.viewer.html import UI_DIRECTORY, render_reader_html, render_tournament_html
from risk_bench.viewer.prompts import preview_next_prompts
from risk_bench.viewer.snapshots import build_view_model
from risk_bench.viewer.tournament import build_tournament_view_model

_GAME_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
_LAUNCH_TIMEOUT_SECONDS = 15.0
# long enough for a worker that rejects the journal outright to die and be reported, short enough
# that a healthy resume is not held up
_WORKER_START_GRACE_SECONDS = 2.0


class _RevalidatingStaticFiles(StaticFiles):
    # the reader's CSS/JS are edited in place and meant to show on a plain browser reload; the
    # default StaticFiles sends an etag but no Cache-Control, so browsers serve them from the
    # heuristic cache without revalidating and stale assets linger. "no-cache" keeps the asset
    # cacheable but forces a conditional request every time — a cheap 304 when unchanged, fresh
    # bytes the moment a file is edited.
    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path=path, scope=scope)
        response.headers["cache-control"] = "no-cache"
        return response


class _UsageTotals(NamedTuple):
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None


_NO_USAGE = _UsageTotals(input_tokens=None, output_tokens=None, cost_usd=None)

# trace file path -> ((mtime, size), per-caller usage); traces can be large and are polled, so
# per-caller usage is recomputed only when a trace file actually changes
_UsageCache = dict[str, tuple[tuple[float, int], dict[str, dict[str, Any]]]]


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
    app.mount("/assets", _RevalidatingStaticFiles(directory=UI_DIRECTORY), name="assets")
    usage_cache: _UsageCache = {}
    install_human_routes(app, manager, lambda game_id: _journal_path(games_directory, game_id))

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(UI_DIRECTORY / "index.html")

    @app.get("/games/{game_id}", include_in_schema=False)
    def game_page(game_id: str) -> HTMLResponse:
        game_view_model = _load_view_model(games_directory=games_directory, game_id=game_id)
        with _journal_path(games_directory, game_id).open(encoding="utf-8") as journal:
            has_human = "human" in json.loads(journal.readline())["player_specs"].values()
        evaluation_view_model = _load_evaluation_view_model(
            games_directory=games_directory, game_id=game_id, manager=manager, run_id=None, usage_cache=usage_cache
        )
        return HTMLResponse(
            render_reader_html(
                game_view_model=game_view_model,
                evaluation_view_model=evaluation_view_model,
                live=game_view_model["result"] is None,
                live_running=manager.is_running(game_id=game_id),
                poll_url=f"/api/games/{game_id}",
                eval_poll_url=f"/api/games/{game_id}/evaluation",
                evaluate_url=f"/api/games/{game_id}/evaluate",
                verifications_url=f"/api/games/{game_id}/verifications",
                trace_url=f"/api/games/{game_id}/trace",
                analysis_url=f"/api/games/{game_id}/analysis",
                human_url=f"/api/games/{game_id}/human" if has_human and game_view_model["result"] is None else "",
            )
        )

    # the classic cockpit and the standalone evaluation page are retired; the reader hosts
    # everything they did, so old bookmarks land on the game page
    @app.get("/games/{game_id}/classic", include_in_schema=False)
    @app.get("/games/{game_id}/evaluation", include_in_schema=False)
    def retired_game_page(game_id: str) -> RedirectResponse:
        return RedirectResponse(url=f"/games/{game_id}")

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
                    usage_cache=usage_cache,
                )
                if summary is not None:
                    games.append(summary)
        return GameListResponse(games=games, tournaments=tournaments)

    @app.get("/api/games/{game_id}")
    def game_view_model(game_id: str) -> dict[str, Any]:
        view_model = _load_view_model(games_directory=games_directory, game_id=game_id)
        view_model["running"] = manager.is_running(game_id=game_id)
        return view_model

    @app.post("/api/games", status_code=201)
    def launch_game(request: NewGameRequest) -> NewGameResponse:
        return _launch_game(games_directory=games_directory, manager=manager, request=request)

    @app.post("/api/games/{game_id}/pause", status_code=204)
    def pause_game(game_id: str) -> None:
        journal_path = _journal_path(games_directory=games_directory, game_id=game_id)
        with journal_control_lock(journal_path):
            if not manager.pause_game(game_id=game_id):
                raise HTTPException(status_code=409, detail="The game is not running.")
            if manager.evaluating_live(game_id):
                manager.pause_evaluation(game_id)

    @app.post("/api/games/{game_id}/resume", status_code=204)
    def resume_game(game_id: str) -> None:
        journal_path = _journal_path(games_directory=games_directory, game_id=game_id)
        with journal_control_lock(journal_path):
            _resume_game(games_directory=games_directory, manager=manager, game_id=game_id, journal_path=journal_path)

    @app.post("/api/games/{game_id}/fork", status_code=201)
    def fork_game_route(game_id: str, request: ForkRequest) -> dict:
        source = _journal_path(games_directory, game_id)
        branch_id = f"fork-{uuid.uuid4().hex[:12]}"
        destination = games_directory / branch_id / "game.jsonl"
        with journal_control_lock(destination):
            try:
                fork_game(source, destination, **request.model_dump(exclude={"start"}))
            except (ValueError, KeyError, TypeError) as error:
                raise HTTPException(status_code=409, detail=str(error)) from None
            if request.start:
                manager.resume_game(
                    branch_id,
                    destination,
                    restore_game(destination).header.commentators,
                    destination.parent / "worker.log",
                )
                _confirm_worker_started(game_directory=destination.parent, manager=manager)

        return {"id": branch_id, "url": f"/games/{branch_id}"}

    @app.post("/api/games/{game_id}/context", status_code=204)
    def set_player_context(game_id: str, request: PlayerContextRequest) -> None:
        journal_path = _journal_path(games_directory=games_directory, game_id=game_id)
        with journal_control_lock(journal_path):
            if manager.is_running(game_id=game_id):
                raise HTTPException(status_code=409, detail="Pause the game before changing what its players know.")
            try:
                apply_additional_context(journal=journal_path, context=request.context, append=request.append)
            except ValueError as error:
                raise HTTPException(status_code=409, detail=str(error)) from None

    @app.post("/api/games/{game_id}/prompt-preview")
    def prompt_preview(game_id: str, request: PlayerContextRequest) -> dict[str, Any]:
        journal_path = _journal_path(games_directory=games_directory, game_id=game_id)
        try:
            previews = preview_next_prompts(journal_path=journal_path, additional_context=request.context)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from None
        return {"prompts": [asdict(preview) for preview in previews]}

    @app.delete("/api/games/{game_id}", status_code=204)
    def delete_game(game_id: str) -> None:
        journal_path = _journal_path(games_directory=games_directory, game_id=game_id)
        with journal_control_lock(journal_path):
            if manager.is_running(game_id=game_id):
                raise HTTPException(status_code=409, detail="The game is running; pause it before deleting.")
            if manager.is_evaluating(game_id=game_id):
                raise HTTPException(
                    status_code=409, detail="An evaluation is running for this game; wait for it to finish."
                )
            # removes the whole game folder: journal, trace, evaluation, and annotation sidecars alike
            shutil.rmtree(journal_path.parent)

    @app.post("/api/games/{game_id}/evaluate", status_code=202)
    def evaluate_game(game_id: str, request: EvaluateRequest) -> dict[str, bool]:
        journal_path = _journal_path(games_directory=games_directory, game_id=game_id)
        with journal_control_lock(journal_path):
            _start_evaluation(manager=manager, game_id=game_id, journal_path=journal_path, request=request)
        return {"running": True}

    @app.get("/api/games/{game_id}/evaluation")
    def evaluation_view_model(game_id: str, version: str | None = None) -> dict[str, Any]:
        return _load_evaluation_view_model(
            games_directory=games_directory, game_id=game_id, manager=manager, run_id=version, usage_cache=usage_cache
        )

    @app.get("/api/games/{game_id}/evaluation/usage")
    def evaluation_usage(game_id: str, version: str | None = None) -> dict[str, Any]:
        journal_path = _journal_path(games_directory=games_directory, game_id=game_id)
        trace_path = _evaluation_trace_path(journal_path=journal_path, run_id=version)
        usage = _cached_usage_by_caller(trace_path=trace_path, cache=usage_cache) if trace_path.is_file() else {}
        return _evaluator_usage_response(usage=usage)

    @app.get("/api/games/{game_id}/analysis")
    def analysis_artifact(game_id: str, version: str | None = None) -> dict[str, Any]:
        journal_path = _journal_path(games_directory=games_directory, game_id=game_id)
        resolved = version_for(journal_path=journal_path, run_id=version)
        if resolved is None:
            raise HTTPException(status_code=404, detail="No evaluation to analyze.")
        # Recheck the bound source on every request; a cached report must not hide later edits.
        try:
            analysis.write_analysis(
                journal_path=journal_path, eval_path=resolved.eval_path, analysis_path=resolved.analysis_path
            )
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from None
        return json.loads(resolved.analysis_path.read_text(encoding="utf-8"))

    @app.put("/api/games/{game_id}/verifications", status_code=204)
    def set_unit_verification(game_id: str, body: UnitVerificationBody) -> None:
        journal_path = _journal_path(games_directory=games_directory, game_id=game_id)
        with journal_control_lock(journal_path):
            # verifications are per version: resolve the run the verdict is about (the one being viewed)
            evaluation = version_for(journal_path=journal_path, run_id=body.version)
            if evaluation is None:
                raise HTTPException(status_code=404, detail="No such evaluation version.")
            if evaluation.is_current and manager.is_evaluating(game_id=game_id):
                raise HTTPException(
                    status_code=409, detail="Evaluation is running; review the completed run afterward."
                )
            path = evaluation.verifications_path
            if not body.verified:
                # clearing is idempotent: un-verifying an unverified tag is a no-op, not an error
                delete_verification(
                    path=path,
                    evaluator_index=body.evaluator_index,
                    unit_type=body.unit_type,
                    unit_id=body.unit_id,
                )
                return
            # a verification must point at a tag this version's evaluation actually holds
            if not _evaluation_has_unit(evaluation=evaluation, body=body):
                raise HTTPException(status_code=404, detail="No such research unit in this evaluation.")
            write_verification(
                path=path,
                verification={
                    "type": "verification",
                    "evaluator_index": body.evaluator_index,
                    "unit_type": body.unit_type,
                    "unit_id": body.unit_id,
                    "verified_at": datetime.now(tz=UTC).isoformat(),
                },
            )

    @app.get("/api/games/{game_id}/trace")
    def trace_call_list(game_id: str, source: str = "game", version: str | None = None) -> dict[str, Any]:
        journal_path = _journal_path(games_directory=games_directory, game_id=game_id)
        trace_path = _resolve_trace_path(journal_path=journal_path, source=source, run_id=version)
        return {"calls": [_trace_call_summary(record=record) for record in _read_trace_records(trace_path=trace_path)]}

    @app.get("/api/games/{game_id}/trace/{sequence_number}")
    def trace_call_detail(
        game_id: str, sequence_number: int, source: str = "game", version: str | None = None
    ) -> dict[str, Any]:
        journal_path = _journal_path(games_directory=games_directory, game_id=game_id)
        trace_path = _resolve_trace_path(journal_path=journal_path, source=source, run_id=version)
        for record in _read_trace_records(trace_path=trace_path):
            if record.get("seq") == sequence_number:
                return record
        raise HTTPException(status_code=404, detail="No such trace record.")

    @app.get("/api/models")
    def model_catalog() -> ModelCatalogResponse:
        return ModelCatalogResponse(
            models_by_provider={
                provider.value: [
                    CatalogModel(
                        id=model.id,
                        input_usd_per_million=model.pricing.input_usd_per_million,
                        output_usd_per_million=model.pricing.output_usd_per_million,
                    )
                    for model in MODELS
                    if model.provider is provider
                ]
                for provider in Provider
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
            try:
                with journal_path.open(encoding="utf-8") as source:
                    require_v3(json.loads(source.readline()))
            except (ValueError, KeyError) as error:
                raise HTTPException(status_code=409, detail=str(error)) from None
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
        raise HTTPException(
            status_code=409, detail=f"The journal does not replay cleanly or is not viewable yet: {error}"
        ) from None
    except IllegalActionError as error:
        # the journal no longer replays: it was corrupted outside the engine's control
        raise HTTPException(status_code=409, detail=f"The journal does not replay cleanly: {error}") from None


def _load_evaluation_view_model(
    games_directory: Path, game_id: str, manager: GameProcessManager, run_id: str | None, usage_cache: _UsageCache
) -> dict[str, Any]:
    path = _journal_path(games_directory=games_directory, game_id=game_id)
    version = version_for(journal_path=path, run_id=run_id)
    try:
        view_model = build_evaluation_view_model(journal_path=path, version=version)
    except ValueError as error:
        raise HTTPException(
            status_code=409, detail=f"The journal does not replay cleanly or is not viewable yet: {error}"
        ) from None
    view_model["running"] = manager.is_evaluating(game_id=game_id)
    if run_id is None:
        view_model["live_evaluation"] = view_model["live_evaluation"] or manager.evaluating_live(game_id)
    error_path = path.with_suffix(".eval.error.json")
    view_model["error"] = None
    if run_id is None:
        try:
            view_model["error"] = json.loads(error_path.read_text(encoding="utf-8")).get("detail")
        except (FileNotFoundError, json.JSONDecodeError):
            pass
    view_model["progress"] = _evaluation_progress(
        journal_path=path,
        panel=manager.evaluating_panel(game_id=game_id),
        turn_count=len({span["turn"] for span in view_model["spans"] if span["layer"] == "message"}),
        usage_cache=usage_cache,
    )
    return view_model


def _evaluation_progress(
    journal_path: Path, panel: list[str] | None, turn_count: int, usage_cache: _UsageCache
) -> dict[str, Any] | None:
    """Report the in-flight (or interrupted) evaluation run from its growing trace sidecar.

    The evaluator flushes one trace record per provider call, and a per-turn sweep makes a known
    number of calls (turns + one synthesis, per member), so the flat trace file doubles as a live
    progress and running-cost signal without any cooperation from the worker. A flat trace left
    behind without its eval sidecar means the last run died before finishing (e.g. a server
    shutdown killed the daemon worker) — surfaced as ``interrupted`` so the silent death and its
    spend are visible.

    Args:
        journal_path: The game's journal path.
        panel: The running panel's specs from the process manager, or None when no run is alive.
        turn_count: The number of turns the sweep covers (each member calls once per turn, plus one).
        usage_cache: The per-trace usage cache shared across requests.

    Returns:
        A running/interrupted progress dict, or None when there is nothing to report.
    """
    trace_path = evaluation_trace_path_for_journal(journal_path=journal_path)
    if not trace_path.is_file():
        return None
    usage = _cached_usage_by_caller(trace_path=trace_path, cache=usage_cache)
    completed_calls = sum(entry["calls"] for entry in usage.values())
    known_costs = [entry["cost_usd"] for entry in usage.values() if entry["cost_usd"] is not None]
    cost_usd = round(sum(known_costs), 4) if known_costs else None
    if panel is not None:
        return {
            "state": "running",
            "expected_calls": len(panel) * (turn_count + 1),
            "completed_calls": completed_calls,
            "failed_calls": sum(entry["errors"] for entry in usage.values()),
            "cost_usd": cost_usd,
            "members": [
                {
                    "index": index,
                    "spec": spec,
                    "calls": usage.get(f"evaluator[{index}]", {}).get("calls", 0),
                    "expected_calls": turn_count + 1,
                }
                for index, spec in enumerate(panel)
            ],
        }
    if evaluation_path_for_journal(journal_path=journal_path).is_file():
        return None  # the flat trace belongs to the completed current run; nothing in flight
    return {
        "state": "interrupted",
        # panel size is estimated from the callers seen, so a member that never called undercounts it
        "expected_calls": len(usage) * (turn_count + 1),
        "completed_calls": completed_calls,
        "cost_usd": cost_usd,
        "last_call_at": datetime.fromtimestamp(trace_path.stat().st_mtime, tz=UTC).isoformat(),
    }


def _cached_usage_by_caller(trace_path: Path, cache: _UsageCache) -> dict[str, dict[str, Any]]:
    stat = trace_path.stat()
    signature = (stat.st_mtime, stat.st_size)
    cached = cache.get(str(trace_path))
    if cached is not None and cached[0] == signature:
        return cached[1]
    usage = usage_by_caller(trace_path=trace_path)
    cache[str(trace_path)] = (signature, usage)
    return usage


def _evaluation_trace_path(journal_path: Path, run_id: str | None) -> Path:
    # an in-flight (or interrupted) run has a flat trace but no eval sidecar yet, so no version
    # lists it; asking for the current trace then means that run rather than the newest archive
    if run_id is None:
        flat_trace = evaluation_trace_path_for_journal(journal_path=journal_path)
        if flat_trace.is_file() and not evaluation_path_for_journal(journal_path=journal_path).is_file():
            return flat_trace
    version = version_for(journal_path=journal_path, run_id=run_id)
    if version is None:
        raise HTTPException(status_code=404, detail="No such evaluation version.")
    return version.trace_path


def _resolve_trace_path(journal_path: Path, source: str, run_id: str | None) -> Path:
    if source == "evaluation":
        return _evaluation_trace_path(journal_path=journal_path, run_id=run_id)
    return trace_path_for_journal(journal_path=journal_path)


def _evaluator_usage_response(usage: dict[str, dict[str, Any]]) -> dict[str, Any]:
    members = []
    for caller, entry in usage.items():
        match = re.fullmatch(r"evaluator\[(\d+)\]", caller)
        if match is not None:
            members.append({"index": int(match.group(1)), **entry})
    members.sort(key=lambda member: member["index"])
    known_costs = [member["cost_usd"] for member in members if member["cost_usd"] is not None]
    return {
        "members": members,
        "total": {
            "calls": sum(member["calls"] for member in members),
            "errors": sum(member["errors"] for member in members),
            "input_tokens": sum(member["input_tokens"] for member in members),
            "output_tokens": sum(member["output_tokens"] for member in members),
            "cache_write_tokens": sum(member["cache_write_tokens"] for member in members),
            "cache_read_tokens": sum(member["cache_read_tokens"] for member in members),
            "cost_usd": round(sum(known_costs), 4) if known_costs else None,
        },
    }


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
        live=request.live,
    )
    (journal_path.parent / "live-evaluators.json").write_text(
        json.dumps({"evaluators": request.evaluators if request.live else []}), encoding="utf-8"
    )


def _evaluation_has_unit(evaluation: EvaluationVersion, body: UnitVerificationBody) -> bool:
    eval_records = read_evaluation(path=evaluation.eval_path)
    return any(
        record.get("type") == "eval_" + body.unit_type
        and record.get("evaluator_index") == body.evaluator_index
        and record.get("id") == body.unit_id
        for record in eval_records
    )


def _read_trace_records(trace_path: Path) -> list[dict[str, Any]]:
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
    usage_cache: _UsageCache,
) -> GameSummary | None:
    journal_path = game_directory / "game.jsonl"
    if not journal_path.is_file():
        return None
    with journal_path.open(encoding="utf-8") as source:
        try:
            require_v3(json.loads(source.readline()))
        except (ValueError, KeyError):
            return None
    return _summarize_journal(
        path=journal_path,
        game_id=game_directory.name,
        running=manager.is_running(game_id=game_directory.name),
        tournament_id=tournament_id,
        usage=_usage_totals(journal_path=journal_path, cache=usage_cache),
    )


def _usage_totals(journal_path: Path, cache: _UsageCache) -> _UsageTotals:
    trace_path = trace_path_for_journal(journal_path=journal_path)
    if not trace_path.is_file():
        return _NO_USAGE
    usage = _cached_usage_by_caller(trace_path=trace_path, cache=cache)
    known_costs = [entry["cost_usd"] for entry in usage.values() if entry["cost_usd"] is not None]
    return _UsageTotals(
        input_tokens=sum(entry["input_tokens"] for entry in usage.values()),
        output_tokens=sum(entry["output_tokens"] for entry in usage.values()),
        cost_usd=sum(known_costs) if known_costs else None,
    )


def _summarize_journal(
    path: Path, game_id: str, running: bool, tournament_id: str | None, usage: _UsageTotals
) -> GameSummary | None:
    header: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    has_commentary = False
    latest_turn = 0
    for record in read_journal(path):
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
        _start_launched_live_evaluation(game_directory, manager, request.evaluators)
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
        game_directory = tournament_directory / game_id
        _wait_for_journal_header(game_directory=game_directory, manager=manager)
        _start_launched_live_evaluation(game_directory, manager, request.evaluators)
    return NewGameResponse(id=base_id, tournament=True)


def _start_launched_live_evaluation(game_directory: Path, manager: GameProcessManager, evaluators: list[str]) -> None:
    if evaluators and not manager.is_evaluating(game_directory.name):
        game_directory.mkdir(parents=True, exist_ok=True)
        (game_directory / "live-evaluators.json").write_text(
            json.dumps({"evaluators": evaluators}), encoding="utf-8"
        )
        manager.evaluate_game(
            game_id=game_directory.name, journal_path=game_directory / "game.jsonl",
            evaluator_specs=evaluators, log_path=game_directory / "evaluation.log", live=True,
        )


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
        additional_context=_starting_context(request=request),
        journal_path=game_directory / "game.jsonl",
        log_path=game_directory / "worker.log",
    )


def _starting_context(request: NewGameRequest) -> list[str]:
    # one line for everyone plus a per-player line, folded into the single block each player sees
    per_player = request.player_context or [""] * len(request.players)
    return ["\n\n".join(part for part in (request.common_context.strip(), own.strip()) if part) for own in per_player]


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
    _confirm_worker_started(game_directory=game_directory, manager=manager)
    live_config = game_directory / "live-evaluators.json"
    launch_path = game_directory / "launch.json"
    source = live_config if live_config.is_file() else launch_path
    if source.is_file():
        evaluators = json.loads(source.read_text(encoding="utf-8")).get("evaluators", [])
        _start_launched_live_evaluation(game_directory, manager, evaluators)


def _confirm_worker_started(game_directory: Path, manager: GameProcessManager) -> None:
    # A worker that refuses the journal dies in its first moments, and the caller would otherwise see
    # a successful resume and a game that never moves. A worker that took the journal writes to it —
    # even a game it finishes inside the grace window — so an untouched journal and a dead worker is
    # the one combination that means it never started.
    journal_path = game_directory / "game.jsonl"
    size_at_start = journal_path.stat().st_size
    deadline = time.monotonic() + _WORKER_START_GRACE_SECONDS
    while time.monotonic() < deadline:
        if journal_path.stat().st_size != size_at_start:
            return
        if not manager.is_running(game_id=game_directory.name):
            log_path = game_directory / "worker.log"
            log_tail = log_path.read_text(encoding="utf-8").strip()[-1200:] if log_path.is_file() else ""
            raise HTTPException(status_code=409, detail=f"The game worker exited at startup: {log_tail}")
        time.sleep(0.05)


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
    if "human" in request.players and request.parallel_games != 1:
        raise HTTPException(status_code=400, detail="Human games must be launched one at a time.")
    if request.player_context and len(request.player_context) != len(request.players):
        raise HTTPException(status_code=400, detail="Per-player context must hold one entry per player.")
    settings = load_settings()
    try:
        specs.named_specs(specs=request.players)
        for spec in request.players:
            if spec not in {"random", "human"}:
                specs.build_agent(spec=spec, settings=settings, max_tokens=settings.max_agent_tokens)
        # mirror player validation: build every panel member up front so a bad spec fails fast
        specs.build_commentators(commentator_specs=request.commentators, settings=settings, trace=None)
        specs.build_evaluators(evaluator_specs=request.evaluators, settings=settings, trace=None)
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
        if journal_path.is_file() and journal_path.read_text(encoding="utf-8").startswith(
            ('{"type": "header"', '{"type":"header"')
        ):
            return
        if not manager.is_running(game_id=game_directory.name):
            log_tail = log_path.read_text(encoding="utf-8")[-2000:] if log_path.is_file() else ""
            raise HTTPException(status_code=500, detail=f"The game worker exited at startup: {log_tail}")
        time.sleep(0.1)
    raise HTTPException(status_code=500, detail="The game did not start within the launch timeout.")
