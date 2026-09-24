"""Resolve v3 research units and evidence against their original observation prefix."""

import copy
from dataclasses import asdict
from pathlib import Path

from risk_bench.runner.codebook import CODES, citations
from risk_bench.runner.evaluation_store import evaluation_header, read_evaluation
from risk_bench.runner.evaluation_validation import resolve_citation
from risk_bench.runner.evaluation_versions import list_versions
from risk_bench.runner.journal import prefix_hash, read_journal
from risk_bench.runner.trace_index import build_trace_index, trace_fingerprint
from risk_bench.runner.verification_store import read_verifications


def build_evaluation_view_model(journal_path: Path, version=None) -> dict:
    records = read_journal(journal_path)
    spans = build_trace_index(records)
    eval_records = read_evaluation(version.eval_path) if version else []
    header = evaluation_header(eval_records)
    count = header.span_count if header else len(spans)
    prefix = spans[:count]
    lookup = {s.span: s for s in prefix}
    source_matches = bool(
        header
        and count <= len(spans)
        and header.source.get("trace_hash") == trace_fingerprint(prefix)
        and header.source.get("game_id") == journal_path.parent.name
        and header.source.get("file") == journal_path.name
        and header.source.get("journal_hash") == prefix_hash(records[: header.record_count])
    )
    verified = read_verifications(version.verifications_path) if version else []
    keys = {(v["evaluator_index"], v["unit_type"], v["unit_id"]) for v in verified}
    members = []
    for index, spec in enumerate(header.panel if header else []):
        rows = [r for r in eval_records if r.get("evaluator_index") == index]
        member = {"index": index, "label": spec, "spec": spec}
        for plural, kind in (("agreements", "agreement"), ("episodes", "episode"), ("claims", "claim")):
            units = []
            for row in rows:
                if row["type"] != "eval_" + kind:
                    continue
                unit = copy.deepcopy(row)
                for cite in citations(unit):
                    cite.update(_evidence_view(cite, lookup, header.source if header else {}))
                if any(not c["resolved"] or c.get("quote_matched") is False for c in citations(unit)):
                    unit["valid"] = False
                    unit["issues"] = [*unit.get("issues", []), "Citation no longer resolves exactly"]
                unit["verified"] = (index, kind, unit["id"]) in keys
                if not source_matches:
                    unit["valid"] = False
                    unit["issues"] = [*unit.get("issues", []), "Source prefix does not match this evaluation"]
                units.append(unit)
            member[plural] = units
        screenings = {r["span"]: r for r in rows if r["type"] == "eval_message_coverage"}
        member["coverage"] = [
            screenings.get(s.span, {"span": s.span, "status": "missing", "explanation": "No screening record"})
            for s in prefix
            if s.layer == "message"
        ]
        member["issues"] = [r for r in rows if r["type"] == "eval_issue"]
        status = next((r for r in rows if r["type"] == "eval_member_status"), {})
        member["status"] = status.get("status", "failed")
        member["memos"] = status.get("memos", [])
        members.append(member)
    return {
        "title": journal_path.name,
        "game_id": journal_path.parent.name,
        "experiment_version": 3,
        "players": [{"name": p, "seat": i} for i, p in enumerate(records[0]["player_specs"])],
        "spans": [asdict(s) for s in spans],
        "has_evaluation": header is not None,
        "live_evaluation": bool(eval_records and eval_records[0].get("live")),
        "evaluated_through_turn": next(
            (r["turn"] for r in reversed(eval_records) if r["type"] == "eval_live_cutoff"), None
        ),
        "live_finalized": next(
            (bool(r.get("finalized")) for r in reversed(eval_records) if r["type"] == "eval_live_cutoff"), False
        ),
        "live_commentary": [r for r in eval_records if r["type"] == "eval_live_commentary"],
        "live_final": [r for r in eval_records if r["type"] == "eval_live_final"],
        "finished": any(r["type"] == "result" for r in records),
        "journal_span_count": len(spans),
        "codes": list(CODES),
        "members": members,
        "panel": [{"index": i, "spec": spec, "label": spec} for i, spec in enumerate(header.panel if header else [])],
        "annotators": [{"index": m["index"], "label": m["label"]} for m in members],
        "coverage": {
            "span_count": count,
            "record_count": header.record_count,
            "created_at": header.created_at,
            "partial": count < len(spans),
            "source_matches": source_matches,
        }
        if header
        else None,
        "codebook_version": header.codebook_version if header else None,
        "version": version.run_id if version else None,
        "versions": [
            dict(run_id=v.run_id, created_at=v.created_at, is_current=v.is_current, panel=v.panel, labels=v.panel)
            for v in list_versions(journal_path)
        ],
    }


def _evidence_view(evidence, span_by_id, source=None):
    s = span_by_id.get(evidence["span"])
    return {
        **resolve_citation(evidence, span_by_id),
        **(
            {
                "turn": s.turn,
                "round": s.round,
                "step": s.step,
                "layer": s.layer,
                "player": s.player,
                "text": s.text,
                "phase": s.phase,
                "record": s.record,
                "event_index": s.event_index,
                "decision_id": s.decision_id,
                "recipients": list(s.recipients),
                "visibility": s.visibility,
                "origin": s.origin,
                "data": s.data,
            }
            if s
            else {}
        ),
        "source": source or {},
    }
