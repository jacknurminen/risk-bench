from risk_bench.analysis.metrics import comparisons, member_summary
from risk_bench.runner.evaluation_versions import version_for
from risk_bench.viewer.evaluation import build_evaluation_view_model
from tests.v3_fixtures import write_evaluation, write_journal


def member(tmp_path):
    path = tmp_path / "game.jsonl"
    write_journal(path)
    write_evaluation(path)
    return build_evaluation_view_model(path, version_for(path, None))["members"][0]


def test_unresolved_and_human_episodes_never_enter_adherence(tmp_path):
    m = member(tmp_path)
    m["episodes"][0]["status"] = "open"
    assert member_summary(m)["adherence"]["proportion"] is None
    m["episodes"][0].update(status="fulfilled", action_provenance="human")
    assert member_summary(m)["adherence"]["resolved_agent_episodes"] == 0


def test_invalid_units_not_counted_but_integrity_is_reported(tmp_path):
    m = member(tmp_path)
    m["claims"][0]["valid"] = False
    m["claims"][0]["comparison"][0]["quote_matched"] = False
    result = member_summary(m)
    assert result["claim_count"] == 0 and result["invalid_units"] == 1
    assert result["integrity"]["quote_mismatches"] == 1


def test_ambiguous_split_is_exposed_not_forced_into_agreement(tmp_path):
    m = member(tmp_path)
    m["episodes"].append({**m["episodes"][0], "id": "split"})
    assert comparisons([m])["episodes"][0]["ambiguous_alignment"]
