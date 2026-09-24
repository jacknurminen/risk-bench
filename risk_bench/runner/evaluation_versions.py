"""Versioned access to a game's evaluation runs.

An evaluation is a re-runnable derived artifact; re-running once overwrote it. To keep old runs
accessible, each run is a *version*, stored **archive-in-place**: the newest run always lives at
the canonical flat sidecar paths (``game.eval.jsonl`` and its trace / analysis / verifications
companions), and older runs are archived beside it under a run-id infix (``game.eval.<run_id>.jsonl``,
``game.analysis.<run_id>.json``, ...). So the current run reads from exactly the same paths as
before — nothing moves until a re-run happens — and a run's tags, evaluator trace, analysis, and
human verifications are versioned together.

A run id includes the timestamp and microseconds. It is stored in the evaluation header;
archive collisions receive a numeric suffix without changing their contents.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from risk_bench.runner.evaluation_store import EvaluationHeader, evaluation_header, read_evaluation

_RUN_ID_PATTERN: re.Pattern[str] = re.compile(r"^\d{8}-\d{6}(?:-\d+)*$")


@dataclass(frozen=True)
class EvaluationVersion:
    """One evaluation run and the four sidecar paths that belong to it."""

    run_id: str
    created_at: str
    panel: list[str]
    is_current: bool  # whether this run occupies the canonical flat paths (the newest run)
    eval_path: Path
    trace_path: Path
    analysis_path: Path
    verifications_path: Path


def list_versions(journal_path: Path) -> list[EvaluationVersion]:
    """List a game's evaluation versions, newest first (the current run leads).

    Args:
        journal_path: The game's ``.jsonl`` journal path.

    Returns:
        Every evaluation version present, current run first then archived runs by descending run id;
        empty when the game has never been evaluated.
    """
    versions = []
    current = _current_version(journal_path=journal_path)
    if current is not None:
        versions.append(current)
    versions.extend(_archived_versions(journal_path=journal_path))
    return versions


def version_for(journal_path: Path, run_id: str | None) -> EvaluationVersion | None:
    """Resolve one evaluation version by run id, or the current run when ``run_id`` is None.

    Args:
        journal_path: The game's ``.jsonl`` journal path.
        run_id: The run to resolve, or None for the current (newest) run.

    Returns:
        The matching version, or None when it does not exist (or no evaluation exists at all).
    """
    versions = list_versions(journal_path=journal_path)
    if run_id is None:
        return versions[0] if versions else None
    return next((version for version in versions if version.run_id == run_id), None)


def archive_current(journal_path: Path) -> str | None:
    """Move the current run's sidecars aside under their run id, freeing the flat paths for a re-run.

    Called at the start of a re-run so the previous run survives as an archived version rather than
    being overwritten. A no-op when the game has never been evaluated.

    Args:
        journal_path: The game's ``.jsonl`` journal path.

    Returns:
        The archived run id, or None when there was no current evaluation to archive.
    """
    current = _current_version(journal_path=journal_path)
    if current is None:
        return None
    run_id = _unique_archived_run_id(journal_path=journal_path, run_id=current.run_id)
    archived = _paths(journal_path=journal_path, run_id=run_id)
    for source, destination in (
        (current.eval_path, archived.eval_path),
        (current.trace_path, archived.trace_path),
        (current.analysis_path, archived.analysis_path),
        (current.verifications_path, archived.verifications_path),
    ):
        if source.is_file():
            source.replace(destination)
    return run_id


@dataclass(frozen=True)
class _VersionPaths:
    eval_path: Path
    trace_path: Path
    analysis_path: Path
    verifications_path: Path


def _paths(journal_path: Path, run_id: str | None) -> _VersionPaths:
    # the flat (current) sidecar names when run_id is None, else the same names with a .<run_id> infix.
    # mirrors evaluation_store / verification_store / analysis.store conventions without importing them,
    # so this low-level module stays free of the higher analysis layer.
    directory, stem = journal_path.parent, journal_path.stem
    infix = "" if run_id is None else f".{run_id}"
    return _VersionPaths(
        eval_path=directory / f"{stem}.eval{infix}.jsonl",
        trace_path=directory / f"{stem}.eval{infix}.trace.jsonl",
        analysis_path=directory / f"{stem}.analysis{infix}.json",
        verifications_path=directory / f"{stem}.verifications{infix}.jsonl",
    )


def _current_version(journal_path: Path) -> EvaluationVersion | None:
    paths = _paths(journal_path=journal_path, run_id=None)
    if not paths.eval_path.is_file():
        return None
    header = evaluation_header(records=read_evaluation(path=paths.eval_path))
    return _version(paths=paths, header=header, run_id=_run_id_of(header=header), is_current=True)


def _archived_versions(journal_path: Path) -> list[EvaluationVersion]:
    directory, stem = journal_path.parent, journal_path.stem
    versions = []
    for eval_file in directory.glob(f"{stem}.eval.*.jsonl"):
        run_id = _archived_run_id(eval_file_name=eval_file.name, stem=stem)
        if run_id is None:
            continue  # e.g. game.eval.trace.jsonl or a run's own .trace.jsonl — not an archived eval
        paths = _paths(journal_path=journal_path, run_id=run_id)
        header = evaluation_header(records=read_evaluation(path=paths.eval_path))
        versions.append(_version(paths=paths, header=header, run_id=run_id, is_current=False))
    versions.sort(key=lambda version: version.run_id, reverse=True)
    return versions


def _archived_run_id(eval_file_name: str, stem: str) -> str | None:
    prefix, suffix = f"{stem}.eval.", ".jsonl"
    if not eval_file_name.startswith(prefix) or not eval_file_name.endswith(suffix):
        return None
    candidate = eval_file_name[len(prefix) : -len(suffix)]
    return candidate if _RUN_ID_PATTERN.match(candidate) else None


def _unique_archived_run_id(journal_path: Path, run_id: str) -> str:
    candidate, suffix = run_id, 2
    while _paths(journal_path=journal_path, run_id=candidate).eval_path.exists():
        candidate = f"{run_id}-{suffix}"
        suffix += 1
    return candidate


def _version(
    paths: _VersionPaths, header: EvaluationHeader | None, run_id: str, is_current: bool
) -> EvaluationVersion:
    return EvaluationVersion(
        run_id=run_id,
        created_at=header.created_at if header else "",
        panel=header.panel if header else [],
        is_current=is_current,
        eval_path=paths.eval_path,
        trace_path=paths.trace_path,
        analysis_path=paths.analysis_path,
        verifications_path=paths.verifications_path,
    )


def _run_id_of(header: EvaluationHeader | None) -> str:
    return header.run_id if header else "unknown"
