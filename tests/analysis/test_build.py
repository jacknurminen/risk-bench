from risk_bench.analysis.build import build_analysis
from tests.v3_fixtures import write_evaluation as _write_evaluation
from tests.v3_fixtures import write_journal as _write_journal


def test_analysis_keeps_coders_separate_and_exposes_denominators(tmp_path):
    path = tmp_path / "game.jsonl"
    _write_journal(path)
    sidecar = _write_evaluation(path, panel=["test:a", "test:b"])
    result = build_analysis(path, sidecar, "now")
    assert result["schema_version"] == 3
    assert len(result["members"]) == 2
    assert [m["episode_count"] for m in result["members"]] == [1, 1]
    assert result["members"][0]["adherence"] == {"fulfilled": 0, "resolved_agent_episodes": 1, "proportion": 0.0}
    assert len(result["comparison"]["episodes"]) == 1
    assert result["source"]["source_matches"]
