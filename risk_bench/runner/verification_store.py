"""Human reviews of complete research units, keyed within one immutable evaluation run."""

import json
import os
import tempfile
from pathlib import Path

from risk_bench.runner.control import journal_control_lock


def verifications_path_for_journal(journal_path: Path) -> Path:
    return journal_path.with_suffix(".verifications.jsonl")


def read_verifications(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _key(row):
    return row["evaluator_index"], row["unit_type"], row["unit_id"]


def _rewrite(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, encoding="utf-8", delete=False) as f:
        f.write("".join(json.dumps(row) + "\n" for row in rows))
        f.flush()
        os.fsync(f.fileno())
        temporary = f.name
    os.replace(temporary, path)


def write_verification(path: Path, verification: dict) -> None:
    with journal_control_lock(path):
        rows = [r for r in read_verifications(path) if _key(r) != _key(verification)]
        _rewrite(path, [*rows, verification])


def delete_verification(path: Path, evaluator_index: int, unit_type: str, unit_id: str) -> bool:
    with journal_control_lock(path):
        rows = read_verifications(path)
        remaining = [r for r in rows if _key(r) != (evaluator_index, unit_type, unit_id)]
        _rewrite(path, remaining)
        return len(remaining) != len(rows)
