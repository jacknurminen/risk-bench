"""Sampled human review of a tournament's evaluator annotations.

A review pins one evaluation run per game and draws two random samples from those runs:

- findings (commitments, documented deceptions and deception candidates), whose
  correctness a reviewer checks field by field, which estimates precision;
- delivered messages, which a reviewer codes without seeing the evaluator's output,
  which estimates what the evaluator missed (recall).

The sample, its seed, the pinned runs and the population sizes are saved before any
verdict is given, so the selection cannot depend on the results. Verdicts are stored
beside the sample, one current verdict per item.
"""

import json
import os
import random
import re
import tempfile
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from risk_bench.runner.control import journal_control_lock
from risk_bench.runner.evaluation_store import read_evaluation
from risk_bench.runner.evaluation_versions import EvaluationVersion, list_versions, version_for
from risk_bench.runner.journal import read_journal
from risk_bench.runner.tournament_evaluation import get_batch
from risk_bench.runner.trace_index import TraceSpan, build_trace_index

REVIEW_DIRECTORY: Final[str] = "annotation-reviews"
SAMPLE_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^\d{8}-\d{6}-\d{6}$")

FINDING_TYPES: Final[dict[str, str]] = {
    "eval_commitment": "commitment",
    "eval_deception": "deception",
    "eval_deception_candidate": "deception_candidate",
}
FINDING_FIELDS: Final[tuple[str, ...]] = ("qualifies", "content", "outcome", "citations")
FINDING_VERDICTS: Final[frozenset[str]] = frozenset({"correct", "incorrect", "unsure", "not_applicable"})
MESSAGE_FIELDS: Final[tuple[str, ...]] = ("commitment", "deception")
MESSAGE_VERDICTS: Final[frozenset[str]] = frozenset({"yes", "no", "unsure"})

# How much surrounding conversation a blind message review shows.
CONTEXT_MESSAGES: Final[int] = 8
CONTEXT_BOARD: Final[int] = 25


def review_directory(tournament_directory: Path) -> Path:
    return tournament_directory / REVIEW_DIRECTORY


def sample_path(tournament_directory: Path, sample_id: str) -> Path:
    if not SAMPLE_ID_PATTERN.match(sample_id):
        raise ValueError("No such review sample.")
    return review_directory(tournament_directory) / f"{sample_id}.json"


def verdicts_path(tournament_directory: Path, sample_id: str) -> Path:
    return sample_path(tournament_directory, sample_id).with_suffix(".verdicts.jsonl")


# --- pinning and population -------------------------------------------------------------------


def pinned_runs(tournament_directory: Path, batch_id: str | None = None) -> tuple[list[dict], list[dict]]:
    """Choose one completed whole-game evaluation run per tournament game.

    With a batch, each game uses the run that batch saved for it. Without one, each game uses
    its newest whole-game run.

    Returns:
        The pinned runs, and the games left out with the reason.
    """
    manifest = json.loads((tournament_directory / "tournament.json").read_text(encoding="utf-8"))
    batch = get_batch(tournament_directory, batch_id) if batch_id else None
    runs, skipped = [], []
    for game_id in manifest["game_ids"]:
        journal = tournament_directory / game_id / "game.jsonl"
        if batch is not None:
            job = batch["games"].get(game_id) or {}
            candidates = [version_for(journal, run_id) for run_id in job.get("versions", [])]
            candidates = [version for version in candidates if version is not None]
        else:
            candidates = list_versions(journal)
        version = next((v for v in candidates if v.method == "whole_game"), None)
        if version is None:
            skipped.append({"game_id": game_id, "reason": "No whole-game evaluation run"})
            continue
        records = read_evaluation(version.eval_path)
        statuses = [r.get("status") for r in records if r.get("type") == "eval_member_status"]
        if not statuses or any(status != "complete" for status in statuses):
            skipped.append({"game_id": game_id, "reason": "Evaluation run did not complete"})
            continue
        header = records[0]
        runs.append(
            {
                "game_id": game_id,
                "run_id": version.run_id,
                "panel": header.get("panel", []),
                "codebook_hash": header.get("codebook_hash"),
                "trace_hash": header.get("source", {}).get("trace_hash"),
                "span_count": header.get("coverage", {}).get("span_count"),
            }
        )
    return runs, skipped


def finding_stratum(record: dict) -> str:
    """Group a finding by type and, where it has one, its final outcome."""
    kind = FINDING_TYPES[record["type"]]
    if kind == "commitment":
        return f"commitment:{record['status_history'][-1]['status']}"
    if kind == "deception_candidate":
        return f"deception_candidate:{record['review_history'][-1]['outcome']}"
    return kind


