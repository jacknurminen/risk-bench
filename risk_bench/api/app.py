import json
import re
import shutil
import time
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
    GameListResponse,
    GameSummary,
    ModelCatalogResponse,
    NewGameRequest,
    NewGameResponse,
    TagVerificationBody,
    TournamentSummary,
)
from risk_bench.config import load_settings
from risk_bench.game.engine import IllegalActionError
from risk_bench.runner.evaluation_store import (
    evaluation_path_for_journal,
    evaluation_trace_path_for_journal,
    read_evaluation,
)
from risk_bench.runner.evaluation_versions import EvaluationVersion, version_for
from risk_bench.runner.verification_store import delete_verification, write_verification
from risk_bench.viewer.evaluation import build_evaluation_view_model
from risk_bench.viewer.html import UI_DIRECTORY, render_reader_html, render_tournament_html
from risk_bench.viewer.snapshots import build_view_model
from risk_bench.viewer.tournament import build_tournament_view_model

_GAME_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
_LAUNCH_TIMEOUT_SECONDS = 15.0


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
        _journal_path(games_directory=games_directory, game_id=game_id)
        if not manager.pause_game(game_id=game_id):
            raise HTTPException(status_code=409, detail="The game is not running.")

    @app.post("/api/games/{game_id}/resume", status_code=204)
    def resume_game(game_id: str) -> None:
        journal_path = _journal_path(games_directory=games_directory, game_id=game_id)
        _resume_game(games_directory=games_directory, manager=manager, game_id=game_id, journal_path=journal_path)

    @app.delete("/api/games/{game_id}", status_code=204)
    def delete_game(game_id: str) -> None:
        journal_path = _journal_path(games_directory=games_directory, game_id=game_id)
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
        if not resolved.analysis_path.is_file():
            # the analysis is derived and cheap (no API calls), so a version that predates the
            # artifact — or lost it — gets one computed and persisted on first request
            try:
                analysis.write_analysis(
                    journal_path=journal_path, eval_path=resolved.eval_path, analysis_path=resolved.analysis_path
                )
            except ValueError as error:
                raise HTTPException(status_code=409, detail=str(error)) from None
        return json.loads(resolved.analysis_path.read_text(encoding="utf-8"))

    @app.put("/api/games/{game_id}/verifications", status_code=204)
    def set_tag_verification(game_id: str, body: TagVerificationBody) -> None:
        journal_path = _journal_path(games_directory=games_directory, game_id=game_id)
        # verifications are per version: resolve the run the verdict is about (the one being viewed)
        evaluation = version_for(journal_path=journal_path, run_id=body.version)
        if evaluation is None:
            raise HTTPException(status_code=404, detail="No such evaluation version.")
        path = evaluation.verifications_path
        if not body.verified:
            # clearing is idempotent: un-verifying an unverified tag is a no-op, not an error
            delete_verification(
                path=path,
                evaluator_index=body.evaluator_index,
                player=body.player,
                behavior=body.behavior,
                turn=body.turn,
            )
            return
        # a verification must point at a tag this version's evaluation actually holds
        if not _evaluation_has_tag(evaluation=evaluation, body=body):
            raise HTTPException(status_code=404, detail="No such tag in the current evaluation.")
        write_verification(
            path=path,
            verification={
                "type": "verification",
                "evaluator_index": body.evaluator_index,
                "player": body.player,
                "behavior": body.behavior,
                "turn": body.turn,
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


def _load_evaluation_view_model(
    games_directory: Path, game_id: str, manager: GameProcessManager, run_id: str | None, usage_cache: _UsageCache
) -> dict[str, Any]:
    path = _journal_path(games_directory=games_directory, game_id=game_id)
    version = version_for(journal_path=path, run_id=run_id)
    try:
        view_model = build_evaluation_view_model(journal_path=path, version=version)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=f"The game is not viewable yet: {error}") from None
    view_model["running"] = manager.is_evaluating(game_id=game_id)
    view_model["progress"] = _evaluation_progress(
        journal_path=path,
        panel=manager.evaluating_panel(game_id=game_id),
        turn_count=len({span["turn"] for span in view_model["spans"]}),
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
    )


def _evaluation_has_tag(evaluation: EvaluationVersion, body: TagVerificationBody) -> bool:
    eval_records = read_evaluation(path=evaluation.eval_path)
    return any(
        record.get("type") == "eval_tag"
        and record.get("evaluator_index") == body.evaluator_index
        and record.get("player") == body.player
        and record.get("behavior") == body.behavior
        and record.get("turn") == body.turn
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
    if "human" in request.players and request.parallel_games != 1:
        raise HTTPException(status_code=400, detail="Human games must be launched one at a time.")
    settings = load_settings()
    try:
        specs.named_specs(specs=request.players)
        for spec in request.players:
            if spec not in {"random", "human"}:
                specs.build_agent(spec=spec, settings=settings, max_tokens=settings.max_agent_tokens)
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
