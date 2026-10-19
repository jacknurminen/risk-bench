"""Saved tournament evaluation batches pin a particular version of every game."""

import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from risk_bench.runner.evaluation_versions import list_versions, version_for


def save_batch(path: Path, batch: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(batch), encoding="utf-8")
    temporary.replace(path)


def list_batches(directory: Path) -> list[dict]:
    batches = [json.loads(path.read_text()) for path in (directory / "evaluation-batches").glob("*.json")]
    return sorted(batches, key=lambda batch: (batch["created_at"], batch["method"] != "snapshot"), reverse=True)


def get_batch(directory: Path, version: str) -> dict:
    if not re.fullmatch(r"\d{8}-\d{6}-\d+(?:-\d+)?", version):
        raise ValueError("No such tournament evaluation version.")
    path = directory / "evaluation-batches" / f"{version}.json"
    if not path.is_file():
        raise ValueError("No such tournament evaluation version.")
    return json.loads(path.read_text())


def create_batch(directory: Path, manifest: dict, panel: list[str], live: bool) -> Path:
    now = datetime.now(tz=UTC)
    batch_id = now.strftime("%Y%m%d-%H%M%S-%f")
    # Capture the existing collection before the first bulk rerun archives any game.
    if not list_batches(directory):
        baseline = {"id": batch_id + "-0", "label": "Before tournament reruns",
                    "created_at": now.isoformat(), "method": "snapshot", "panel": [], "games": {}}
        for game_id in manifest["game_ids"]:
            latest = {}
            for version in list_versions(directory / game_id / "game.jsonl"):
                latest.setdefault(version.method, version.run_id)
            baseline["games"][game_id] = {"versions": list(latest.values()), "status": "saved"}
        save_batch(directory / "evaluation-batches" / f"{baseline['id']}.json", baseline)
    batch = {
        "id": batch_id, "created_at": now.isoformat(), "method": "live_rounds" if live else "whole_game",
        "panel": panel, "parallel_games": manifest.get("parallel_games", len(manifest["game_ids"])),
        "games": {game_id: {"versions": [batch_id], "status": "queued"} for game_id in manifest["game_ids"]},
    }
    path = directory / "evaluation-batches" / f"{batch_id}.json"
    save_batch(path, batch)
    return path


def interrupt_batch(path: Path) -> None:
    batch = json.loads(path.read_text())
    for job in batch["games"].values():
        if job["status"] in {"queued", "running"}:
            job.update(status="interrupted", error="Evaluation worker stopped before this game completed.")
    save_batch(path, batch)


def run_batch(path: Path, settings) -> None:
    """Bound parallel games, retaining failures while allowing the remaining jobs to finish."""
    from risk_bench.launch import run_evaluation
    from risk_bench.runner.evaluation_config import configure_evaluation
    from risk_bench.runner.live_evaluation import run_live_evaluation
    from risk_bench.viewer.evaluation import build_evaluation_view_model

    batch = json.loads(path.read_text())
    directory = path.parent.parent
    lock = threading.Lock()

    def update(game_id, **fields):
        with lock:
            batch["games"][game_id].update(fields)
            save_batch(path, batch)

    def evaluate(game_id):
        journal = directory / game_id / "game.jsonl"
        error_path = journal.with_suffix(".eval.error.json")
        update(game_id, status="running")
        try:
            error_path.unlink(missing_ok=True)
            configure_evaluation(journal, batch["method"], batch["panel"])
            if batch["method"] == "live_rounds":
                run_live_evaluation(journal, batch["panel"], settings, follow=False, restart=True, run_id=batch["id"])
            else:
                run_evaluation(journal, batch["panel"], settings, run_id=batch["id"])
            view = build_evaluation_view_model(journal, version_for(journal, batch["id"]))
            update(game_id, status=view["completion_status"], review_flags=view.get("review_flags", 0))
        except Exception as error:
            detail = f"{type(error).__name__}: {error}"
            error_path.write_text(json.dumps({"detail": detail}), encoding="utf-8")
            update(game_id, status="failed", error=detail)

    with ThreadPoolExecutor(max_workers=max(1, min(batch["parallel_games"], len(batch["games"])))) as pool:
        list(pool.map(evaluate, batch["games"]))
