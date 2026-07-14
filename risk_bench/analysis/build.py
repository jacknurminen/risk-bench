"""Assemble ``game.analysis.json`` from the journal, trace index, and evaluation sidecar.

The whole object is derived and re-runnable: it reads the game journal (for the players, the
outcome, and the trace index the panel cited), the evaluation sidecar (the panel's tags and
syntheses), and reports integrity / agreement / descriptive statistics over them. Every citation is
**re-verified against the trace index** here rather than trusting the flags the evaluator recorded,
so a bug in the evaluator's own checker cannot hide inside these numbers.

The integrity and agreement maths live in ``analysis.metrics`` (shared with the reader); everything
below composes those primitives and adds the per-source, calibration, inventory, and journal-join
layers. Section builders are pure over their inputs so a later cross-game roll-up can call them.
"""

from collections import Counter
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, Final

from risk_bench.analysis import metrics
from risk_bench.runner.commentary import BEHAVIOR_AXIS, axis_and_valence
from risk_bench.runner.evaluation import WITHIN_TURN_GAP_BEHAVIORS
from risk_bench.runner.evaluation_store import EvaluationHeader, evaluation_header, read_evaluation
from risk_bench.runner.journal import read_journal
from risk_bench.runner.trace_index import TraceSpan, build_trace_index

SCHEMA_VERSION: Final[int] = 2  # v2 adds integrity.consideration (forced-consideration coverage)

# the four in-the-moment trace layers a divergence reconstructs for a (player, turn)
_DIVERGENCE_LAYERS: Final[tuple[str, ...]] = ("thought", "message", "action", "note")


def build_analysis(journal_path: Path, eval_path: Path, generated_at: str) -> dict[str, Any]:
    """Build the whole ``game.analysis.json`` object for one evaluation run of a game.

    Args:
        journal_path: The game journal the evaluation was computed against.
        eval_path: The run's evaluation sidecar to analyze — the flat ``game.eval.jsonl`` for the
            current run, or an archived version's ``game.eval.<run_id>.jsonl``.
        generated_at: The ISO-8601 timestamp to stamp the artifact with, supplied by the caller so
            this function stays pure and testable.

    Returns:
        The analysis object, ready to serialize to JSON.

    Raises:
        ValueError: If the journal has no header line or ``eval_path`` holds no evaluation.
    """
    records = read_journal(path=journal_path)
    if not records or records[0].get("type") != "header":
        raise ValueError(f"{journal_path} is not a game journal (missing header line).")
    player_specs = records[0]["player_specs"]
    result = next((record for record in records if record.get("type") == "result"), None)
    spans = build_trace_index(records=records)
    span_by_id = {span.span: span for span in spans}

    eval_records = read_evaluation(path=eval_path)
    header = evaluation_header(records=eval_records)
    if header is None:
        raise ValueError(f"{eval_path} holds no evaluation to analyze.")

    tags = [record for record in eval_records if record.get("type") == "eval_tag"]
    syntheses = [record for record in eval_records if record.get("type") == "eval_synthesis"]
    coverage_records = [record for record in eval_records if record.get("type") == "eval_coverage"]
    members = _reverified_members(tags=tags, syntheses=syntheses, panel=header.panel, span_by_id=span_by_id)
    player_providers = {player: _provider(spec=spec) for player, spec in player_specs.items()}

    return {
        "schema_version": SCHEMA_VERSION,
        "game_id": journal_path.parent.name,
        "generated_at": generated_at,
        "source": _source(header=header, result=result),
        "integrity": _integrity_section(members=members, span_count=len(spans), coverage_records=coverage_records),
        "agreement": _agreement_section(members=members),
        "calibration": _calibration_section(members=members),
        "sibling_leniency": _sibling_leniency_section(members=members, player_providers=player_providers),
        "behaviour_inventory": _behaviour_inventory_section(members=members),
        "divergences": _divergences_section(members=members, spans=spans),
        "player_profiles": _player_profiles_section(members=members, player_providers=player_providers),
    }