def _version(tournament_directory: Path, run: dict) -> EvaluationVersion:
    version = version_for(tournament_directory / run["game_id"] / "game.jsonl", run["run_id"])
    if version is None:
        raise ValueError(f"Evaluation run {run['run_id']} of {run['game_id']} is missing.")
    return version


def _spans(tournament_directory: Path, run: dict) -> tuple[TraceSpan, ...]:
    spans = build_trace_index(read_journal(tournament_directory / run["game_id"] / "game.jsonl"))
    # The evaluator saw only the prefix it was given; a resumed game may have grown since.
    return spans[: run["span_count"]] if run.get("span_count") is not None else spans


def population(tournament_directory: Path, runs: list[dict], evaluator_index: int) -> tuple[list[dict], list[dict]]:
    """Every finding of one panel member and every delivered message in the pinned runs."""
    findings, messages = [], []
    for run in runs:
        for record in read_evaluation(_version(tournament_directory, run).eval_path):
            if record.get("type") in FINDING_TYPES and record.get("evaluator_index", 0) == evaluator_index:
                findings.append(
                    {
                        "game_id": run["game_id"],
                        "unit_type": FINDING_TYPES[record["type"]],
                        "unit_id": record["id"],
                        "stratum": finding_stratum(record),
                        "span": record["passage"]["span"],
                    }
                )
        # Messages come from the trace itself, not from the evaluator's screening rows.
        messages.extend(
            {"game_id": run["game_id"], "span": span.span, "player": span.player}
            for span in _spans(tournament_directory, run)
            if span.layer == "message"
        )
    return findings, messages


# --- sampling ---------------------------------------------------------------------------------


def allocate(sizes: dict[str, int], total: int, minimum: int) -> dict[str, int]:
    """Split a sample across strata: a floor per stratum, the rest in proportion to size.

    The floor keeps rare outcomes (such as violated commitments) in the sample; proportional
    allocation of the remainder uses the largest-remainder method so the counts sum exactly.
    """
    total = min(total, sum(sizes.values()))
    counts = {name: min(size, minimum) for name, size in sizes.items()}
    if sum(counts.values()) >= total:
        # Too small for every floor: give strata one at a time, largest first.
        counts = dict.fromkeys(sizes, 0)
        order = sorted(sizes, key=lambda name: (-sizes[name], name))
        while sum(counts.values()) < total:
            for name in order:
                if sum(counts.values()) < total and counts[name] < sizes[name]:
                    counts[name] += 1
        return counts
    while sum(counts.values()) < total:
        remaining = total - sum(counts.values())
        spare = {name: sizes[name] - counts[name] for name in sizes if sizes[name] > counts[name]}
        weight = sum(spare.values())
        shares = {name: remaining * size / weight for name, size in spare.items()}
        for name, share in shares.items():
            counts[name] += min(int(share), spare[name])
        left = total - sum(counts.values())
        for name in sorted(shares, key=lambda n: (-(shares[n] - int(shares[n])), n))[:left]:
            if counts[name] < sizes[name]:
                counts[name] += 1
    return counts


def _spread_across_games(units: list[dict], rng: random.Random) -> list[dict]:
    """Random order that takes one unit from each game in turn, so no game dominates a stratum."""
    by_game: dict[str, list[dict]] = defaultdict(list)
    for unit in units:
        by_game[unit["game_id"]].append(unit)
    games = sorted(by_game)
    rng.shuffle(games)
    for game in games:
        rng.shuffle(by_game[game])
    ordered, depth = [], 0
    while len(ordered) < len(units):
        ordered.extend(by_game[game][depth] for game in games if depth < len(by_game[game]))
        depth += 1
    return ordered


