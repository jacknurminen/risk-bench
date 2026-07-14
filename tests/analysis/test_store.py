import json
from pathlib import Path

from risk_bench.analysis.store import analysis_path_for_journal, write_analysis
from risk_bench.runner.evaluation_store import evaluation_path_for_journal
from tests.analysis.test_build import _write_evaluation, _write_journal


def test_analysis_path_is_derived_next_to_the_journal(tmp_path: Path):
    assert analysis_path_for_journal(journal_path=tmp_path / "game.jsonl") == tmp_path / "game.analysis.json"


def test_write_analysis_writes_valid_json_and_is_rerunnable(tmp_path: Path):
    journal_path = tmp_path / "game.jsonl"
    _write_journal(path=journal_path)
    _write_evaluation(journal_path=journal_path)
    eval_path = evaluation_path_for_journal(journal_path=journal_path)
    analysis_path = analysis_path_for_journal(journal_path=journal_path)

    first = write_analysis(journal_path=journal_path, eval_path=eval_path, analysis_path=analysis_path)
    assert first == tmp_path / "game.analysis.json"
    analysis = json.loads(first.read_text(encoding="utf-8"))
    assert analysis["schema_version"] == 2
    assert analysis["integrity"]["panel"]["total_citations"] == 13

    # a re-run overwrites cleanly (latest wins), leaving a single valid artifact
    second = write_analysis(journal_path=journal_path, eval_path=eval_path, analysis_path=analysis_path)
    assert second == first
    assert json.loads(second.read_text(encoding="utf-8"))["source"]["winner"] == "alice"
