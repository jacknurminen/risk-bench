"""The JSON view model the evaluation page renders, joining the sidecar to the trace index.

Mirrors ``snapshots.build_view_model``: it reads the journal once into the trace index (so a
citation handle resolves to its turn, step, and text), reads the ``game.eval.jsonl`` sidecar, and
**resolves every citation** into ``{span, step, turn, ...}`` so the page can show the cited line and
jump to it. It also computes the deterministic integrity rate, the cross-member agreement on tags,
and the per-player arc resolution tallies the report wants.
"""

from collections import Counter
from pathlib import Path
from typing import Any

from risk_bench import specs
from risk_bench.runner.commentary import BEHAVIORS, axis_and_valence
from risk_bench.runner.evaluation import ARC_OUTCOME_VALENCE
from risk_bench.runner.evaluation_store import (
    evaluation_header,
    evaluation_path_for_journal,
    read_evaluation,
)
from risk_bench.runner.journal import read_journal
from risk_bench.runner.trace_index import TraceSpan, build_trace_index
from risk_bench.runner.verification_store import read_verifications, verifications_path_for_journal

_VALENCE_BY_BEHAVIOR: dict[str, str | None] = {
    behavior: axis_and_valence(behavior=behavior)[1] for behavior in BEHAVIORS
}

# the full behaviour vocabulary, in canonical order, so the axis filters
# can offer exactly what the LLM panel uses — grouped by axis, coloured by valence, on the client
_VOCABULARY: list[dict[str, str | None]] = [
    {
        "behavior": behavior,
        "axis": axis_and_valence(behavior=behavior)[0],
        "valence": axis_and_valence(behavior=behavior)[1],
    }
    for behavior in BEHAVIORS
]


def build_evaluation_view_model(journal_path: Path) -> dict[str, Any]:
    """Build the evaluation page's view model from a game journal and its eval sidecar.

    Args:
        journal_path: The game journal to evaluate against.

    Returns:
        A JSON-serializable dict with the trace index, the panel roster, each member's
        resolved tags/arcs/syntheses, and the cross-member aggregates. ``has_evaluation`` is
        False (and members empty) when no sidecar exists yet — the page then shows only the
        configure-and-run controls.

    Raises:
        ValueError: If the file is not a game journal.
    """
    records = read_journal(path=journal_path)
    if not records or records[0].get("type") != "header":
        raise ValueError(f"{journal_path} is not a game journal (missing header line).")
    header = records[0]
    player_names = list(header["player_specs"])
    spans = build_trace_index(records=records)
    span_by_id = {span.span: span for span in spans}

    eval_records = read_evaluation(path=evaluation_path_for_journal(journal_path=journal_path))
    eval_header = evaluation_header(records=eval_records)
    roster = _roster(panel_specs=eval_header.panel if eval_header else [], eval_records=eval_records)
    verified_keys = _verified_tag_keys(journal_path=journal_path)
    members = _members(eval_records=eval_records, roster=roster, span_by_id=span_by_id, verified_keys=verified_keys)

    return {
        "title": journal_path.name,
        "players": [{"name": name, "seat": seat} for seat, name in enumerate(player_names)],
        "spans": [_span_view(span=span) for span in spans],
        "has_evaluation": eval_header is not None,
        "finished": any(record.get("type") == "result" for record in records),
        "journal_span_count": len(spans),
        "tag_valence": _VALENCE_BY_BEHAVIOR,
        "vocabulary": _VOCABULARY,
        "coverage": _coverage(eval_header=eval_header, journal_span_count=len(spans)),
        "panel": roster,
        "members": members,
        "annotators": _annotators(roster=roster),
        "aggregate": _aggregate(members=members, player_names=player_names),
    }


def _span_view(span: TraceSpan) -> dict[str, Any]:
    return {
        "span": span.span,
        "turn": span.turn,
        "step": span.step,
        "layer": span.layer,
        "player": span.player,
        "text": span.text,
    }