def _source(header: EvaluationHeader, result: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "panel": header.panel,
        "eval_created_at": header.created_at,
        "record_count": header.record_count,
        "span_count": header.span_count,
        "winner": result.get("winner") if result else None,
        "turn_count": result.get("turn_count") if result else None,
    }


# ---- integrity -------------------------------------------------------------------------------


def _integrity_section(
    members: list[dict[str, Any]], span_count: int, coverage_records: list[dict[str, Any]]
) -> dict[str, Any]:
    all_evidence = [citation for member in members for citation in metrics.evidence_of_member(member=member)]
    return {
        "panel": _integrity_block(evidence=all_evidence, span_count=span_count),
        # consideration-coverage is the headline coverage signal for per-turn evaluations: the
        # (turn, living-player) units the panel actually considered, and the missing ones. The
        # per-block citation ``coverage`` below stays as a secondary diagnostic.
        "consideration": _consideration(coverage_records=coverage_records, members=members),
        "by_member": [
            {
                "evaluator_index": member["index"],
                "evaluator": member["spec"],
                **_integrity_block(evidence=metrics.evidence_of_member(member=member), span_count=span_count),
            }
            for member in members
        ],
        "by_source": {
            "tag_evidence": _integrity_block(
                evidence=_across(members=members, extractor=metrics.tag_evidence), span_count=span_count
            ),
            "synthesis_profile_evidence": _integrity_block(
                evidence=_across(members=members, extractor=metrics.synthesis_profile_evidence), span_count=span_count
            ),
            "key_episodes": _integrity_block(
                evidence=_across(members=members, extractor=metrics.key_episode_evidence), span_count=span_count
            ),
        },
        "structural": _structural(members=members),
        "flags": _integrity_flags(members=members),
    }


def _across(members: list[dict[str, Any]], extractor: Callable[..., list[dict[str, Any]]]) -> list[dict[str, Any]]:
    return [citation for member in members for citation in extractor(member=member)]


def _consideration(coverage_records: list[dict[str, Any]], members: list[dict[str, Any]]) -> dict[str, Any]:
    # forced-consideration coverage from the eval_coverage records: ~100% by construction, with the
    # missing units the real integrity signal. Absent for pre-v2 sidecars (no per-turn sweep ran).
    if not coverage_records:
        return {
            "records_present": False,
            "total_units": 0,
            "considered_units": 0,
            "considered_fraction": 0.0,
            "no_behaviour_units": 0,
            "no_behaviour_rate": 0.0,
            "missing_units": 0,
            "missing": [],
            "by_member": [],
        }
    spec_by_index = {member["index"]: member["spec"] for member in members}
    total_units = considered_units = no_behaviour_units = 0
    missing: list[dict[str, Any]] = []
    per_member: dict[int, list[int]] = {}
    for record in coverage_records:
        evaluator_index = record["evaluator_index"]
        living = record.get("living", [])
        record_missing = record.get("missing", [])
        record_considered = len(living) - len(record_missing)
        total_units += len(living)
        considered_units += record_considered
        no_behaviour_units += sum(1 for player in record.get("no_behaviour", []) if player in living)
        totals = per_member.setdefault(evaluator_index, [0, 0, 0])
        totals[0] += len(living)
        totals[1] += record_considered
        totals[2] += len(record_missing)
        missing.extend(
            {"evaluator_index": evaluator_index, "turn": record["turn"], "player": player} for player in record_missing
        )
    return {
        "records_present": True,
        "total_units": total_units,
        "considered_units": considered_units,
        "considered_fraction": _rate(part=considered_units, whole=total_units),
        "no_behaviour_units": no_behaviour_units,
        "no_behaviour_rate": _rate(part=no_behaviour_units, whole=considered_units),
        "missing_units": total_units - considered_units,
        "missing": missing,
        "by_member": [
            {
                "evaluator_index": index,
                "evaluator": spec_by_index.get(index),
                "total_units": totals[0],
                "considered_units": totals[1],
                "missing_units": totals[2],
                "considered_fraction": _rate(part=totals[1], whole=totals[0]),
            }
            for index, totals in sorted(per_member.items())
        ],
    }


