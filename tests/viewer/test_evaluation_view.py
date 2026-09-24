import json

from risk_bench.runner.evaluation_versions import version_for
from risk_bench.viewer.evaluation import build_evaluation_view_model
from tests.v3_fixtures import write_evaluation, write_journal


def test_quotes_resolve_to_original_record_and_decision(tmp_path):
    journal = tmp_path / "game.jsonl"
    write_journal(journal)
    write_evaluation(journal)
    view = build_evaluation_view_model(journal, version_for(journal, None))
    cite = view["members"][0]["claims"][0]["comparison"][0]
    assert cite["resolved"] and cite["quote_matched"]
    assert cite["record"] == 1 and cite["decision_id"] == "d1"
    assert cite["source"]["game_id"] == tmp_path.name


def test_append_preserves_cutoff_but_changed_prefix_invalidates_units(tmp_path):
    journal = tmp_path / "game.jsonl"
    write_journal(journal)
    write_evaluation(journal)
    version = version_for(journal, None)
    with journal.open("a") as out:
        out.write(json.dumps({"type": "note", "turn": 2, "player": "bob", "text": "More"}) + "\n")
    view = build_evaluation_view_model(journal, version)
    assert view["coverage"]["partial"] and view["coverage"]["source_matches"]
    journal.write_text(journal.read_text().replace("lie to Bob", "tell Bob"))
    view = build_evaluation_view_model(journal, version)
    assert not view["coverage"]["source_matches"]
    assert not view["members"][0]["episodes"][0]["valid"]


def test_copied_evaluation_rejected_by_game_identity(tmp_path):
    journal = tmp_path / "original" / "game.jsonl"
    write_journal(journal)
    sidecar = write_evaluation(journal)
    copy = tmp_path / "copy" / "game.jsonl"
    write_journal(copy)
    copy.with_suffix(".eval.jsonl").write_text(sidecar.read_text())
    view = build_evaluation_view_model(copy, version_for(copy, None))
    assert not view["coverage"]["source_matches"]


def test_changed_game_settings_invalidate_evaluation_even_with_same_trace_text(tmp_path):
    journal = tmp_path / "game.jsonl"
    write_journal(journal)
    write_evaluation(journal)
    journal.write_text(journal.read_text().replace('"seed": 1', '"seed": 2'))
    view = build_evaluation_view_model(journal, version_for(journal, None))
    assert not view["coverage"]["source_matches"]