def _coverage(eval_header: Any, journal_span_count: int) -> dict[str, Any] | None:
    if eval_header is None:
        return None
    return {
        "record_count": eval_header.record_count,
        "span_count": eval_header.span_count,
        "created_at": eval_header.created_at,
        # the journal may have grown since (a paused game resumed), making the evaluation partial
        "partial": eval_header.span_count < journal_span_count,
    }


def _roster(panel_specs: list[str], eval_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if panel_specs:
        members: list[tuple[int, str | None]] = list(enumerate(panel_specs))
    else:
        seen: dict[int, str | None] = {}
        for record in eval_records:
            index = record.get("evaluator_index")
            if isinstance(index, int):
                seen.setdefault(index, record.get("evaluator"))
        members = sorted(seen.items())
    labels = _member_labels(member_specs=[spec for _, spec in members])
    return [{"index": index, "spec": spec, "label": label} for (index, spec), label in zip(members, labels)]


def _member_labels(member_specs: list[str | None]) -> list[str]:
    resolved = [_resolve_member_label(spec=spec) for spec in member_specs]
    base_counts = Counter(resolved)
    # the same model run at two efforts shares a base label; append its index to keep members distinct
    return [base if base_counts[base] == 1 else f"{base} ·{index}" for index, base in enumerate(resolved)]


def _resolve_member_label(spec: str | None) -> str:
    if not spec:
        return "evaluator"
    try:
        return specs.resolve_model(spec=spec)
    except ValueError:
        return spec


def _annotators(roster: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # the roster the filter UI and compare matrix iterate over
    return [{"index": member["index"], "label": member["label"], "spec": member["spec"]} for member in roster]


def _verified_tag_keys(journal_path: Path) -> set[tuple[int, str, str, int]]:
    records = read_verifications(path=verifications_path_for_journal(journal_path=journal_path))
    return {(record["evaluator_index"], record["player"], record["behavior"], record["turn"]) for record in records}


def _members(
    eval_records: list[dict[str, Any]],
    roster: list[dict[str, Any]],
    span_by_id: dict[int, TraceSpan],
    verified_keys: set[tuple[int, str, str, int]],
) -> list[dict[str, Any]]:
    members = []
    for member in roster:
        index = member["index"]
        records = [record for record in eval_records if record.get("evaluator_index") == index]
        members.append(
            {
                "index": index,
                "label": member["label"],
                "tags": [
                    _tag_view(record=record, span_by_id=span_by_id, verified_keys=verified_keys)
                    for record in records
                    if record["type"] == "eval_tag"
                ],
                "arcs": [
                    _arc_view(record=record, span_by_id=span_by_id)
                    for record in records
                    if record["type"] == "eval_arc"
                ],
                "syntheses": [
                    _synthesis_view(record=record, span_by_id=span_by_id)
                    for record in records
                    if record["type"] == "eval_synthesis"
                ],
            }
        )
    return members


def _tag_view(
    record: dict[str, Any], span_by_id: dict[int, TraceSpan], verified_keys: set[tuple[int, str, str, int]]
) -> dict[str, Any]:
    tag_key = (record["evaluator_index"], record["player"], record["behavior"], record["turn"])
    return {
        "player": record["player"],
        "behavior": record["behavior"],
        "turn": record["turn"],
        "confidence": record["confidence"],
        "note": record["note"],
        "verified": tag_key in verified_keys,
        "evidence": [_evidence_view(evidence=evidence, span_by_id=span_by_id) for evidence in record["evidence"]],
    }


def _arc_view(record: dict[str, Any], span_by_id: dict[int, TraceSpan]) -> dict[str, Any]:
    return {
        "kind": record["kind"],
        "subject": record["subject"],
        "object": record.get("object"),
        "outcome": record["outcome"],
        "valence": ARC_OUTCOME_VALENCE.get(record["outcome"], "other"),
        "intent": record.get("intent"),
        "confidence": record["confidence"],
        "note": record["note"],
        "antecedent": _evidence_view(evidence=record["antecedent"], span_by_id=span_by_id),
        "consequent": _evidence_view(evidence=record["consequent"], span_by_id=span_by_id),
    }


def _synthesis_view(record: dict[str, Any], span_by_id: dict[int, TraceSpan]) -> dict[str, Any]:
    return {
        "player": record["player"],
        "summary": record["summary"],
        "profile": [
            {
                "dimension": dimension["dimension"],
                "characterization": dimension["characterization"],
                "evidence": [
                    _evidence_view(evidence=evidence, span_by_id=span_by_id) for evidence in dimension["evidence"]
                ],
            }
            for dimension in record["profile"]
        ],
        "key_episodes": [
            _evidence_view(evidence=evidence, span_by_id=span_by_id) for evidence in record["key_episodes"]
        ],
    }


def _evidence_view(evidence: dict[str, Any], span_by_id: dict[int, TraceSpan]) -> dict[str, Any]:
    span = span_by_id.get(evidence["span"])
    return {
        "span": evidence["span"],
        "quote": evidence.get("quote"),
        "resolved": bool(evidence.get("resolved")),
        "quote_matched": evidence.get("quote_matched"),
        # the resolved position, so a chip can jump the trace to the cited line (None when broken)
        "step": span.step if span is not None else None,
        "turn": span.turn if span is not None else None,
    }


def _aggregate(members: list[dict[str, Any]], player_names: list[str]) -> dict[str, Any]:
    return {
        "integrity": _integrity(members=members),
        "agreement": _tag_agreement(members=members),
        "arc_resolution": _arc_resolution(members=members, player_names=player_names),
    }


def _integrity(members: list[dict[str, Any]]) -> dict[str, int]:
    total, broken, quotes, quote_mismatches = 0, 0, 0, 0
    for member in members:
        for evidence in _all_evidence(member=member):
            total += 1
            broken += 0 if evidence["resolved"] else 1
            if evidence["quote"] is not None:
                quotes += 1
                quote_mismatches += 0 if evidence["quote_matched"] else 1
    return {
        "total_citations": total,
        "broken_citations": broken,
        "quotes": quotes,
        "quote_mismatches": quote_mismatches,
    }


def _all_evidence(member: dict[str, Any]) -> list[dict[str, Any]]:
    evidence = [item for tag in member["tags"] for item in tag["evidence"]]
    for arc in member["arcs"]:
        evidence += [arc["antecedent"], arc["consequent"]]
    for synthesis in member["syntheses"]:
        evidence += [item for dimension in synthesis["profile"] for item in dimension["evidence"]]
        evidence += synthesis["key_episodes"]
    return evidence


def _tag_agreement(members: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # for each (player, behavior, turn) tagged by anyone, which members flagged it — the agreement signal
    flaggers: dict[tuple[str, str, int], set[int]] = {}
    for member in members:
        for tag in member["tags"]:
            key = (tag["player"], tag["behavior"], tag["turn"])
            flaggers.setdefault(key, set()).add(member["index"])
    return [
        {
            "player": player,
            "behavior": behavior,
            "turn": turn,
            "members": sorted(indices),
            "count": len(indices),
        }
        for (player, behavior, turn), indices in sorted(flaggers.items(), key=lambda item: (item[0][2], item[0][0]))
    ]


def _arc_resolution(members: list[dict[str, Any]], player_names: list[str]) -> dict[str, dict[str, int]]:
    # panel-summed arc outcome tallies per subject, the basis for resolution rates (promise-kept etc.)
    tallies = {name: {"arcs": 0, "honoured": 0, "broken": 0, "open": 0, "other": 0} for name in player_names}
    for member in members:
        for arc in member["arcs"]:
            subject = arc["subject"]
            if subject not in tallies:
                continue
            tallies[subject]["arcs"] += 1
            tallies[subject][arc["valence"]] += 1
    return tallies