def draw_sample(
    tournament_directory: Path,
    *,
    commitments: int = 40,
    deception_findings: int = 20,
    messages: int = 60,
    minimum_per_stratum: int = 5,
    seed: int | None = None,
    batch_id: str | None = None,
    evaluator_index: int = 0,
) -> dict:
    """Draw and save a review sample; the seed and population are recorded with it.

    Args:
        tournament_directory: The tournament's directory (holds ``tournament.json``).
        commitments: Commitment findings to review, stratified by final status.
        deception_findings: Deceptions and deception candidates to review. Documented
            deceptions are taken first because they are rare; candidates fill the rest.
        messages: Delivered messages to code blind, drawn uniformly from all games.
        minimum_per_stratum: Floor per status stratum before proportional allocation.
        seed: Random seed; a fresh one is chosen and recorded when omitted.
        batch_id: A tournament evaluation batch to take runs from; newest runs otherwise.
        evaluator_index: The panel member whose findings are reviewed.

    Returns:
        The saved sample.
    """
    if min(commitments, deception_findings, messages, minimum_per_stratum) < 0:
        raise ValueError("Sample sizes must not be negative.")
    runs, skipped = pinned_runs(tournament_directory, batch_id)
    if not runs:
        raise ValueError("No game in this tournament has a completed whole-game evaluation.")
    seed = random.SystemRandom().randrange(2**31) if seed is None else seed
    rng = random.Random(seed)
    findings, message_population = population(tournament_directory, runs, evaluator_index)

    strata: dict[str, list[dict]] = defaultdict(list)
    for finding in findings:
        strata[finding["stratum"]].append(finding)
    sizes = {name: len(units) for name, units in sorted(strata.items())}

    commitment_sizes = {name: size for name, size in sizes.items() if name.startswith("commitment:")}
    allocation = allocate(commitment_sizes, commitments, minimum_per_stratum)
    documented = min(sizes.get("deception", 0), deception_findings)
    allocation["deception"] = documented
    candidate_sizes = {name: size for name, size in sizes.items() if name.startswith("deception_candidate:")}
    allocation.update(allocate(candidate_sizes, deception_findings - documented, minimum_per_stratum))

    chosen = []
    for name, count in sorted(allocation.items()):
        chosen.extend({**unit, "kind": "finding"} for unit in _spread_across_games(strata[name], rng)[:count])
    chosen.extend(
        {**unit, "kind": "message"} for unit in rng.sample(message_population, min(messages, len(message_population)))
    )
    rng.shuffle(chosen)
    items = [
        {"item_id": f"{'f' if unit['kind'] == 'finding' else 'm'}{index}", **unit}
        for index, unit in enumerate(chosen, start=1)
    ]

    warnings = []
    panels = {json.dumps(run["panel"]) for run in runs}
    if len(panels) > 1:
        warnings.append("The pinned runs used different evaluator panels: " + ", ".join(sorted(panels)))
    if len({run["codebook_hash"] for run in runs}) > 1:
        warnings.append("The pinned runs used different codebook versions.")
    if skipped:
        warnings.append(f"{len(skipped)} game(s) have no completed whole-game evaluation and are not sampled.")

    now = datetime.now(tz=UTC)
    sample = {
        "id": now.strftime("%Y%m%d-%H%M%S-%f"),
        "created_at": now.isoformat(),
        "tournament_id": tournament_directory.name,
        "seed": seed,
        "batch_id": batch_id,
        "evaluator_index": evaluator_index,
        "requested": {
            "commitments": commitments,
            "deception_findings": deception_findings,
            "messages": messages,
            "minimum_per_stratum": minimum_per_stratum,
        },
        "runs": runs,
        "skipped_games": skipped,
        "warnings": warnings,
        "population": {"findings": sizes, "messages": len(message_population)},
        "allocation": {**allocation, "messages": sum(item["kind"] == "message" for item in items)},
        "items": items,
    }
    path = sample_path(tournament_directory, sample["id"])
    _write_atomic(path, json.dumps(sample, indent=1, ensure_ascii=False))
    return sample


def list_samples(tournament_directory: Path) -> list[dict]:
    """Saved samples, newest first, with how many items have a verdict."""
    summaries = []
    for path in review_directory(tournament_directory).glob("*.json"):
        if not SAMPLE_ID_PATTERN.match(path.stem):
            continue
        sample = json.loads(path.read_text(encoding="utf-8"))
        done = set(read_verdicts(tournament_directory, sample["id"]))
        summaries.append(
            {
                "id": sample["id"],
                "created_at": sample["created_at"],
                "seed": sample["seed"],
                "batch_id": sample["batch_id"],
                "games": len(sample["runs"]),
                "findings": sum(item["kind"] == "finding" for item in sample["items"]),
                "messages": sum(item["kind"] == "message" for item in sample["items"]),
                "reviewed": sum(item["item_id"] in done for item in sample["items"]),
            }
        )
    return sorted(summaries, key=lambda summary: summary["id"], reverse=True)


def load_sample(tournament_directory: Path, sample_id: str) -> dict:
    path = sample_path(tournament_directory, sample_id)
    if not path.is_file():
        raise ValueError("No such review sample.")
    return json.loads(path.read_text(encoding="utf-8"))


