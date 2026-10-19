"""Incremental, resumable evaluation of completed game rounds.

The current sidecar is a complete snapshot at one committed round. Replacing it
atomically keeps readers from seeing half an update, and a failed provider call
leaves the previous cutoff intact so the same round can be retried.
"""

import json
import os
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from risk_bench import specs
from risk_bench.agents.tracing import TraceWriter
from risk_bench.config import Settings
from risk_bench.runner.codebook_v4 import CODEBOOK_HASH, CODEBOOK_VERSION
from risk_bench.runner.evaluation_store import (
    EVALUATION_SCHEMA_VERSION,
    evaluation_path_for_journal,
    evaluation_trace_path_for_journal,
    read_evaluation,
)
from risk_bench.runner.evaluation_versions import archive_current
from risk_bench.runner.journal import prefix_hash, read_journal
from risk_bench.runner.trace_index import build_trace_index, trace_fingerprint

LIVE_REVIEW_VERSION = "round-review-v3"


def review_activity_for_journal(journal_path: Path) -> dict | None:
    """Return the active round/final review, ignoring a marker left by a dead worker."""
    try:
        activity = json.loads(journal_path.with_suffix(".eval.active.json").read_text(encoding="utf-8"))
        pid = activity["pid"]
        if not isinstance(pid, int) or pid <= 0 or activity.get("kind") not in {"round", "final", "whole_game"}:
            return None
        if activity["kind"] == "round" and not isinstance(activity.get("round"), int):
            return None
        os.kill(pid, 0)
        return activity
    except (FileNotFoundError, json.JSONDecodeError, KeyError, OSError):
        return None


def _mark_review_active(
    journal_path: Path, *, kind: str, round_number: int | None = None, panel: list[str] | None = None,
) -> None:
    marker = journal_path.with_suffix(".eval.active.json")
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=marker.parent, prefix=".eval-active-", delete=False
    ) as file:
        temporary = Path(file.name)
        file.write(json.dumps({"pid": os.getpid(), "kind": kind, "round": round_number, "panel": panel}))
    try:
        temporary.replace(marker)
    finally:
        temporary.unlink(missing_ok=True)


