import json
import re
import shutil
import time
import uuid
from contextlib import ExitStack
from dataclasses import asdict
from datetime import UTC, datetime
from itertools import permutations
from pathlib import Path
from typing import Any, NamedTuple

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from starlette.types import Scope

from risk_bench import analysis, specs
from risk_bench.agents.base import ReasoningEffort
from risk_bench.agents.models import MODELS, Provider
from risk_bench.agents.tracing import trace_path_for_journal, usage_by_caller
from risk_bench.api.human import install_human_routes
from risk_bench.api.processes import GameProcessManager
from risk_bench.api.schemas import (
    CatalogModel,
    ContinueTournamentRequest,
    EvaluateRequest,
    ForkRequest,
    GameListResponse,
    GameSummary,
    ModelCatalogResponse,
    NewGameRequest,
    NewGameResponse,
    PlayerContextRequest,
    ReviewSampleRequest,
    ReviewVerdictBody,
    TournamentSummary,
    UnitVerificationBody,
)
from risk_bench.config import REPLAY_SETTING_FIELDS, load_settings
from risk_bench.game.engine import IllegalActionError
from risk_bench.players.profiles import ExperimentProfile, load_profile, profile_catalog
from risk_bench.runner import annotation_review
from risk_bench.runner.control import journal_control_lock
from risk_bench.runner.evaluation_config import configure_evaluation, configured_panel
from risk_bench.runner.evaluation_store import (
    evaluation_path_for_journal,
    evaluation_trace_path_for_journal,
    read_evaluation,
)
from risk_bench.runner.evaluation_versions import EvaluationVersion, version_for
from risk_bench.runner.forking import apply_additional_context, fork_game
from risk_bench.runner.journal import read_journal, restore_game
from risk_bench.runner.live_evaluation import review_activity_for_journal
from risk_bench.runner.tournament_evaluation import create_batch, list_batches
from risk_bench.runner.verification_store import delete_verification, write_verification
from risk_bench.version import require_v3
from risk_bench.viewer.evaluation import build_evaluation_view_model
from risk_bench.viewer.html import UI_DIRECTORY, render_reader_html, render_review_html, render_tournament_html
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
    def game_page(game_id: str, version: str | None = None) -> HTMLResponse:
        game_view_model = _load_view_model(games_directory=games_directory, game_id=game_id)
        with _journal_path(games_directory, game_id).open(encoding="utf-8") as journal:
            has_human = "human" in json.loads(journal.readline())["player_specs"].values()
        evaluation_view_model = _load_evaluation_view_model(
            games_directory=games_directory, game_id=game_id, manager=manager, run_id=version, usage_cache=usage_cache
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
    def tournament_page(tournament_id: str, version: str | None = None) -> HTMLResponse:
        directory = _tournament_directory(games_directory, tournament_id)
        manager.reconcile_tournament_evaluations(directory)
        view_model = _load_tournament_view_model(games_directory, tournament_id, version)
        _add_tournament_replay_controls(view_model, directory, manager)
        live = len(view_model["games"]) < view_model["total_games"] or not all(
            game["finished"] for game in view_model["games"]
        )
        return HTMLResponse(
            render_tournament_html(view_model=view_model, live=live, poll_url=f"/api/tournaments/{tournament_id}")
        )

    @app.get("/api/tournaments/{tournament_id}")
    def tournament_view_model(tournament_id: str, version: str | None = None) -> dict[str, Any]:
        directory = _tournament_directory(games_directory, tournament_id)
        manager.reconcile_tournament_evaluations(directory)
        view_model = _load_tournament_view_model(games_directory, tournament_id, version)
        _add_tournament_replay_controls(view_model, directory, manager)
        return view_model

    @app.post("/api/tournaments/{tournament_id}/evaluate", status_code=202)
    def evaluate_tournament(tournament_id: str, request: EvaluateRequest) -> dict:
        directory = _tournament_directory(games_directory, tournament_id)
        manager.reconcile_tournament_evaluations(directory)
        if not request.evaluators:
            raise HTTPException(status_code=400, detail="Provide at least one evaluator.")
        try:
            specs.build_evaluators(evaluator_specs=request.evaluators, settings=load_settings(), trace=None)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from None
        with ExitStack() as locks:
            locks.enter_context(journal_control_lock(directory / "tournament.json"))
            manifest = json.loads((directory / "tournament.json").read_text())
            for game_id in sorted(manifest["game_ids"]):
                journal = directory / game_id / "game.jsonl"
                locks.enter_context(journal_control_lock(journal))
                if (manager.is_running(game_id) or manager.is_queued(game_id) or manager.is_evaluating(game_id)
                        or review_activity_for_journal(journal)):
                    raise HTTPException(status_code=409, detail=f"{game_id} is running, queued or being evaluated.")
                if not journal.is_file() or not any(row["type"] == "result" for row in read_journal(journal)):
                    raise HTTPException(
                        status_code=409, detail="Finish all games before rerunning tournament evaluations."
                    )
            if not manifest["game_ids"]:
                raise HTTPException(status_code=409, detail="This tournament has no games.")
            path = create_batch(directory, manifest, request.evaluators, request.live)
            manager.evaluate_tournament(path)
        return {"version": path.stem, "games": len(manifest["game_ids"])}

    @app.post("/api/tournaments/{tournament_id}/continue", status_code=202)
    def continue_tournament(tournament_id: str, request: ContinueTournamentRequest) -> dict:
        directory = _tournament_directory(games_directory, tournament_id)
        with journal_control_lock(directory / "tournament.json"):
            return _continue_tournament(directory, manager, request)

    @app.post("/api/tournaments/{tournament_id}/games/{game_id}/replay", status_code=202)
    def replay_tournament_game(tournament_id: str, game_id: str) -> dict:
        directory = _tournament_directory(games_directory, tournament_id)
        with journal_control_lock(directory / "tournament.json"):
            manifest = json.loads((directory / "tournament.json").read_text())
            if game_id not in manifest["game_ids"]:
                raise HTTPException(
                    status_code=409, detail="This game is no longer a current tournament attempt. Refresh the page."
                )
            with journal_control_lock(directory / game_id / "game.jsonl"):
                return _replay_tournament_game(directory, manifest, game_id, manager)

    # --- annotation review: sampled human checks of the evaluator's findings and misses ---

    @app.get("/tournaments/{tournament_id}/review", include_in_schema=False)
    def review_page(tournament_id: str) -> HTMLResponse:
        _tournament_directory(games_directory, tournament_id)
        return HTMLResponse(render_review_html(tournament_id=tournament_id))

    @app.get("/api/tournaments/{tournament_id}/reviews")
    def list_review_samples(tournament_id: str) -> dict[str, Any]:
        directory = _tournament_directory(games_directory, tournament_id)
        batches = [
            {"id": batch["id"], "created_at": batch["created_at"], "panel": batch["panel"], "method": batch["method"]}
            for batch in list_batches(directory)
            if batch["method"] == "whole_game"
        ]
        return {"samples": annotation_review.list_samples(directory), "batches": batches}

    @app.post("/api/tournaments/{tournament_id}/reviews", status_code=201)
    def create_review_sample(tournament_id: str, request: ReviewSampleRequest) -> dict[str, Any]:
        directory = _tournament_directory(games_directory, tournament_id)
        try:
            sample = annotation_review.draw_sample(directory, **request.model_dump())
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from None
        return {key: value for key, value in sample.items() if key != "items"}

    @app.get("/api/tournaments/{tournament_id}/reviews/{sample_id}")
    def review_sample(tournament_id: str, sample_id: str) -> dict[str, Any]:
        directory = _tournament_directory(games_directory, tournament_id)
        try:
            return annotation_review.review_view(directory, sample_id)
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from None

    @app.put("/api/tournaments/{tournament_id}/reviews/{sample_id}/items/{item_id}")
    def set_review_verdict(tournament_id: str, sample_id: str, item_id: str, body: ReviewVerdictBody) -> dict:
        directory = _tournament_directory(games_directory, tournament_id)
        try:
            if body.verdict is None:
                annotation_review.clear_verdict(directory, sample_id, item_id)
                return {"verdict": None}
            return annotation_review.save_verdict(directory, sample_id, item_id, body.verdict, body.note)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from None

    @app.get("/api/tournaments/{tournament_id}/reviews/{sample_id}/report")
    def review_report(tournament_id: str, sample_id: str) -> dict[str, Any]:
        directory = _tournament_directory(games_directory, tournament_id)
        try:
            return annotation_review.score_sample(directory, sample_id)
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from None

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
        journal_path = _journal_path(games_directory=games_directory, game_id=game_id, allow_unstarted=True)
        with journal_control_lock(journal_path):
            if not manager.pause_game(game_id=game_id):
                raise HTTPException(status_code=409, detail="The game is not running.")
            if manager.evaluating_live(game_id):
                manager.pause_evaluation(game_id)

    @app.post("/api/games/{game_id}/resume", status_code=204)
    def resume_game(game_id: str) -> None:
        journal_path = _journal_path(games_directory=games_directory, game_id=game_id, allow_unstarted=True)
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
        journal_path = _journal_path(games_directory=games_directory, game_id=game_id, allow_unstarted=True)
        with journal_control_lock(journal_path):
            if manager.is_queued(game_id):
                raise HTTPException(status_code=409, detail="Cancel the queued game before deleting it.")
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

    @app.get("/api/experiment-profiles")
    def experiment_profiles() -> list[ExperimentProfile]:
        return profile_catalog()

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


def _journal_path(games_directory: Path, game_id: str, *, allow_unstarted: bool = False) -> Path:
    if not _GAME_ID_PATTERN.match(game_id):
        raise HTTPException(status_code=404, detail="No such game.")
    # a game folder lives at the top level (standalone) or inside its tournament's folder
    candidate_directories = [games_directory / game_id, *games_directory.glob(f"*/{game_id}")]
    for game_directory in candidate_directories:
        journal_path = game_directory / "game.jsonl"
        if allow_unstarted and not journal_path.exists() and (game_directory / "launch.json").is_file():
            return journal_path
        if journal_path.is_file():
            try:
                with journal_path.open(encoding="utf-8") as source:
                    require_v3(json.loads(source.readline()))
            except (ValueError, KeyError) as error:
                raise HTTPException(status_code=409, detail=str(error)) from None
            return journal_path
    raise HTTPException(status_code=404, detail="No such game.")


def _tournament_directory(games_directory: Path, tournament_id: str) -> Path:
    if not _GAME_ID_PATTERN.match(tournament_id):
        raise HTTPException(status_code=404, detail="No such tournament.")
    tournament_directory = games_directory / tournament_id
    if not (tournament_directory / "tournament.json").is_file():
        raise HTTPException(status_code=404, detail="No such tournament.")
    return tournament_directory


def _load_tournament_view_model(games_directory: Path, tournament_id: str,
                                version: str | None = None) -> dict[str, Any]:
    tournament_directory = _tournament_directory(games_directory, tournament_id)
    try:
        return build_tournament_view_model(tournament_directory=tournament_directory, evaluation_version=version)
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
    live_panel = _configured_live_evaluators(path)
    view_model["configured_panels"] = {
        "live_rounds": live_panel, "whole_game": configured_panel(path, "whole_game"),
    }
    live_in_game = run_id is None and manager.is_running(game_id) and not view_model["finished"] and bool(live_panel)
    view_model["live_panel"] = live_panel
    review_activity = review_activity_for_journal(path) if run_id is None else None
    whole_active = bool(review_activity and review_activity["kind"] == "whole_game")
    detached_active = run_id is None and manager.is_evaluating(game_id)
    view_model["review_active"] = review_activity is not None
    view_model["reviewing_round"] = review_activity.get("round") if review_activity else None
    view_model["reviewing_final"] = bool(review_activity and review_activity["kind"] == "final")
    view_model["reviewing_whole_game"] = whole_active
    view_model["running"] = detached_active or live_in_game or bool(review_activity)
    if run_id is None:
        if whole_active or (detached_active and not manager.evaluating_live(game_id)):
            view_model["live_evaluation"] = False
        else:
            view_model["live_evaluation"] = (
                view_model["live_evaluation"] or manager.evaluating_live(game_id) or live_in_game
                or bool(review_activity) or (bool(live_panel) and not view_model["has_evaluation"])
            )
    error_path = path.with_suffix(".eval.error.json")
    view_model["error"] = None
    if run_id is None:
        try:
            view_model["error"] = json.loads(error_path.read_text(encoding="utf-8")).get("detail")
        except (FileNotFoundError, json.JSONDecodeError):
            pass
    panel = (manager.evaluating_panel(game_id=game_id) or (review_activity or {}).get("panel")
             or (live_panel if live_in_game else None)) if run_id is None else None
    if view_model["live_evaluation"] and not whole_active:
        rounds = {span["round"] for span in view_model["spans"] if span["turn"] > 0 and span.get("round") is not None}
        completed_rounds = len(rounds) if view_model["finished"] else max(0, len(rounds) - 1)
        expected_per_member = completed_rounds + (1 if view_model["finished"] else 0)
    else:
        expected_per_member = 1
    view_model["progress"] = _evaluation_progress(
        journal_path=path,
        panel=panel,
        expected_per_member=expected_per_member,
        usage_cache=usage_cache,
    ) if run_id is None else None
    if view_model["error"] or (view_model["progress"] or {}).get("state") == "interrupted":
        view_model["completion_status"] = "interrupted"
    elif view_model["review_active"] or detached_active:
        view_model["completion_status"] = "running"
    return view_model


def _evaluation_progress(
    journal_path: Path, panel: list[str] | None, expected_per_member: int, usage_cache: _UsageCache
) -> dict[str, Any] | None:
    """Report the in-flight (or interrupted) evaluation run from its growing trace sidecar.

    The evaluator flushes one trace record per provider call. A stored-game sweep calls each
    member once for a fresh whole-game assessment (plus corrections if needed); live research calls per round plus a
    final pass. The flat trace file therefore doubles as a progress and running-cost signal
    without any cooperation from the worker. A flat trace left
    behind without its eval sidecar means the last run died before finishing (e.g. a server
    shutdown killed the daemon worker) — surfaced as ``interrupted`` so the silent death and its
    spend are visible.

    Args:
        journal_path: The game's journal path.
        panel: The running panel's specs from the process manager, or None when no run is alive.
        expected_per_member: Expected calls for one member at the current journal cutoff.
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
            "expected_calls": len(panel) * expected_per_member,
            "completed_calls": completed_calls,
            "failed_calls": sum(entry["errors"] for entry in usage.values()),
            "cost_usd": cost_usd,
            "members": [
                {
                    "index": index,
                    "spec": spec,
                    "calls": usage.get(f"evaluator[{index}]", {}).get("calls", 0),
                    "expected_calls": expected_per_member,
                }
                for index, spec in enumerate(panel)
            ],
        }
    if evaluation_path_for_journal(journal_path=journal_path).is_file():
        return None  # the flat trace belongs to the completed current run; nothing in flight
    return {
        "state": "interrupted",
        # panel size is estimated from the callers seen, so a member that never called undercounts it
        "expected_calls": len(usage) * expected_per_member,
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
    if manager.is_queued(game_id):
        raise HTTPException(status_code=409, detail="Cancel the queued game before starting an evaluation.")
    if manager.is_evaluating(game_id=game_id) or review_activity_for_journal(journal_path):
        raise HTTPException(status_code=409, detail="An evaluation is already running for this game.")
    if request.restart:
        if not request.live:
            raise HTTPException(status_code=400, detail="Fresh replay requires round-by-round evaluation.")
        if manager.is_running(game_id):
            raise HTTPException(status_code=409, detail="Pause the game before replaying its completed rounds.")
    if not request.live and manager.is_running(game_id):
        raise HTTPException(
            status_code=409, detail="Wait for the game to finish or pause it before whole-game analysis."
        )
    if manager.is_running(game_id) and _configured_live_evaluators(journal_path):
        raise HTTPException(status_code=409, detail="Pause the game before changing its live evaluator panel.")
    if not request.evaluators:
        raise HTTPException(status_code=400, detail="Provide at least one evaluator panel member.")
    # build every member up front so a bad spec or missing key fails fast, before the worker starts
    try:
        specs.build_evaluators(evaluator_specs=request.evaluators, settings=load_settings(), trace=None)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from None
    configure_evaluation(journal_path, "live_rounds" if request.live else "whole_game", request.evaluators)
    if request.live and manager.is_running(game_id):
        journal_path.with_suffix(".eval.error.json").unlink(missing_ok=True)
        return  # the game worker will block at the next completed round and run this panel
    manager.evaluate_game(
        game_id=game_id,
        journal_path=journal_path,
        evaluator_specs=request.evaluators,
        log_path=journal_path.parent / "evaluation.log",
        live=request.live,
        restart=request.restart,
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
        parallel_games=data.get("parallel_games", len(data["game_ids"])),
    )


def _summarize_game_directory(
    game_directory: Path,
    manager: GameProcessManager,
    tournament_id: str | None,
    usage_cache: _UsageCache,
) -> GameSummary | None:
    journal_path = game_directory / "game.jsonl"
    if not journal_path.is_file():
        launch_path = game_directory / "launch.json"
        if not launch_path.is_file():
            return None
        launch = NewGameRequest.model_validate_json(launch_path.read_text(encoding="utf-8"))
        return GameSummary(
            id=game_directory.name, players=launch.players, seed=launch.seed,
            max_turns=launch.max_turns or load_settings().max_turns,
            has_commentary=bool(launch.commentators), has_evaluation=False, finished=False,
            running=manager.is_running(game_directory.name), queued=manager.is_queued(game_directory.name),
            started=False, winner=None, turn_count=None, input_tokens=None, output_tokens=None, cost_usd=None,
            modified_at=datetime.fromtimestamp(launch_path.stat().st_mtime, tz=UTC), tournament_id=tournament_id,
        )
    with journal_path.open(encoding="utf-8") as source:
        try:
            require_v3(json.loads(source.readline()))
        except (ValueError, KeyError):
            return None
    summary = _summarize_journal(
        path=journal_path,
        game_id=game_directory.name,
        running=manager.is_running(game_id=game_directory.name),
        tournament_id=tournament_id,
        usage=_usage_totals(journal_path=journal_path, cache=usage_cache),
    )

    if summary is not None:
        summary.queued = manager.is_queued(game_directory.name)
    return summary


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
    if request.experiment_player is not None:
        # Freeze once before queuing. Continuation and replay reuse this snapshot even if files change.
        selection = request.experiment_player
        request = request.model_copy(update={
            "experiment_player": selection.model_copy(update={"snapshot": load_profile(profile_id=selection.profile)}),
        })
    orders = list(permutations(range(1, len(request.players) + 1))) if request.seating_mode == "cycle" else []
    request = request.model_copy(update={
        "seating_orders": [list(orders[index % len(orders)]) for index in range(request.games)] if orders else None,
    })
    games_directory.mkdir(parents=True, exist_ok=True)
    base_id = _new_game_id(games_directory=games_directory, seed=request.seed)

    if request.games == 1:
        game_directory = games_directory / base_id
        _start_game_worker(game_directory=game_directory, manager=manager, request=request, seed=request.seed)
        _wait_for_journal_header(game_directory=game_directory, manager=manager)
        return NewGameResponse(id=base_id, tournament=False)

    # a tournament: the same setup replicated across workers on consecutive seeds,
    # each game in its own folder inside the tournament's folder
    tournament_directory = games_directory / base_id
    tournament_directory.mkdir(parents=True, exist_ok=True)
    game_ids = [f"{base_id}-g{index}" for index in range(1, request.games + 1)]
    manifest = {
        "id": base_id,
        "players": request.players,
        "game_ids": game_ids,
        "parallel_games": min(request.parallel_games, request.games),
        "seating_mode": request.seating_mode,
        "seating_orders": request.seating_orders,
        "seeds": [request.seed + index for index in range(request.games)],
        "created_at": datetime.now(tz=UTC).isoformat(),
        "launch_config": request.model_copy(update={"games": 1, "seating_orders": None}).model_dump(),
    }
    (tournament_directory / "tournament.json").write_text(json.dumps(manifest), encoding="utf-8")
    manager.register_tournament(base_id, game_ids, min(request.parallel_games, request.games))
    for index, game_id in enumerate(game_ids):
        game_request = request.model_copy(update={
            "games": 1,
            "seating_orders": [request.seating_orders[index]] if request.seating_orders is not None else None,
        })
        _start_game_worker(
            game_directory=tournament_directory / game_id, manager=manager, request=game_request,
            seed=request.seed + index
        )
    for game_id in game_ids[:min(request.parallel_games, request.games)]:
        game_directory = tournament_directory / game_id
        _wait_for_journal_header(game_directory=game_directory, manager=manager)
    return NewGameResponse(id=base_id, tournament=True)


def _start_game_worker(game_directory: Path, manager: GameProcessManager, request: NewGameRequest, seed: int) -> None:
    _prepare_game_launch(game_directory, request, seed)
    _launch_prepared_game(game_directory, manager, request, seed)


def _prepare_game_launch(game_directory: Path, request: NewGameRequest, seed: int) -> None:
    game_directory.mkdir(parents=True, exist_ok=True)
    # the launch record keeps the game's own seed so resume sees the game, not the tournament
    launch_record = request.model_copy(update={"seed": seed, "games": 1, "parallel_games": 1})
    (game_directory / "launch.json").write_text(launch_record.model_dump_json(), encoding="utf-8")
    configure_evaluation(game_directory / "game.jsonl", request.evaluation_method, request.evaluators)


def _launch_prepared_game(
    game_directory: Path, manager: GameProcessManager, request: NewGameRequest, seed: int
) -> None:
    manager.launch_game(
        game_id=game_directory.name,
        players=request.players,
        seed=seed,
        commentators=request.commentators,
        max_turns=request.max_turns,
        additional_context=_starting_context(request=request),
        journal_path=game_directory / "game.jsonl",
        log_path=game_directory / "worker.log",
        seat_order=request.seating_orders[0] if request.seating_orders is not None else None,
        experiment_player=request.experiment_player,
    )


def _continue_tournament(directory: Path, manager: GameProcessManager, extension: ContinueTournamentRequest) -> dict:
    manifest_path = directory / "tournament.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    previous_count = len(manifest["game_ids"])
    if extension.expected_total_games != previous_count:
        raise HTTPException(status_code=409, detail="The tournament changed. Refresh before adding more games.")
    if previous_count + extension.games > 1000:
        raise HTTPException(status_code=400, detail="A tournament can contain at most 1000 games.")
    config = manifest.get("launch_config")
    if config is None:
        # Tournaments created before continuation saved the setup in each game's launch record.
        launch_path = next((directory / game / "launch.json" for game in manifest["game_ids"]
                            if (directory / game / "launch.json").is_file()), None)
        if launch_path is None:
            raise HTTPException(status_code=409, detail="The original launch configuration is missing.")
        config = json.loads(launch_path.read_text(encoding="utf-8"))
    try:
        request = NewGameRequest.model_validate(config)
    except ValidationError:
        raise HTTPException(status_code=409, detail="The saved launch configuration is invalid.") from None
    request = request.model_copy(update={
        "games": 1, "seating_orders": None,
        "seating_mode": manifest.get("seating_mode", request.seating_mode),
        "parallel_games": manifest.get("parallel_games", previous_count),
    })
    _validate_launch_request(request)
    seeds = manifest.get("seeds")
    if seeds is None:
        try:
            seeds = [json.loads((directory / game / "launch.json").read_text())["seed"]
                     for game in manifest["game_ids"]]
        except (OSError, ValueError, KeyError):
            raise HTTPException(status_code=409, detail="The tournament's original seeds are missing.") from None
    if len(seeds) != previous_count or not seeds:
        raise HTTPException(status_code=409, detail="The tournament's saved seed schedule is incomplete.")
    new_seeds = list(range(max(seeds) + 1, max(seeds) + 1 + extension.games))
    game_ids = [f"{manifest['id']}-g{index + 1}" for index in range(previous_count, previous_count + extension.games)]
    if any((directory / game).exists() for game in game_ids):
        raise HTTPException(status_code=409, detail="A new game folder already exists; no games were added.")
    orders = list(permutations(range(1, len(request.players) + 1))) if request.seating_mode == "cycle" else []
    new_orders = [list(orders[index % len(orders)])
                  for index in range(previous_count, previous_count + extension.games)] if orders else None
    old_orders = manifest.get("seating_orders") or []
    if orders and len(old_orders) != previous_count:
        raise HTTPException(status_code=409, detail="The tournament's saved seating schedule is incomplete.")
    requests = [request.model_copy(update={"seating_orders": [new_orders[index]] if new_orders else None})
                for index in range(extension.games)]
    updated = {**manifest, "game_ids": manifest["game_ids"] + game_ids, "seeds": seeds + new_seeds,
               "seating_orders": old_orders + new_orders if new_orders else None,
               "seating_mode": request.seating_mode, "parallel_games": request.parallel_games,
               "launch_config": request.model_dump()}
    prepared = []
    temporary = manifest_path.with_suffix(f".{uuid.uuid4().hex}.tmp")
    try:
        # Persist all launch records before publishing membership or starting workers.
        # A restart after publication leaves every new game individually resumable.
        for game_id, game_request, seed in zip(game_ids, requests, new_seeds):
            game_directory = directory / game_id
            game_directory.mkdir()
            prepared.append(game_directory)
            _prepare_game_launch(game_directory, game_request, seed)
        temporary.write_text(json.dumps(updated), encoding="utf-8")
        temporary.replace(manifest_path)
    except Exception:
        for game_directory in prepared:
            shutil.rmtree(game_directory)
        raise
    finally:
        temporary.unlink(missing_ok=True)
    manager.register_tournament(manifest["id"], updated["game_ids"], request.parallel_games)
    for game_id, game_request, seed in zip(game_ids, requests, new_seeds):
        _launch_prepared_game(directory / game_id, manager, game_request, seed)
    return {"id": manifest["id"], "added_games": len(game_ids), "total_games": len(updated["game_ids"]),
            "game_ids": game_ids}


def _replay_unavailable_reason(directory: Path, game_id: str, manager: GameProcessManager) -> str | None:
    journal = directory / game_id / "game.jsonl"
    if manager.is_running(game_id) or manager.is_queued(game_id):
        return "Pause the game or cancel its queue entry before replaying it."
    if manager.is_evaluating(game_id) or review_activity_for_journal(journal):
        return "Wait for this game's evaluation to finish before replaying it."
    if not (journal.parent / "launch.json").is_file():
        return "The original launch configuration is missing."
    return None


def _add_tournament_replay_controls(view: dict, directory: Path, manager: GameProcessManager) -> None:
    manifest = json.loads((directory / "tournament.json").read_text())
    view["replay_slots"] = [
        {"slot": index + 1, "game_id": game_id,
         "reason": _replay_unavailable_reason(directory, game_id, manager)}
        for index, game_id in enumerate(manifest["game_ids"])
    ]
    view["previous_attempts"] = [
        {**entry, "available": (directory / entry["previous_game_id"] / "game.jsonl").is_file()}
        for entry in manifest.get("replays", [])
    ]


def _replay_tournament_game(directory: Path, manifest: dict, game_id: str, manager: GameProcessManager) -> dict:
    reason = _replay_unavailable_reason(directory, game_id, manager)
    if reason:
        raise HTTPException(status_code=409, detail=reason)
    source = directory / game_id
    try:
        request = NewGameRequest.model_validate_json((source / "launch.json").read_text())
    except (ValueError, OSError):
        raise HTTPException(status_code=409, detail="The saved launch configuration is invalid.") from None
    index = manifest["game_ids"].index(game_id)
    order = request.seating_orders[0] if request.seating_orders else None
    if manifest.get("seating_orders"):
        order = manifest["seating_orders"][index]
    seed = manifest["seeds"][index] if manifest.get("seeds") else request.seed
    snapshot = {}
    saved_snapshot = source / "replay-settings.json"
    if saved_snapshot.is_file():
        snapshot = json.loads(saved_snapshot.read_text())
    journal = source / "game.jsonl"
    if journal.is_file():
        try:
            with journal.open(encoding="utf-8") as stream:
                header = json.loads(stream.readline())
            require_v3(header)
            names = list(specs.named_specs(specs=request.players))
            if set(header["player_specs"]) != set(names):
                raise ValueError("Saved roster does not match the game")
            # Pin even randomly assigned seats to the actual original order.
            order = [names.index(name) + 1 for name in header["player_specs"]]
            seed = header["seed"]
            request = request.model_copy(update={"max_turns": header["max_turns"]})
            snapshot.update(header.get("configuration", {}).get("settings", {}))
            snapshot.update({key: header[key] for key in ("allow_private_messages", "max_negotiation_rounds")})
        except (ValueError, KeyError):
            raise HTTPException(
                status_code=409, detail="Cannot recover the original game's setup from its header."
            ) from None
    request = request.model_copy(update={"games": 1, "parallel_games": 1, "seed": seed,
                                         "seating_orders": [order] if order else None})
    _validate_launch_request(request)
    replay_id = f"{manifest['id']}-g{index + 1}-replay-{uuid.uuid4().hex[:8]}"
    destination = directory / replay_id
    destination.mkdir()
    manifest_path = directory / "tournament.json"
    temporary = manifest_path.with_suffix(f".{uuid.uuid4().hex}.tmp")
    try:
        _prepare_game_launch(destination, request, seed)
        if snapshot:
            (destination / "replay-settings.json").write_text(json.dumps({
                key: value for key, value in snapshot.items() if key in REPLAY_SETTING_FIELDS
            }), encoding="utf-8")
        replacement_ids = list(manifest["game_ids"])
        replacement_ids[index] = replay_id
        entry = {"slot": index + 1, "previous_game_id": game_id, "game_id": replay_id,
                 "created_at": datetime.now(tz=UTC).isoformat()}
        updated = {**manifest, "game_ids": replacement_ids, "replays": [*manifest.get("replays", []), entry]}
        temporary.write_text(json.dumps(updated), encoding="utf-8")
        temporary.replace(manifest_path)
    except Exception:
        shutil.rmtree(destination)
        raise
    finally:
        temporary.unlink(missing_ok=True)
    manager.register_tournament(manifest["id"], replacement_ids,
                                manifest.get("parallel_games", len(replacement_ids)))
    _launch_prepared_game(destination, manager, request, seed)
    return {"id": replay_id, "previous_game_id": game_id, "slot": index + 1,
            "total_games": len(replacement_ids), "queued": manager.is_queued(replay_id)}


def _starting_context(request: NewGameRequest) -> list[str]:
    # one line for everyone plus a per-player line, folded into the single block each player sees
    per_player = request.player_context or [""] * len(request.players)
    return ["\n\n".join(part for part in (request.common_context.strip(), own.strip()) if part) for own in per_player]


def _resume_game(games_directory: Path, manager: GameProcessManager, game_id: str, journal_path: Path) -> None:
    if manager.is_running(game_id=game_id) or manager.is_queued(game_id):
        raise HTTPException(status_code=409, detail="The game is already running or queued.")
    manifest_path = journal_path.parent.parent / "tournament.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manager.register_tournament(manifest["id"], manifest["game_ids"],
                                    manifest.get("parallel_games", len(manifest["game_ids"])))
    if not journal_path.exists():
        request = NewGameRequest.model_validate_json((journal_path.parent / "launch.json").read_text())
        _start_game_worker(journal_path.parent, manager, request, request.seed)
        if not manager.is_queued(game_id):
            _wait_for_journal_header(journal_path.parent, manager)
        return
    if manager.is_evaluating(game_id=game_id):
        raise HTTPException(
            status_code=409, detail="Wait for the evaluation replay to finish before resuming the game."
        )
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
    if not manager.is_queued(game_id):
        _confirm_worker_started(game_directory=game_directory, manager=manager)


def _configured_live_evaluators(journal_path: Path) -> list[str]:
    return configured_panel(journal_path, "live_rounds")


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
    if "human" in request.players and (request.games != 1 or request.parallel_games != 1):
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