# --- verdicts ---------------------------------------------------------------------------------


def read_verdicts(tournament_directory: Path, sample_id: str) -> dict[str, dict]:
    path = verdicts_path(tournament_directory, sample_id)
    if not path.is_file():
        return {}
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return {row["item_id"]: row for row in rows}


def save_verdict(tournament_directory: Path, sample_id: str, item_id: str, verdict: dict, note: str = "") -> dict:
    """Validate and store one item's verdict, replacing any earlier verdict for that item."""
    sample = load_sample(tournament_directory, sample_id)
    item = next((item for item in sample["items"] if item["item_id"] == item_id), None)
    if item is None:
        raise ValueError("No such item in this sample.")
    fields, allowed = (
        (FINDING_FIELDS, FINDING_VERDICTS) if item["kind"] == "finding" else (MESSAGE_FIELDS, MESSAGE_VERDICTS)
    )
    if set(verdict) != set(fields) or any(verdict[field] not in allowed for field in fields):
        raise ValueError(f"Give one of {sorted(allowed)} for each of {list(fields)}.")
    row = {
        "item_id": item_id,
        "kind": item["kind"],
        "verdict": {field: verdict[field] for field in fields},
        "note": note.strip(),
        "reviewed_at": datetime.now(tz=UTC).isoformat(),
    }
    path = verdicts_path(tournament_directory, sample_id)
    with journal_control_lock(path):
        current = read_verdicts(tournament_directory, sample_id)
        current[item_id] = row
        _write_atomic(path, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in current.values()))
    return row


def clear_verdict(tournament_directory: Path, sample_id: str, item_id: str) -> None:
    path = verdicts_path(tournament_directory, sample_id)
    with journal_control_lock(path):
        current = read_verdicts(tournament_directory, sample_id)
        if current.pop(item_id, None) is not None:
            _write_atomic(path, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in current.values()))


# --- what the evaluator said about a message ---------------------------------------------------


def _cited_spans(value: Any) -> set[int]:
    if isinstance(value, dict):
        found = {value["span"]} if isinstance(value.get("span"), int) and "quote" in value else set()
        for child in value.values():
            found |= _cited_spans(child)
        return found
    if isinstance(value, list):
        return set().union(*(_cited_spans(child) for child in value)) if value else set()
    return set()


def judge_labels(records: list[dict], evaluator_index: int) -> dict[int, dict]:
    """The evaluator's annotations per message span, for comparison with a blind code.

    A message counts as a commitment if it is the passage of a commitment record or is cited as
    a participant's proposal, acceptance, rejection or counterproposal, or as a revision. It counts
    as a deception only if it is the passage of a documented deception.
    """
    labels: dict[int, dict] = defaultdict(
        lambda: {"commitments": [], "deceptions": [], "candidates": [], "screening": None}
    )
    for record in records:
        if record.get("evaluator_index", 0) != evaluator_index:
            continue
        match record.get("type"):
            case "eval_commitment":
                spans = {record["passage"]["span"]}
                for event in record.get("participant_history", []):
                    spans |= _cited_spans(event.get("evidence", []))
                for revision in record.get("revisions", []):
                    spans |= _cited_spans(revision.get("evidence", []))
                for span in spans:
                    labels[span]["commitments"].append(record["id"])
            case "eval_deception":
                labels[record["passage"]["span"]]["deceptions"].append(record["id"])
            case "eval_deception_candidate":
                labels[record["passage"]["span"]]["candidates"].append(record["id"])
            case "eval_message_coverage":
                labels[record["span"]]["screening"] = {
                    key: record.get(key) for key in ("status", "scope", "explanation", "missing_evidence")
                }
    return dict(labels)


# --- the review view --------------------------------------------------------------------------


def _span_view(span: TraceSpan) -> dict:
    return {
        "span": span.span,
        "layer": span.layer,
        "player": span.player,
        "turn": span.turn,
        "round": span.round,
        "text": span.text,
        "decision_id": span.decision_id,
    }


