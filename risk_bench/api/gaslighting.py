"""Routes that run the single-call gaslighting judge from the reader and serve its result."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException

from risk_bench import specs
from risk_bench.api.processes import GameProcessManager
from risk_bench.api.schemas import GaslightingJudgeRequest
from risk_bench.config import load_settings
from risk_bench.runner import gaslighting_evaluation
from risk_bench.runner.control import journal_control_lock


def install_gaslighting_routes(app: FastAPI, manager: GameProcessManager, journal_path: Callable[[str], Path]) -> None:
    """Add the gaslighting judge's status and start routes to the web app.

    Args:
        app: The web app.
        manager: Owns the judge worker processes.
        journal_path: Resolves a game id to its journal, raising 404 for unknown games.
    """

    @app.get("/api/games/{game_id}/gaslighting")
    def gaslighting_status(game_id: str) -> dict[str, Any]:
        path = journal_path(game_id)
        return {
            "available": has_gaslighter(journal_path=path),
            "running": manager.is_judging_gaslighting(game_id=game_id),
            "error": _read_error(journal_path=path),
            "assessment": _read_sidecar(journal_path=path),
        }

    @app.post("/api/games/{game_id}/gaslighting", status_code=202)
    def start_gaslighting_judge(game_id: str, request: GaslightingJudgeRequest) -> dict[str, bool]:
        path = journal_path(game_id)
        with journal_control_lock(path):
            _check_judge_can_start(manager=manager, game_id=game_id, journal_path=path, evaluator=request.evaluator)
            gaslighting_evaluation.gaslighting_error_path_for_journal(journal_path=path).unlink(missing_ok=True)
            manager.judge_gaslighting(game_id=game_id, journal_path=path, evaluator_spec=request.evaluator)
        return {"running": True}


def has_gaslighter(journal_path: Path) -> bool:
    """Tell whether a game's experiment player uses the Gaslighter profile.

    Args:
        journal_path: The game's ``.jsonl`` journal.

    Returns:
        True when the journal header records a Gaslighter experiment player.
    """
    with journal_path.open(encoding="utf-8") as journal:
        header = json.loads(journal.readline())
    experiment = header.get("configuration", {}).get("experiment_player") or {}
    return experiment.get("id") == gaslighting_evaluation.GASLIGHTER_PROFILE_ID


def _read_error(journal_path: Path) -> str | None:
    error_path = gaslighting_evaluation.gaslighting_error_path_for_journal(journal_path=journal_path)
    if not error_path.is_file():
        return None
    return json.loads(error_path.read_text(encoding="utf-8")).get("detail")


def _read_sidecar(journal_path: Path) -> dict[str, Any] | None:
    sidecar_path = gaslighting_evaluation.gaslighting_path_for_journal(journal_path=journal_path)
    if not sidecar_path.is_file():
        return None
    return json.loads(sidecar_path.read_text(encoding="utf-8"))


def _check_judge_can_start(manager: GameProcessManager, game_id: str, journal_path: Path, evaluator: str) -> None:
    if not has_gaslighter(journal_path=journal_path):
        raise HTTPException(status_code=409, detail="This game has no Gaslighter experiment player.")
    if manager.is_queued(game_id) or manager.is_running(game_id=game_id):
        raise HTTPException(status_code=409, detail="Pause the game before running the gaslighting judge.")
    if manager.is_judging_gaslighting(game_id=game_id):
        raise HTTPException(status_code=409, detail="The gaslighting judge is already running for this game.")
    # build the client up front so a bad spec or a missing key fails here, before the worker starts
    settings = load_settings()
    try:
        specs.build_agent(spec=evaluator, settings=settings, max_tokens=settings.max_evaluator_tokens)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from None
