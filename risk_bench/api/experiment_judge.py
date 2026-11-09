"""Routes that run the experiment player's single-call judge from the reader and serve its result."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException

from risk_bench import specs
from risk_bench.api.processes import GameProcessManager
from risk_bench.api.schemas import ExperimentJudgeRequest
from risk_bench.config import load_settings
from risk_bench.runner.control import journal_control_lock
from risk_bench.runner.judges import core as judge_core
from risk_bench.runner.judges import registry as judge_registry


def install_experiment_judge_routes(
    app: FastAPI, manager: GameProcessManager, journal_path: Callable[[str], Path]
) -> None:
    """Add the experiment judge's status and start routes to the web app.

    Args:
        app: The web app.
        manager: Owns the judge worker processes.
        journal_path: Resolves a game id to its journal, raising 404 for unknown games.
    """

    @app.get("/api/games/{game_id}/experiment-judge")
    def experiment_judge_status(game_id: str) -> dict[str, Any]:
        path = journal_path(game_id)
        judge = judge_for_journal(journal_path=path)
        if judge is None:
            return {"available": False, "judge": None, "running": False, "error": None, "assessment": None}
        return {
            "available": True,
            "judge": {"name": judge.name, "title": judge.title},
            "running": manager.is_running_experiment_judge(game_id=game_id),
            "error": _read_error(journal_path=path, judge=judge),
            "assessment": _read_sidecar(journal_path=path, judge=judge),
        }

    @app.post("/api/games/{game_id}/experiment-judge", status_code=202)
    def start_experiment_judge(game_id: str, request: ExperimentJudgeRequest) -> dict[str, bool]:
        path = journal_path(game_id)
        with journal_control_lock(path):
            judge = _check_judge_can_start(
                manager=manager, game_id=game_id, journal_path=path, evaluator=request.evaluator
            )
            judge_core.error_path_for_journal(journal_path=path, definition=judge).unlink(missing_ok=True)
            manager.run_experiment_judge(game_id=game_id, journal_path=path, evaluator_spec=request.evaluator)
        return {"running": True}


def judge_for_journal(journal_path: Path) -> judge_core.JudgeDefinition | None:
    """Return the judge for a game's experiment player, read from the journal header.

    Args:
        journal_path: The game's ``.jsonl`` journal.

    Returns:
        The experiment profile's judge, or None when the game has no judged experiment player.
    """
    with journal_path.open(encoding="utf-8") as journal:
        header = json.loads(journal.readline())
    experiment = header.get("configuration", {}).get("experiment_player") or {}
    return judge_registry.judge_for_profile(profile_id=experiment.get("id"))


def _read_error(journal_path: Path, judge: judge_core.JudgeDefinition) -> str | None:
    error_path = judge_core.error_path_for_journal(journal_path=journal_path, definition=judge)
    if not error_path.is_file():
        return None
    return json.loads(error_path.read_text(encoding="utf-8")).get("detail")


def _read_sidecar(journal_path: Path, judge: judge_core.JudgeDefinition) -> dict[str, Any] | None:
    sidecar_path = judge_core.sidecar_path_for_journal(journal_path=journal_path, definition=judge)
    if not sidecar_path.is_file():
        return None
    return json.loads(sidecar_path.read_text(encoding="utf-8"))


def _check_judge_can_start(
    manager: GameProcessManager, game_id: str, journal_path: Path, evaluator: str
) -> judge_core.JudgeDefinition:
    judge = judge_for_journal(journal_path=journal_path)
    if judge is None:
        raise HTTPException(status_code=409, detail="This game has no Prober, Deceiver or Gaslighter player.")
    if manager.is_queued(game_id) or manager.is_running(game_id=game_id):
        raise HTTPException(status_code=409, detail="Pause the game before running the judge.")
    if manager.is_running_experiment_judge(game_id=game_id):
        raise HTTPException(status_code=409, detail="The judge is already running for this game.")
    # build the client up front so a bad spec or a missing key fails here, before the worker starts
    settings = load_settings()
    try:
        specs.build_agent(spec=evaluator, settings=settings, max_tokens=settings.max_evaluator_tokens)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from None
    return judge
