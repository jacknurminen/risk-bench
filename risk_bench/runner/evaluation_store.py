"""V3 evaluation sidecars: versioned, source-bound research observations."""

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from risk_bench.runner.codebook_v4 import CODEBOOK_HASH, CODEBOOK_VERSION
from risk_bench.runner.evaluation import EvaluatedGame

EVALUATION_SCHEMA_VERSION = 6


@dataclass(frozen=True)
class EvaluationHeader:
    schema_version: int
    run_id: str
    panel: list[str]
    record_count: int
    span_count: int
    created_at: str
    source: dict = field(default_factory=dict)
    codebook_version: str = CODEBOOK_VERSION
    codebook_hash: str = CODEBOOK_HASH
    method: str = "whole_game"


def evaluation_path_for_journal(journal_path: Path) -> Path:
    return journal_path.with_suffix(".eval.jsonl")


def evaluation_trace_path_for_journal(journal_path: Path) -> Path:
    return journal_path.with_suffix(".eval.trace.jsonl")


class EvaluationWriter:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._file = path.open("w", encoding="utf-8")

    def write_header(self, panel: list[str], record_count: int, span_count: int, source: dict | None = None,
                     run_id: str | None = None) -> None:
        created = datetime.now(tz=UTC)
        self._write_line(
            {
                "type": "eval_header",
                "schema_version": EVALUATION_SCHEMA_VERSION,
                "experiment_version": 3,
                "method": "whole_game",
                "codebook_version": CODEBOOK_VERSION,
                "codebook_hash": CODEBOOK_HASH,
                "run_id": run_id or created.strftime("%Y%m%d-%H%M%S-%f"),
                "created_at": created.isoformat(),
                "panel": panel,
                "coverage": {"record_count": record_count, "span_count": span_count},
                "source": source or {},
            }
        )

    def write_evaluation(self, evaluator_index: int, evaluation: EvaluatedGame) -> None:
        for record in evaluation.records:
            self._write_line({**record, "evaluator_index": evaluator_index, "evaluator": evaluation.evaluator_spec})

    def close(self) -> None:
        self._file.close()

    def _write_line(self, record: dict) -> None:
        self._file.write(json.dumps(record) + "\n")
        self._file.flush()


def read_evaluation(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            break
    return records


def evaluation_header(records: list[dict[str, Any]]) -> EvaluationHeader | None:
    if not records or records[0].get("type") != "eval_header":
        return None
    data = records[0]
    if data.get("schema_version") not in {3, 4, 5, 6}:
        raise ValueError("Older evaluations belong to the v2 application.")
    coverage = data["coverage"]
    return EvaluationHeader(
        schema_version=data["schema_version"],
        run_id=data["run_id"],
        panel=data["panel"],
        record_count=coverage["record_count"],
        span_count=coverage["span_count"],
        created_at=data["created_at"],
        source=data.get("source", {}),
        codebook_version=data.get("codebook_version", ""),
        codebook_hash=data.get("codebook_hash", ""),
        method=data.get("method", "live_rounds" if data.get("live") else "whole_game"),
    )