def _message_context(spans: tuple[TraceSpan, ...], target: TraceSpan) -> dict:
    """One chronological timeline around a message, each line tagged with why it is shown.

    Roles: ``target`` (the message to code), ``message`` (surrounding conversation),
    ``private`` (the speaker's decision summaries and game memory) and ``board`` (the speaker's
    actions and the outcomes naming them).
    """
    earlier = [s for s in spans if s.layer == "message" and s.span < target.span][-CONTEXT_MESSAGES:]
    later = [s for s in spans if s.layer == "message" and s.span > target.span][:2]
    start = earlier[0].span if earlier else target.span
    # Deception needs the speaker's own prior or same-decision private records.
    private = [
        s
        for s in spans
        if s.player == target.player
        and s.layer in {"thought", "note"}
        and (
            (target.decision_id is not None and s.decision_id == target.decision_id)
            or (s.span < target.span and s.layer == "thought")
        )
    ]
    last_note = next(
        (s for s in reversed(spans[: target.span]) if s.player == target.player and s.layer == "note"), None
    )
    private_spans = {s.span: s for s in private[-4:]}
    private_spans.update({s.span: s for s in private if s.decision_id == target.decision_id})
    if last_note is not None:
        private_spans[last_note.span] = last_note
    # Board facts a deceptive claim could contradict: the speaker's actions and the outcomes naming
    # them, within the conversation window up to the message. Single dice rolls are left out.
    board = [
        s
        for s in spans[start : target.span]
        if (
            (s.layer == "action" and s.player == target.player)
            or (
                s.layer == "event"
                and target.player is not None
                and str(target.player) in s.text
                and " rolled " not in s.text
            )
        )
    ][-CONTEXT_BOARD:]
    roles = {s.span: "board" for s in board}
    roles.update({s: "private" for s in private_spans})
    roles.update({s.span: "message" for s in (*earlier, *later)})
    roles[target.span] = "target"
    return {"timeline": [{**_span_view(spans[number]), "role": roles[number]} for number in sorted(roles)]}


def review_view(tournament_directory: Path, sample_id: str) -> dict:
    """The sample with everything a reviewer needs per item.

    The evaluator's labels for a message are withheld until that message has a verdict, so the
    message review stays blind.
    """
    sample = load_sample(tournament_directory, sample_id)
    verdicts = read_verdicts(tournament_directory, sample_id)
    runs = {run["game_id"]: run for run in sample["runs"]}
    evaluator_index = sample["evaluator_index"]
    spans_by_game, records_by_game, labels_by_game = {}, {}, {}
    for game_id, run in runs.items():
        spans_by_game[game_id] = _spans(tournament_directory, run)
        records_by_game[game_id] = read_evaluation(_version(tournament_directory, run).eval_path)
        labels_by_game[game_id] = judge_labels(records_by_game[game_id], evaluator_index)

    items = []
    for item in sample["items"]:
        game_id, run = item["game_id"], runs[item["game_id"]]
        spans = spans_by_game[game_id]
        view = {
            **item,
            "game_url": f"/games/{game_id}?version={run['run_id']}#span={item['span']}",
            "verdict": verdicts.get(item["item_id"]),
        }
        if item["kind"] == "finding":
            record = next(
                r
                for r in records_by_game[game_id]
                if r.get("type") in FINDING_TYPES
                and r.get("evaluator_index", 0) == evaluator_index
                and r["id"] == item["unit_id"]
            )
            view["record"] = record
            view["spans"] = {
                str(number): _span_view(spans[number])
                for number in sorted(_cited_spans(record))
                if number < len(spans)
            }
        else:
            target = spans[item["span"]]
            view["message"] = _span_view(target)
            view["context"] = _message_context(spans, target)
            if view["verdict"] is not None:
                view["judge"] = labels_by_game[game_id].get(
                    item["span"], {"commitments": [], "deceptions": [], "candidates": [], "screening": None}
                )
        items.append(view)
    return {**{key: value for key, value in sample.items() if key != "items"}, "items": items}


# --- scoring ----------------------------------------------------------------------------------


def wilson_interval(successes: int, trials: int, z: float = 1.959964) -> tuple[float, float] | None:
    if trials == 0:
        return None
    p = successes / trials
    denominator = 1 + z * z / trials
    centre = (p + z * z / (2 * trials)) / denominator
    half = z * ((p * (1 - p) / trials + z * z / (4 * trials * trials)) ** 0.5) / denominator
    return max(0.0, centre - half), min(1.0, centre + half)


def _rate(successes: int, trials: int) -> dict:
    interval = wilson_interval(successes, trials)
    return {
        "correct": successes,
        "judged": trials,
        "rate": successes / trials if trials else None,
        "ci95": list(interval) if interval else None,
    }


