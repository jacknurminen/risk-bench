"""Saved evaluator configuration for live rounds or whole-game analysis."""

import json
import uuid
from pathlib import Path
from typing import Literal

EvaluationMethod = Literal["live_rounds", "whole_game"]
_FILES = {"live_rounds": "live-evaluators.json", "whole_game": "whole-game-evaluators.json"}


def configured_panel(journal_path: Path, method: EvaluationMethod) -> list[str]:
    try:
        return json.loads((journal_path.parent / _FILES[method]).read_text(encoding="utf-8")).get("evaluators", [])
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def configure_panel(journal_path: Path, method: EvaluationMethod, evaluators: list[str]) -> None:
    target = journal_path.parent / _FILES[method]
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps({"evaluators": evaluators}), encoding="utf-8")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def configure_evaluation(journal_path: Path, method: EvaluationMethod, evaluators: list[str]) -> None:
    """Select one automatic evaluation method, disabling the other."""
    configure_panel(journal_path, "whole_game" if method == "live_rounds" else "live_rounds", [])
    configure_panel(journal_path, method, evaluators)
