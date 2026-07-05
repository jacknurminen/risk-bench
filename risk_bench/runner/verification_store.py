"""Reading and writing the human tag-verification sidecar, ``game.verifications.jsonl``.

Humans no longer author their own tags; they verify the evaluator panel's. A verification
marks one panel tag — identified by ``(evaluator_index, player, behavior, turn)``, the same
key the agreement aggregate groups on — as human-checked. Verifications live in their own
sidecar next to the journal because ``game.eval.jsonl`` is rewritten wholesale every time the
panel re-runs, whereas verifications are made one at a time at human pace and must survive a
re-evaluation: a re-run that produces the same tag key keeps its verification, and a tag that
disappears simply leaves a record that no longer matches anything.

The file is a plain list of ``verification`` records, one JSON object per line, with no
header. Every mutation rewrites the whole file atomically (verifications are few and
human-paced), so what is read back is always the current set, not an event log.
"""

import json
import os
from pathlib import Path
from typing import Any


def verifications_path_for_journal(journal_path: Path) -> Path:
    """Derive the tag-verification sidecar path that belongs to a game journal.

    Args:
        journal_path: The game's ``.jsonl`` journal path.

    Returns:
        The ``.verifications.jsonl`` path next to the journal.
    """
    return journal_path.with_suffix(".verifications.jsonl")


def read_verifications(path: Path) -> list[dict[str, Any]]:
    """Read a sidecar's verification records, tolerating a truncated final line.

    Args:
        path: The ``.verifications.jsonl`` file to read; a missing file yields an empty list.

    Returns:
        The parsed verification records in file order.
    """
    if not path.is_file():
        return []
    verifications = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            break  # a partial trailing line from a crash or an in-flight write
        if record.get("type") == "verification":
            verifications.append(record)
    return verifications


def write_verification(path: Path, verification: dict[str, Any]) -> None:
    """Insert or replace one verification by its tag key, rewriting the sidecar atomically.

    Verifying an already-verified tag replaces the record in place (refreshing its
    timestamp) rather than duplicating it, so the file stays a set.

    Args:
        path: The ``.verifications.jsonl`` sidecar to write.
        verification: The verification record to store; must carry the tag-key fields.
    """
    existing = read_verifications(path=path)
    key = _tag_key(record=verification)
    kept = [record for record in existing if _tag_key(record=record) != key]
    _rewrite(path=path, verifications=[*kept, verification])


def delete_verification(path: Path, evaluator_index: int, player: str, behavior: str, turn: int) -> bool:
    """Remove one verification by its tag key, rewriting the sidecar atomically.

    Args:
        path: The ``.verifications.jsonl`` sidecar to write.
        evaluator_index: The panel member whose tag was verified.
        player: The tagged player.
        behavior: The tagged behaviour.
        turn: The tagged turn.

    Returns:
        Whether a verification with that key existed and was removed.
    """
    existing = read_verifications(path=path)
    key = (evaluator_index, player, behavior, turn)
    kept = [record for record in existing if _tag_key(record=record) != key]
    if len(kept) == len(existing):
        return False
    _rewrite(path=path, verifications=kept)
    return True


def _tag_key(record: dict[str, Any]) -> tuple[int, str, str, int]:
    return (record["evaluator_index"], record["player"], record["behavior"], record["turn"])


def _rewrite(path: Path, verifications: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(path.name + ".tmp")
    temp_path.write_text("".join(json.dumps(record) + "\n" for record in verifications), encoding="utf-8")
    os.replace(temp_path, path)  # atomic swap so a crash mid-write can never truncate the sidecar
