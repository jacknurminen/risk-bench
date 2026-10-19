import json

from risk_bench.analysis.metrics import comparisons, member_summary
from risk_bench.runner import codebook_v4 as cb
from risk_bench.runner.evaluation_validation_v4 import validate_adjudication
from risk_bench.runner.evaluation_versions import version_for
from risk_bench.runner.journal import read_journal
from risk_bench.runner.trace_index import build_trace_index
from risk_bench.viewer.evaluation import build_evaluation_view_model
from risk_bench.viewer.research import tournament_research
from tests.runner.test_thesis_evaluation import thesis_ledger
from tests.v3_fixtures import PLAYERS, write_evaluation, write_journal


def thesis_view(tmp_path, flagged=False):
    journal = tmp_path / "game.jsonl"
    write_journal(journal)
    raw = thesis_ledger()
    if flagged:
        raw.deceptions[0].deliberate_intent = [cb.Citation(span=4, quote="I deceived Bob.")]
    units = validate_adjudication(raw, build_trace_index(read_journal(journal)), PLAYERS)
    path = write_evaluation(journal, units=units)
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    rows[0].update(schema_version=6, codebook_version=cb.CODEBOOK_VERSION, codebook_hash=cb.CODEBOOK_HASH)
    for row in rows:
        if row["type"] == "eval_message_coverage":
            row.update(scope="in_scope", missing_evidence=[])
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return journal, build_evaluation_view_model(journal, version_for(journal, None))


def test_v4_report_counts_findings_and_scope_without_historical_statement_tags(tmp_path):
    _, view = thesis_view(tmp_path)
    assert view["tag_schema"] == "commitments_deception_v4"
    member = view["members"][0]
    assert "statements" not in member
    summary = member_summary(member)
    assert summary["deception_count"] == 1
    assert "statement_statuses" not in summary
    assert summary["screening_scope"] == {"in_scope": 2}
    assert "deceptions" in comparisons(view["members"])
    report = view["research_report"]
    context = {"players": [{"name": name, "turns_survived": 1} for name in PLAYERS]}
    tournament = tournament_research([("game", context, report)])
    cohort = tournament["cohorts"][0]
    assert cohort["scope"] == "commitment_communication"
    alice = next(p for p in report["members"][0]["players"] if p["name"] == "alice")
    assert alice["deception_incidents"] == 1
    assert alice["deception_records"][0]["kind"] == "deception"


def test_review_flags_do_not_make_a_completed_evaluation_interrupted(tmp_path):
    journal, view = thesis_view(tmp_path, flagged=True)
    assert view["completion_status"] == "complete"
    assert view["members"][0]["status"] == "complete"
    assert view["review_flags"] == 1
    assert view["review_status"] == "needs_review"
    journal.with_suffix(".eval.error.json").write_text(json.dumps({"detail": "Provider stopped"}))
    interrupted = build_evaluation_view_model(journal, version_for(journal, None))
    assert interrupted["completion_status"] == "interrupted"
    assert interrupted["review_flags"] == 1


def test_unfinished_live_cutoff_is_separate_from_review_flags(tmp_path):
    journal, _ = thesis_view(tmp_path, flagged=True)
    path = journal.with_suffix(".eval.jsonl")
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    rows[0]["live"] = True
    rows.append({"type": "eval_live_cutoff", "round": 1, "turn": 1, "finalized": False})
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    view = build_evaluation_view_model(journal, version_for(journal, None))
    assert view["completion_status"] == "unfinished"
    assert view["review_flags"] == 1
