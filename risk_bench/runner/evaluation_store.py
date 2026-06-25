"""Reading and writing the evaluator's sidecar artifact, ``game.eval.jsonl``.

The evaluation is a re-runnable *derived* artifact, so it lives next to the game
journal (like the trace sidecar) rather than inside it: the journal is append-only
and canonical, while a fresh evaluation simply overwrites this file. The format
mirrors the journal — a header line then one line per occurrence
(``eval_tag`` / ``eval_arc`` / ``eval_synthesis``), each stamped with the panel
member's index, flushed per line, read back tolerantly.

The header records ``coverage`` (the journal record and span counts the evaluation
was computed against) so a viewer can report "covers N of the journal's now-M
spans" after a paused game resumes; because span handles are append-stable, an
out-of-date evaluation is partial, never invalid.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from risk_bench.runner.evaluation import Arc, Evaluation, EvaluatorTag, Evidence, PlayerSynthesis

EVALUATION_SCHEMA_VERSION: Final[int] = 1


@dataclass(frozen=True)
class EvaluationHeader:
    """The first line of an evaluation sidecar: the panel and what it was computed against."""

    schema_version: int
    panel: list[str]  # one model spec per member, in panel order
    record_count: int  # journal records the evaluation covered
    span_count: int  # trace-index spans the evaluation covered
    created_at: str


def evaluation_path_for_journal(journal_path: Path) -> Path:
    """Derive the evaluation sidecar path that belongs to a game journal.

    Args:
        journal_path: The game's ``.jsonl`` journal path.

    Returns:
        The ``.eval.jsonl`` path next to the journal.
    """
    return journal_path.with_suffix(".eval.jsonl")


class EvaluationWriter:
    """Writes one evaluation sidecar, overwriting any prior run (latest panel wins)."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._file = path.open("w", encoding="utf-8")

    def write_header(self, panel: list[str], record_count: int, span_count: int) -> None:
        """Write the header line; must be the first write.

        Args:
            panel: The evaluator panel as one model spec per member, in panel order.
            record_count: The number of journal records this evaluation covered.
            span_count: The number of trace-index spans this evaluation covered.
        """
        self._write_line(
            record={
                "type": "eval_header",
                "schema_version": EVALUATION_SCHEMA_VERSION,
                "panel": panel,
                "coverage": {"record_count": record_count, "span_count": span_count},
                "created_at": datetime.now(tz=UTC).isoformat(),
            }
        )

    def write_evaluation(self, evaluator_index: int, evaluation: Evaluation) -> None:
        """Write one member's tags, arcs, and syntheses as separate stamped lines.

        Args:
            evaluator_index: The member's position in the panel roster (the agreement key).
            evaluation: The member's validated output.
        """
        stamp = {"evaluator_index": evaluator_index, "evaluator": evaluation.evaluator_spec}
        for tag in evaluation.tags:
            self._write_line(record={"type": "eval_tag", **stamp, **_tag_to_json(tag=tag)})
        for arc in evaluation.arcs:
            self._write_line(record={"type": "eval_arc", **stamp, **_arc_to_json(arc=arc)})
        for synthesis in evaluation.syntheses:
            self._write_line(record={"type": "eval_synthesis", **stamp, **_synthesis_to_json(synthesis=synthesis)})

    def close(self) -> None:
        """Close the underlying file."""
        self._file.close()

    def _write_line(self, record: dict[str, Any]) -> None:
        self._file.write(json.dumps(record) + "\n")
        self._file.flush()


def read_evaluation(path: Path) -> list[dict[str, Any]]:
    """Read an evaluation sidecar's records, tolerating a truncated final line.

    Args:
        path: The ``.eval.jsonl`` file to read; a missing file yields an empty list.

    Returns:
        The parsed records in file order (header first when present).
    """
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
    """Reconstruct the header from an evaluation sidecar's records.

    Args:
        records: The records as ``read_evaluation`` returns them.

    Returns:
        The header, or None when the records do not begin with one.
    """
    if not records or records[0].get("type") != "eval_header":
        return None
    data = records[0]
    coverage = data.get("coverage", {})
    return EvaluationHeader(
        schema_version=int(data.get("schema_version", EVALUATION_SCHEMA_VERSION)),
        panel=[str(spec) for spec in data.get("panel", [])],
        record_count=int(coverage.get("record_count", 0)),
        span_count=int(coverage.get("span_count", 0)),
        created_at=str(data.get("created_at", "")),
    )


def _tag_to_json(tag: EvaluatorTag) -> dict[str, Any]:
    return {
        "player": tag.player,
        "behavior": tag.behavior,
        "turn": tag.turn,
        "evidence": [_evidence_to_json(evidence=evidence) for evidence in tag.evidence],
        "confidence": tag.confidence,
        "note": tag.note,
    }


def _arc_to_json(arc: Arc) -> dict[str, Any]:
    return {
        "kind": arc.kind,
        "subject": arc.subject,
        "object": arc.object,
        "antecedent": _evidence_to_json(evidence=arc.antecedent),
        "consequent": _evidence_to_json(evidence=arc.consequent),
        "outcome": arc.outcome,
        "intent": arc.intent,
        "confidence": arc.confidence,
        "note": arc.note,
    }


def _synthesis_to_json(synthesis: PlayerSynthesis) -> dict[str, Any]:
    return {
        "player": synthesis.player,
        "profile": [
            {
                "dimension": dimension.dimension,
                "characterization": dimension.characterization,
                "evidence": [_evidence_to_json(evidence=evidence) for evidence in dimension.evidence],
            }
            for dimension in synthesis.profile
        ],
        "key_episodes": [_evidence_to_json(evidence=evidence) for evidence in synthesis.key_episodes],
        "summary": synthesis.summary,
    }


def _evidence_to_json(evidence: Evidence) -> dict[str, Any]:
    return {
        "span": evidence.span,
        "quote": evidence.quote,
        "resolved": evidence.resolved,
        "quote_matched": evidence.quote_matched,
    }