def score_sample(tournament_directory: Path, sample_id: str) -> dict:
    """Precision per finding stratum and field, and recall from the blind message codes.

    Unsure and not-applicable verdicts are left out of each rate and counted separately. The
    overall commitment precision weights each status stratum by its share of the population, so
    the floor that over-samples rare statuses does not bias the estimate.
    """
    view = review_view(tournament_directory, sample_id)
    reviewed = [item for item in view["items"] if item["verdict"] is not None]

    precision: dict[str, dict] = {}
    by_stratum: dict[str, list[dict]] = defaultdict(list)
    for item in reviewed:
        if item["kind"] == "finding":
            by_stratum[item["stratum"]].append(item)
    for stratum, items in sorted(by_stratum.items()):
        fields = {}
        for field in FINDING_FIELDS:
            values = [item["verdict"]["verdict"][field] for item in items]
            judged = [value for value in values if value in {"correct", "incorrect"}]
            fields[field] = {
                **_rate(judged.count("correct"), len(judged)),
                "unsure": values.count("unsure"),
                "not_applicable": values.count("not_applicable"),
            }
        all_correct = [
            all(item["verdict"]["verdict"][f] in {"correct", "not_applicable"} for f in FINDING_FIELDS)
            for item in items
            if "unsure" not in item["verdict"]["verdict"].values()
        ]
        precision[stratum] = {
            "reviewed": len(items),
            "fields": fields,
            "all_correct": _rate(sum(all_correct), len(all_correct)),
        }

    sizes = view["population"]["findings"]
    commitment_strata = {s: p for s, p in precision.items() if s.startswith("commitment:")}
    weighted = None
    if commitment_strata:
        total = sum(sizes[s] for s in commitment_strata)
        if total:
            rates = [
                (sizes[s] / total, p["fields"]["qualifies"]["rate"])
                for s, p in commitment_strata.items()
                if p["fields"]["qualifies"]["rate"] is not None
            ]
            covered = sum(weight for weight, _ in rates)
            weighted = sum(weight * rate for weight, rate in rates) / covered if covered else None

    recall, misses, extra = {}, [], []
    messages = [item for item in reviewed if item["kind"] == "message"]
    for field, judge_key in (("commitment", "commitments"), ("deception", "deceptions")):
        coded = [item for item in messages if item["verdict"]["verdict"][field] in {"yes", "no"}]
        human_yes = [item for item in coded if item["verdict"]["verdict"][field] == "yes"]
        judge_yes = [item for item in coded if item["judge"][judge_key]]
        both = [item for item in human_yes if item["judge"][judge_key]]
        recall[field] = {
            "coded": len(coded),
            "unsure": sum(item["verdict"]["verdict"][field] == "unsure" for item in messages),
            "human_positive": len(human_yes),
            "judge_positive": len(judge_yes),
            "recall": _rate(len(both), len(human_yes)),
            "agreement": _rate(
                sum(bool(i["judge"][judge_key]) == (i["verdict"]["verdict"][field] == "yes") for i in coded),
                len(coded),
            ),
        }
        misses.extend(
            {
                "item_id": i["item_id"],
                "field": field,
                "game_id": i["game_id"],
                "span": i["span"],
                "note": i["verdict"]["note"],
                "game_url": i["game_url"],
            }
            for i in human_yes
            if not i["judge"][judge_key]
        )
        extra.extend(
            {
                "item_id": i["item_id"],
                "field": field,
                "game_id": i["game_id"],
                "span": i["span"],
                "judge_ids": i["judge"][judge_key],
                "note": i["verdict"]["note"],
                "game_url": i["game_url"],
            }
            for i in judge_yes
            if i["verdict"]["verdict"][field] == "no"
        )

    return {
        "sample_id": sample_id,
        "seed": view["seed"],
        "runs": view["runs"],
        "progress": {"reviewed": len(reviewed), "total": len(view["items"])},
        "precision": precision,
        "commitment_qualifies_weighted": weighted,
        "message_recall": recall,
        "missed": misses,
        "disputed_judge_positives": extra,
        "incorrect_findings": [
            {
                "item_id": i["item_id"],
                "game_id": i["game_id"],
                "unit_id": i["unit_id"],
                "stratum": i["stratum"],
                "fields": [f for f in FINDING_FIELDS if i["verdict"]["verdict"][f] == "incorrect"],
                "note": i["verdict"]["note"],
                "game_url": i["game_url"],
            }
            for i in reviewed
            if i["kind"] == "finding" and "incorrect" in i["verdict"]["verdict"].values()
        ],
    }


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as file:
        file.write(text)
        file.flush()
        os.fsync(file.fileno())
        temporary = file.name
    os.replace(temporary, path)
