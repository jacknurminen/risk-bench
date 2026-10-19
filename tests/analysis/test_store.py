import json

from risk_bench.analysis.store import analysis_path_for_journal, write_analysis
from tests.v3_fixtures import write_evaluation, write_journal


def test_write_analysis_round_trips_and_is_rerunnable(tmp_path):
    journal = tmp_path / "game.jsonl"
    write_journal(journal)
    evaluation = write_evaluation(journal)
    path = analysis_path_for_journal(journal)
    for _ in range(2):
        assert write_analysis(journal, evaluation, path) == path
        saved = json.loads(path.read_text())
        assert saved["schema_version"] == 3
        assert saved["members"][0]["episode_count"] == 1
