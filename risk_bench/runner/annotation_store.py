"""Reading and writing the human-annotation sidecar, ``game.annotations.jsonl``.

Human annotations are the manual counterpart to the LLM evaluator panel: a person tags a
turn with a behaviour, a coding confidence, a rationale, and evidence citations into the
trace index — the same shape the panel produces. They live in their own sidecar next to the
journal rather than in ``game.eval.jsonl`` because that file is rewritten wholesale every time
the panel re-runs (latest panel wins), whereas annotations accrue and are edited one at a time
and must survive a re-evaluation.

The file is a plain list of ``annotation`` records, one JSON object per line, with no header.
Each mutation rewrites the whole file atomically (annotations are few and human-paced); a
citation is a ``span`` ordinal from the same append-stable trace index the evaluator cites, so
annotations stay valid as a paused game resumes.
"""

import json
import os
from pathlib import Path
from typing import Any


def annotations_path_for_journal(journal_path: Path) -> Path:
    """Derive the human-annotation sidecar path that belongs to a game journal.

    Args:
        journal_path: The game's ``.jsonl`` journal path.

    Returns:
        The ``.annotations.jsonl`` path next to the journal.
    """
    return journal_path.with_suffix(".annotations.jsonl")


def read_annotations(path: Path) -> list[dict[str, Any]]:
    """Read a sidecar's annotation records, tolerating a truncated final line.

    Args:
        path: The ``.annotations.jsonl`` file to read; a missing file yields an empty list.

    Returns:
        The parsed annotation records in file order.
    """
    if not path.is_file():
        return []
    annotations = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            break  # a partial trailing line from a crash or an in-flight write
        if record.get("type") == "annotation":
            annotations.append(record)
    return annotations


def write_annotation(path: Path, annotation: dict[str, Any]) -> None:
    """Insert or replace one annotation by id, rewriting the sidecar atomically.

    An existing record with the same ``id`` is replaced in place (so edits do not reorder the
    feed); a new id is appended.

    Args:
        path: The ``.annotations.jsonl`` sidecar to write.
        annotation: The annotation record to store.
    """
    existing = read_annotations(path=path)
    replaced = False
    result: list[dict[str, Any]] = []
    for record in existing:
        if record["id"] == annotation["id"]:
            result.append(annotation)
            replaced = True
        else:
            result.append(record)
    if not replaced:
        result.append(annotation)
    _rewrite(path=path, annotations=result)


def delete_annotation(path: Path, annotation_id: str) -> bool:
    """Remove one annotation by id, rewriting the sidecar atomically.

    Args:
        path: The ``.annotations.jsonl`` sidecar to write.
        annotation_id: The id of the annotation to remove.

    Returns:
        Whether an annotation with that id existed and was removed.
    """
    existing = read_annotations(path=path)
    kept = [record for record in existing if record["id"] != annotation_id]
    if len(kept) == len(existing):
        return False
    _rewrite(path=path, annotations=kept)
    return True


def _rewrite(path: Path, annotations: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(path.name + ".tmp")
    temp_path.write_text("".join(json.dumps(record) + "\n" for record in annotations), encoding="utf-8")
    os.replace(temp_path, path)  # atomic swap so a crash mid-write can never truncate the sidecar