def run_live_evaluation(
    journal_path: Path, evaluator_specs: list[str], settings: Settings, *, follow: bool = True,
    poll_seconds: float = 1.0, restart: bool = False, run_id: str | None = None,
) -> Path:
    """Catch up completed rounds, then follow the journal until the game ends."""
    if not evaluator_specs:
        raise ValueError("Provide at least one evaluator panel member.")
    path = evaluation_path_for_journal(journal_path)
    saved = read_evaluation(path)
    resumable = bool(
        not restart and saved and saved[0].get("live")
        and saved[0].get("live_review_version") == LIVE_REVIEW_VERSION
        and saved[0].get("panel") == evaluator_specs
        and saved[0].get("codebook_hash") == CODEBOOK_HASH
        and saved[-1].get("type") == "eval_live_cutoff"
    )
    if resumable:
        current_records = read_journal(journal_path)
        current_spans = build_trace_index(current_records)
        bound = saved[0]
        record_count = bound["coverage"]["record_count"]
        span_count = bound["coverage"]["span_count"]
        source = bound["source"]
        if (
            record_count > len(current_records) or span_count > len(current_spans)
            or source.get("journal_hash") != prefix_hash(current_records[:record_count])
            or source.get("trace_hash") != trace_fingerprint(current_spans[:span_count])
        ):
            raise ValueError("The saved live evaluation does not match this game's journal prefix.")
    if saved and not resumable:
        archive_current(journal_path)
    header: dict
    if resumable:
        header = saved[0]
        cutoff = saved[-1]
        last_turn = cutoff["turn"]
        last_round = cutoff["round"]
        finalized = bool(cutoff.get("finalized"))
        ledgers = {int(row["evaluator_index"]): row["ledger"] for row in saved if row["type"] == "eval_live_ledger"}
        coverage = [row for row in saved if row["type"] == "eval_message_coverage"]
        commentary = [row for row in saved if row["type"] == "eval_live_commentary"]
    else:
        now = datetime.now(tz=UTC)
        header = {
            "type": "eval_header", "schema_version": EVALUATION_SCHEMA_VERSION,
            "experiment_version": 3, "codebook_version": CODEBOOK_VERSION,
            "codebook_hash": CODEBOOK_HASH, "run_id": run_id or now.strftime("%Y%m%d-%H%M%S-%f"),
            "created_at": now.isoformat(), "panel": evaluator_specs, "live": True,
            "cadence": "round", "method": "live_rounds", "live_review_version": LIVE_REVIEW_VERSION,
            "coverage": {"record_count": 0, "span_count": 0},
            "source": {
                "game_id": journal_path.parent.name, "file": journal_path.name,
                "journal_hash": prefix_hash([]), "trace_hash": trace_fingerprint(()),
                "finished": False,
            },
        }
        last_turn, last_round, ledgers, coverage, commentary, finalized = 0, 0, {}, [], [], False
        # Keep the run identity (and its failed-call trace) even if the first call fails.
        _replace_jsonl(path, [header, {
            "type": "eval_live_cutoff", "round": 0, "turn": 0, "finalized": False,
        }])
    journal_path.with_suffix(".eval.active.json").unlink(missing_ok=True)
    trace = TraceWriter(path=evaluation_trace_path_for_journal(journal_path), append=resumable)
    try:
        evaluators = specs.build_evaluators(evaluator_specs=evaluator_specs, settings=settings, trace=trace)
        while True:
            records = read_journal(journal_path)
            spans = build_trace_index(records)
            finished = any(record["type"] == "result" for record in records)
            rounds = sorted({span.round for span in spans if span.turn > 0 and span.round is not None})
            completed = rounds if finished else rounds[:-1]
            for round_number in (number for number in completed if number > last_round):
                end_span = max(span.span for span in spans if span.round == round_number)
                prefix = spans[:end_span + 1]
                round_spans = spans[header["coverage"]["span_count"]:end_span + 1]
                if not round_spans:
                    continue
                turn = max(span.turn for span in spans if span.round == round_number)
                players = set(records[0]["player_specs"])
                new_ledgers = dict(ledgers)
                new_coverage = list(coverage)
                new_commentary = list(commentary)
                member_records = []
                # All members must succeed before this round's snapshot is committed.
                _mark_review_active(journal_path, kind="round", round_number=round_number)
                for index, evaluator in enumerate(evaluators):
                    trace.set_context(turn=turn, kind=f"evaluation-round-{round_number}[{index}]")
                    ledger, assessed = evaluator.evaluate_round(
                        players, prefix, round_spans, ledgers.get(index), round_number
                    )
                    new_ledgers[index] = ledger
                    new_coverage.extend(
                        {**row, "evaluator_index": index, "evaluator": evaluator.spec}
                        for row in assessed if row["type"] == "eval_message_coverage"
                    )
                    member_records.extend(
                        {**row, "evaluator_index": index, "evaluator": evaluator.spec}
                        for row in assessed if row["type"] not in {"eval_message_coverage", "eval_live_commentary"}
                    )
                    new_commentary.extend(
                        {**row, "evaluator_index": index, "evaluator": evaluator.spec}
                        for row in assessed if row["type"] == "eval_live_commentary"
                    )
                source_record = prefix[-1].record
                assert source_record is not None
                record_count = source_record + 1
                header["coverage"] = {"record_count": record_count, "span_count": len(prefix)}
                header["source"] = {
                    "game_id": journal_path.parent.name, "file": journal_path.name,
                    "trace_hash": trace_fingerprint(prefix),
                    "journal_hash": prefix_hash(records[:record_count]),
                    "ancestry": records[0].get("ancestry", {}),
                    "finished": finished and round_number == rounds[-1],
                }
                snapshot = [header, *new_coverage, *new_commentary, *member_records]
                snapshot.extend(
                    {"type": "eval_live_ledger", "evaluator_index": index, "ledger": ledger}
                    for index, ledger in sorted(new_ledgers.items())
                )
                snapshot.append({"type": "eval_live_cutoff", "round": round_number,
                                 "turn": turn, "finalized": False})
                _replace_jsonl(path, snapshot)
                journal_path.with_suffix(".eval.active.json").unlink(missing_ok=True)
                last_turn, last_round, ledgers, coverage, commentary = (
                    turn, round_number, new_ledgers, new_coverage, new_commentary
                )
            if finished:
                if not finalized:
                    result = next(record for record in reversed(records) if record["type"] == "result")
                    players = set(records[0]["player_specs"])
                    final_ledgers = dict(ledgers)
                    final_rows = []
                    _mark_review_active(journal_path, kind="final")
                    for index, evaluator in enumerate(evaluators):
                        trace.set_context(turn=last_turn, kind=f"evaluation-final[{index}]")
                        ledger, assessed = evaluator.finalize_game(players, spans, ledgers.get(index), result)
                        final_ledgers[index] = ledger
                        final_rows.extend(
                            {**row, "evaluator_index": index, "evaluator": evaluator.spec}
                            for row in assessed
                        )
                    header["coverage"] = {"record_count": len(records), "span_count": len(spans)}
                    header["source"] = {
                        "game_id": journal_path.parent.name, "file": journal_path.name,
                        "trace_hash": trace_fingerprint(spans), "journal_hash": prefix_hash(records),
                        "ancestry": records[0].get("ancestry", {}), "finished": True,
                    }
                    snapshot = [header, *coverage, *commentary, *final_rows]
                    snapshot.extend(
                        {"type": "eval_live_ledger", "evaluator_index": index, "ledger": ledger}
                        for index, ledger in sorted(final_ledgers.items())
                    )
                    snapshot.append({"type": "eval_live_cutoff", "round": last_round,
                                     "turn": last_turn, "finalized": True})
                    _replace_jsonl(path, snapshot)
                    journal_path.with_suffix(".eval.active.json").unlink(missing_ok=True)
                return path
            if not follow:
                return path
            time.sleep(poll_seconds)
    finally:
        journal_path.with_suffix(".eval.active.json").unlink(missing_ok=True)
        trace.close()


def _replace_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=".eval-", delete=False
    ) as file:
        temporary = Path(file.name)
        try:
            for record in records:
                file.write(json.dumps(record, ensure_ascii=False) + "\n")
            file.flush()
            os.fsync(file.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    temporary.replace(path)
