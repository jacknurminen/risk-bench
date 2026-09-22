"""The JSON view model the evaluation page renders, joining the sidecar to the trace index.

Mirrors ``snapshots.build_view_model``: it reads the journal once into the trace index (so a
citation handle resolves to its turn, step, and text), reads the ``game.eval.jsonl`` sidecar, and
**resolves every citation** into ``{span, step, turn, ...}`` so the page can show the cited line and
jump to it. It also computes the deterministic integrity rate and the cross-member agreement on tags.
"""

from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from risk_bench import specs
from risk_bench.analysis import metrics
from risk_bench.runner.commentary import BEHAVIOR_FAMILY, BEHAVIORS, SUBFAMILY, axis_and_valence
from risk_bench.runner.evaluation_store import evaluation_header, read_evaluation
from risk_bench.runner.evaluation_versions import EvaluationVersion, list_versions
from risk_bench.runner.journal import read_journal
from risk_bench.runner.trace_index import TraceSpan, build_trace_index
from risk_bench.runner.verification_store import read_verifications

_VALENCE_BY_BEHAVIOR: dict[str, str | None] = {
    behavior: axis_and_valence(behavior=behavior)[1] for behavior in BEHAVIORS
}

# the full behaviour vocabulary, in canonical order, so the axis filters can offer exactly what
# the LLM panel uses — grouped by axis, coloured by valence, on the client. Each entry also
# carries the behavior's projection to every code grain (subfamily, family), so the stats page
# can re-group tags at a coarser grain client-side without a server round-trip.
_VOCABULARY: list[dict[str, str | None]] = [
    {
        "behavior": behavior,
        "axis": axis_and_valence(behavior=behavior)[0],
        "valence": axis_and_valence(behavior=behavior)[1],
        "subfamily": SUBFAMILY[behavior],
        "family": BEHAVIOR_FAMILY[behavior],
    }
    for behavior in BEHAVIORS
]


def build_evaluation_view_model(journal_path: Path, version: EvaluationVersion | None) -> dict[str, Any]:
    """Build the evaluation page's view model for one evaluation version of a game.

    Args:
        journal_path: The game journal to evaluate against.
        version: The evaluation run to render (its own eval + verifications sidecars), or None to
            render no evaluation (the page then shows only the configure-and-run controls). The
            caller resolves this via ``evaluation_versions.version_for``; pass the current run for
            the default view.

    Returns:
        A JSON-serializable dict with the trace index, the panel roster, each member's
        resolved tags/syntheses, the cross-member aggregates, and the list of available
        evaluation versions (``version`` names the one rendered). ``has_evaluation`` is
        False (and members empty) when no evaluation exists yet.

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

    eval_records = read_evaluation(path=version.eval_path) if version is not None else []
    eval_header = evaluation_header(records=eval_records)
    roster = _roster(panel_specs=eval_header.panel if eval_header else [], eval_records=eval_records)
    verified_keys = _verified_tag_keys(path=version.verifications_path) if version is not None else set()
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
        "consideration": _consideration_records(eval_records=eval_records),
        "panel": roster,
        "members": members,
        "annotators": _annotators(roster=roster),
        "aggregate": _aggregate(members=members),
        # the runs the user can switch between; ``version`` is the one rendered here
        "versions": _versions(journal_path=journal_path),
        "version": version.run_id if version is not None else None,
    }


def _versions(journal_path: Path) -> list[dict[str, Any]]:
    return [
        {
            "run_id": version.run_id,
            "created_at": version.created_at,
            "is_current": version.is_current,
            "panel": version.panel,
            "labels": _member_labels(member_specs=version.panel),
        }
        for version in list_versions(journal_path=journal_path)
    ]


def _span_view(span: TraceSpan) -> dict[str, Any]:
    return {
        "span": span.span,
        "turn": span.turn,
        "step": span.step,
        "layer": span.layer,
        "player": span.player,
        "text": span.text,
        **({"origin": span.origin} if span.origin else {}),
        **({"inherited": True} if span.inherited else {}),
        **({"cursor": span.cursor} if span.cursor is not None else {}),
    }


def _consideration_records(eval_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # the per-(member, turn) forced-consideration bookkeeping, passed through for the stats
    # modal's coverage grid; empty for pre-v2 sidecars (no per-turn sweep ran)
    return [
        {
            "evaluator_index": record["evaluator_index"],
            "turn": record["turn"],
            "living": record.get("living", []),
            "tagged": record.get("tagged", []),
            "no_behaviour": record.get("no_behaviour", []),
            "missing": record.get("missing", []),
        }
        for record in eval_records
        if record.get("type") == "eval_coverage"
    ]


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


def _member_labels(member_specs: Sequence[str | None]) -> list[str]:
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


def _verified_tag_keys(path: Path) -> set[tuple[int, str, str, int]]:
    records = read_verifications(path=path)
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


def _aggregate(members: list[dict[str, Any]]) -> dict[str, Any]:
    # the integrity rate and cross-member agreement live in analysis.metrics, so the reader and the
    # post-eval analysis dump report the same numbers from one implementation. Agreement is matched
    # at (behavior, turn) — the 1:1 grain; coarser code grains over-credit matching (see metrics)
    return {
        "integrity": metrics.integrity(members=members),
        "agreement": metrics.tag_agreement(members=members, code_grain="behavior", locus_grain="turn"),
    }