def _integrity_block(evidence: list[dict[str, Any]], span_count: int) -> dict[str, Any]:
    counts = metrics.integrity_of_evidence(evidence=evidence)
    distinct_spans = {citation["span"] for citation in evidence if citation["resolved"]}
    return {
        "total_citations": counts["total_citations"],
        "broken_citations": counts["broken_citations"],
        "broken_rate": _rate(part=counts["broken_citations"], whole=counts["total_citations"]),
        "quoted_citations": counts["quoted_citations"],
        "quote_mismatches": counts["quote_mismatches"],
        "quote_mismatch_rate": _rate(part=counts["quote_mismatches"], whole=counts["quoted_citations"]),
        "coverage": {
            "span_count": span_count,
            "distinct_spans_cited": len(distinct_spans),
            "coverage_fraction": _rate(part=len(distinct_spans), whole=span_count),
        },
    }


def _structural(members: list[dict[str, Any]]) -> dict[str, Any]:
    # the gap-code >=2-evidence rule and the "cite something" rule are prompt discipline, not
    # code-enforced at generation time, so surface any tag that violates them for a glance
    gap_code_underevidenced = []
    evidence_free_tags = []
    for member in members:
        for tag in member["tags"]:
            resolved_spans = [citation["span"] for citation in tag["evidence"] if citation["resolved"]]
            identity = {
                "evaluator_index": member["index"],
                "player": tag["player"],
                "behavior": tag["behavior"],
                "turn": tag["turn"],
            }
            if not resolved_spans:
                evidence_free_tags.append(identity)
            if tag["behavior"] in WITHIN_TURN_GAP_BEHAVIORS and len(resolved_spans) < 2:
                gap_code_underevidenced.append({**identity, "cited_spans": resolved_spans})
    return {"gap_code_underevidenced": gap_code_underevidenced, "evidence_free_tags": evidence_free_tags}


def _integrity_flags(members: list[dict[str, Any]]) -> dict[str, Any]:
    broken_citations: list[dict[str, Any]] = []
    mismatched_quotes: list[dict[str, Any]] = []
    for member in members:
        for tag in member["tags"]:
            for citation in tag["evidence"]:
                _collect_flags(
                    citation=citation,
                    evaluator_index=member["index"],
                    source="tag_evidence",
                    player=tag["player"],
                    behavior=tag["behavior"],
                    owner_turn=tag["turn"],
                    broken_citations=broken_citations,
                    mismatched_quotes=mismatched_quotes,
                )
        for synthesis in member["syntheses"]:
            profile_evidence = [citation for dimension in synthesis["profile"] for citation in dimension["evidence"]]
            for source, citations in (
                ("synthesis_profile_evidence", profile_evidence),
                ("key_episodes", synthesis["key_episodes"]),
            ):
                for citation in citations:
                    _collect_flags(
                        citation=citation,
                        evaluator_index=member["index"],
                        source=source,
                        player=synthesis["player"],
                        behavior=None,
                        owner_turn=None,
                        broken_citations=broken_citations,
                        mismatched_quotes=mismatched_quotes,
                    )
    return {"broken_citations": broken_citations, "mismatched_quotes": mismatched_quotes}


def _collect_flags(
    citation: dict[str, Any],
    evaluator_index: int,
    source: str,
    player: str,
    behavior: str | None,
    owner_turn: int | None,
    broken_citations: list[dict[str, Any]],
    mismatched_quotes: list[dict[str, Any]],
) -> None:
    if not citation["resolved"]:
        broken_citations.append(
            {
                "evaluator_index": evaluator_index,
                "source": source,
                "player": player,
                "behavior": behavior,
                "turn": owner_turn,
                "span": citation["span"],
            }
        )
    if citation["quote"] is not None and not citation["quote_matched"]:
        mismatched_quotes.append(
            {
                "evaluator_index": evaluator_index,
                "source": source,
                "span": citation["span"],
                "turn": citation["turn"],
                "quote": citation["quote"],
            }
        )


