"""Reading and writing the analysis artifact, ``game.analysis.json``.

Like the evaluation sidecar, the analysis is a re-runnable *derived* artifact next to the game
journal: one JSON object, overwritten wholesale each run (latest wins). The path is derived from the
journal exactly as the other sidecars' are.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

from risk_bench.analysis import build


def analysis_path_for_journal(journal_path: Path) -> Path:
    """Derive the analysis artifact path that belongs to a game journal.

    Args:
        journal_path: The game's ``.jsonl`` journal path.

    Returns:
        The ``.analysis.json`` path next to the journal.
    """
    return journal_path.with_suffix(".analysis.json")


def write_analysis(journal_path: Path, eval_path: Path, analysis_path: Path) -> Path:
    """Compute the analysis for one evaluation run of a game and write its analysis artifact.

    Args:
        journal_path: The game journal the evaluation was computed against.
        eval_path: The run's evaluation sidecar to analyze.
        analysis_path: Where to write the analysis artifact (the flat path for the current run,
            or an archived version's ``game.analysis.<run_id>.json``).

    Returns:
        The path to the written analysis artifact.

    Raises:
        ValueError: If the journal has no header line or ``eval_path`` holds no evaluation.
    """
    analysis = build.build_analysis(
        journal_path=journal_path, eval_path=eval_path, generated_at=datetime.now(tz=UTC).isoformat()
    )
    analysis_path.parent.mkdir(parents=True, exist_ok=True)
    analysis_path.write_text(json.dumps(analysis, indent=2) + "\n", encoding="utf-8")
    return analysis_path