# ---- agreement -------------------------------------------------------------------------------


def _agreement_section(members: list[dict[str, Any]]) -> dict[str, Any]:
    strict_observations = metrics.tag_agreement(members=members, code_grain="behavior", locus_grain="turn")
    return {
        "strict": {
            "distribution": _distribution(counts=(observation["count"] for observation in strict_observations)),
            "observations": strict_observations,
        },
        "collapsed": _collapsed_agreement(members=members, grouping="axis", turn_window=1),
    }


def _collapsed_agreement(members: list[dict[str, Any]], grouping: str, turn_window: int) -> dict[str, Any]:
    # strict agreement (exact behavior + exact turn) is harsh; collapse the behavior to its axis and
    # bucket the turn into blocks of (window + 1) so members who saw the same kind of thing at nearly
    # the same time count as agreeing. Floor-division buckets form a clean partition (unlike a sliding
    # tolerance), which is what Fleiss' kappa needs as its item set.
    flaggers: dict[tuple[str, str, int], set[int]] = {}
    for member in members:
        for tag in member["tags"]:
            key = (tag["player"], BEHAVIOR_AXIS[tag["behavior"]], tag["turn"] // (turn_window + 1))
            flaggers.setdefault(key, set()).add(member["index"])
    ordered_keys = sorted(flaggers, key=lambda key: (key[2], key[0], key[1]))
    present_counts = [len(flaggers[key]) for key in ordered_keys]
    observations = [
        {
            "player": player,
            "group": group,
            "turn_bucket": bucket,
            "members": sorted(flaggers[(player, group, bucket)]),
            "count": len(flaggers[(player, group, bucket)]),
        }
        for (player, group, bucket) in ordered_keys
    ]
    return {
        "grouping": grouping,
        "turn_window": turn_window,
        "distribution": _distribution(counts=present_counts),
        "observations": observations,
        "fleiss_kappa": _fleiss_kappa(item_present_counts=present_counts, rater_count=len(members)),
    }


def _distribution(counts: Iterable[int]) -> dict[str, int]:
    counter = Counter(counts)
    return {str(level): counter[level] for level in sorted(counter)}


def _fleiss_kappa(item_present_counts: list[int], rater_count: int) -> float | None:
    # two categories (a collapsed item is present or absent for each member); each item is rated by
    # every member. Returns None when the statistic is undefined: fewer than two raters, no items, or
    # a degenerate distribution where chance agreement is already total.
    item_count = len(item_present_counts)
    if rater_count < 2 or item_count == 0:
        return None
    mean_item_agreement = 0.0
    total_present = 0
    for present in item_present_counts:
        absent = rater_count - present
        mean_item_agreement += (present * present + absent * absent - rater_count) / (rater_count * (rater_count - 1))
        total_present += present
    mean_item_agreement /= item_count
    fraction_present = total_present / (item_count * rater_count)
    chance_agreement = fraction_present * fraction_present + (1 - fraction_present) * (1 - fraction_present)
    if chance_agreement >= 1.0:
        return None
    return round((mean_item_agreement - chance_agreement) / (1 - chance_agreement), 4)


# ---- calibration -----------------------------------------------------------------------------


def _calibration_section(members: list[dict[str, Any]]) -> dict[str, Any]:
    return {"by_member": [_calibration_row(member=member) for member in members]}


def _calibration_row(member: dict[str, Any]) -> dict[str, Any]:
    counts = metrics.integrity_of_evidence(evidence=metrics.evidence_of_member(member=member))
    return {
        "evaluator_index": member["index"],
        "evaluator": member["spec"],
        "tag_count": len(member["tags"]),
        "citation_count": counts["total_citations"],
        "mean_confidence": _mean_confidence(tags=member["tags"]),
        "quote_mismatch_rate": _rate(part=counts["quote_mismatches"], whole=counts["quoted_citations"]),
    }


def _mean_confidence(tags: list[dict[str, Any]]) -> float:
    if not tags:
        return 0.0
    return round(sum(tag["confidence"] for tag in tags) / len(tags), 2)


# ---- sibling leniency ------------------------------------------------------------------------


def _sibling_leniency_section(members: list[dict[str, Any]], player_providers: dict[str, str]) -> dict[str, Any]:
    pairs = []
    for member in members:
        member_provider = _provider(spec=member["spec"])
        for player, player_provider in player_providers.items():
            player_tags = [tag for tag in member["tags"] if tag["player"] == player]
            pairs.append(
                {
                    "evaluator_index": member["index"],
                    "member_provider": member_provider,
                    "player": player,
                    "is_sibling": member_provider == player_provider,
                    "tag_count": len(player_tags),
                    "positive_tags": sum(
                        1 for tag in player_tags if _valence_sign(behavior=tag["behavior"]) == "positive"
                    ),
                    "negative_tags": sum(
                        1 for tag in player_tags if _valence_sign(behavior=tag["behavior"]) == "negative"
                    ),
                }
            )
    return {"pairs": pairs}


def _valence_sign(behavior: str) -> str:
    _, valence = axis_and_valence(behavior=behavior)
    if valence in ("strong", "cooperative"):
        return "positive"
    if valence in ("weak", "adversarial", "deceptive"):
        return "negative"
    return "neutral"  # affect behaviours are descriptive, neither positive nor negative


# ---- behaviour inventory ---------------------------------------------------------------------


def _behaviour_inventory_section(members: list[dict[str, Any]]) -> dict[str, Any]:
    # an instance is a distinct (player, behavior, turn): identical tags from several members collapse
    # into one, so the inventory counts what happened in the game, not how many members noticed it.
    # The dedup key is the shared grain projection in metrics, so counts and agreement can't diverge.
    instances = metrics.tag_agreement(members=members, code_grain="behavior", locus_grain="turn")
    by_behavior = Counter(instance["behavior"] for instance in instances)
    by_group = Counter(_group_of(behavior=instance["behavior"]) for instance in instances)
    by_player_behavior = Counter((instance["player"], instance["behavior"]) for instance in instances)
    all_groups = sorted(set(BEHAVIOR_AXIS.values()))
    return {
        "by_behavior": _sorted_counts(counter=by_behavior),
        "by_group": _sorted_counts(counter=by_group),
        "matrix": [
            {"player": player, "behavior": behavior, "group": _group_of(behavior=behavior), "count": count}
            for (player, behavior), count in sorted(by_player_behavior.items())
        ],
        "groups_observed": sorted(by_group),
        "groups_absent": [group for group in all_groups if group not in by_group],
    }


def _group_of(behavior: str) -> str:
    return BEHAVIOR_AXIS[behavior]


def _sorted_counts(counter: Counter[str]) -> dict[str, int]:
    # most frequent first, ties broken alphabetically, so the inventory reads sensibly and stably
    return {key: counter[key] for key in sorted(counter, key=lambda key: (-counter[key], key))}


# ---- divergences -----------------------------------------------------------------------------


def _divergences_section(members: list[dict[str, Any]], spans: tuple[TraceSpan, ...]) -> list[dict[str, Any]]:
    layers_by_player_turn = _layers_index(spans=spans)
    divergences = [
        {
            "evaluator_index": member["index"],
            "player": tag["player"],
            "behavior": tag["behavior"],
            "turn": tag["turn"],
            "cited_spans": [citation["span"] for citation in tag["evidence"]],
            "layers": _layers(index=layers_by_player_turn, player=tag["player"], turn=tag["turn"]),
        }
        for member in members
        for tag in member["tags"]
        if tag["behavior"] in WITHIN_TURN_GAP_BEHAVIORS
    ]
    divergences.sort(key=lambda row: (row["turn"], row["player"], row["behavior"], row["evaluator_index"]))
    return divergences


def _layers_index(spans: tuple[TraceSpan, ...]) -> dict[tuple[str, int], dict[str, list[str]]]:
    index: dict[tuple[str, int], dict[str, list[str]]] = {}
    for span in spans:
        if span.player is None or span.layer not in _DIVERGENCE_LAYERS:
            continue
        index.setdefault((span.player, span.turn), {}).setdefault(span.layer, []).append(span.text)
    return index


def _layers(index: dict[tuple[str, int], dict[str, list[str]]], player: str, turn: int) -> dict[str, str]:
    by_layer = index.get((player, turn), {})
    return {layer: "\n".join(by_layer.get(layer, [])) for layer in _DIVERGENCE_LAYERS}


# ---- player profiles -------------------------------------------------------------------------


def _player_profiles_section(members: list[dict[str, Any]], player_providers: dict[str, str]) -> dict[str, Any]:
    return {
        "by_player": [
            {
                "player": player,
                "provider": provider,
                "syntheses": [
                    {
                        "evaluator_index": member["index"],
                        "summary": synthesis["summary"],
                        "dimensions": [
                            {"dimension": dimension["dimension"], "characterization": dimension["characterization"]}
                            for dimension in synthesis["profile"]
                        ],
                        "key_episode_spans": [citation["span"] for citation in synthesis["key_episodes"]],
                    }
                    for member in members
                    for synthesis in member["syntheses"]
                    if synthesis["player"] == player
                ],
            }
            for player, provider in player_providers.items()
        ]
    }


# ---- re-verification against the trace index -------------------------------------------------


def _reverified_members(
    tags: list[dict[str, Any]],
    syntheses: list[dict[str, Any]],
    panel: list[str],
    span_by_id: dict[int, TraceSpan],
) -> list[dict[str, Any]]:
    # one member per panel seat (an index with no records yields an empty member), each carrying its
    # tags and syntheses with every citation re-verified against the trace index
    return [
        {
            "index": index,
            "spec": spec,
            "tags": [
                _reverified_tag(tag=tag, span_by_id=span_by_id) for tag in tags if tag.get("evaluator_index") == index
            ],
            "syntheses": [
                _reverified_synthesis(synthesis=synthesis, span_by_id=span_by_id)
                for synthesis in syntheses
                if synthesis.get("evaluator_index") == index
            ],
        }
        for index, spec in enumerate(panel)
    ]


def _reverified_tag(tag: dict[str, Any], span_by_id: dict[int, TraceSpan]) -> dict[str, Any]:
    return {
        "player": tag["player"],
        "behavior": tag["behavior"],
        "turn": tag["turn"],
        "confidence": tag.get("confidence", 1),
        "note": tag.get("note", ""),
        "evidence": [_reverified_evidence(evidence=evidence, span_by_id=span_by_id) for evidence in tag["evidence"]],
    }


def _reverified_synthesis(synthesis: dict[str, Any], span_by_id: dict[int, TraceSpan]) -> dict[str, Any]:
    return {
        "player": synthesis["player"],
        "summary": synthesis.get("summary", ""),
        "profile": [
            {
                "dimension": dimension["dimension"],
                "characterization": dimension.get("characterization", ""),
                "evidence": [
                    _reverified_evidence(evidence=evidence, span_by_id=span_by_id)
                    for evidence in dimension["evidence"]
                ],
            }
            for dimension in synthesis["profile"]
        ],
        "key_episodes": [
            _reverified_evidence(evidence=evidence, span_by_id=span_by_id) for evidence in synthesis["key_episodes"]
        ],
    }


def _reverified_evidence(evidence: dict[str, Any], span_by_id: dict[int, TraceSpan]) -> dict[str, Any]:
    span = evidence["span"]
    quote = evidence.get("quote")
    quote = quote if isinstance(quote, str) and quote else None
    resolved = span in span_by_id
    quote_matched = None if quote is None else (resolved and quote in span_by_id[span].text)
    return {
        "span": span,
        "quote": quote,
        "resolved": resolved,
        "quote_matched": quote_matched,
        "turn": span_by_id[span].turn if resolved else None,
    }


def _rate(part: int, whole: int) -> float:
    return round(part / whole, 2) if whole else 0.0


def _provider(spec: str) -> str:
    return spec.partition(":")[0]
