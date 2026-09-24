import json
from pathlib import Path

import pytest

from risk_bench.runner.evaluation_versions import archive_current, list_versions, version_for


def _write_eval(path: Path, run_id: str | None, panel: list[str]) -> None:
    # a raw sidecar so the test controls the run id deterministically (the writer stamps its own)
    header = {
        "type": "eval_header",
        "schema_version": 3,
        "panel": panel,
        "coverage": {"record_count": 1, "span_count": 1},
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    if run_id is not None:
        header["run_id"] = run_id
    tag = {
        "type": "eval_tag",
        "evaluator_index": 0,
        "evaluator": panel[0],
        "player": "alice",
        "behavior": "deception",
        "turn": 1,
        "evidence": [],
        "confidence": 2,
        "note": "",
    }
    path.write_text(json.dumps(header) + "\n" + json.dumps(tag) + "\n", encoding="utf-8")


def test_a_single_run_is_the_current_version(tmp_path: Path):
    journal_path = tmp_path / "game.jsonl"
    _write_eval(path=tmp_path / "game.eval.jsonl", run_id="20260101-000000", panel=["anthropic:claude-opus-5"])

    (version,) = list_versions(journal_path=journal_path)
    assert version.is_current and version.run_id == "20260101-000000" and version.panel == ["anthropic:claude-opus-5"]
    assert version_for(journal_path=journal_path, run_id=None) == version


def test_run_id_is_derived_from_created_at_when_absent(tmp_path: Path):
    journal_path = tmp_path / "game.jsonl"
    _write_eval(
        path=tmp_path / "game.eval.jsonl", run_id=None, panel=["anthropic:claude-opus-5"]
    )  # pre-versioning sidecar

    with pytest.raises(KeyError):
        list_versions(journal_path=journal_path)


def test_archiving_frees_the_flat_paths_and_a_re_run_adds_a_second_version(tmp_path: Path):
    journal_path = tmp_path / "game.jsonl"
    _write_eval(path=tmp_path / "game.eval.jsonl", run_id="20260101-000000", panel=["anthropic:claude-opus-5"])
    # the first run's companion sidecars, which must travel with it when archived
    (tmp_path / "game.verifications.jsonl").write_text("{}\n", encoding="utf-8")
    (tmp_path / "game.analysis.json").write_text("{}\n", encoding="utf-8")
    # a stray current eval-trace must never be mistaken for an archived run
    (tmp_path / "game.eval.trace.jsonl").write_text("{}\n", encoding="utf-8")

    archived_run_id = archive_current(journal_path=journal_path)

    assert archived_run_id == "20260101-000000"
    assert not (tmp_path / "game.eval.jsonl").exists()  # the flat path is now free for the re-run
    assert (tmp_path / "game.eval.20260101-000000.jsonl").is_file()
    assert (tmp_path / "game.verifications.20260101-000000.jsonl").is_file()  # verifications archived per version
    assert (tmp_path / "game.analysis.20260101-000000.json").is_file()

    _write_eval(path=tmp_path / "game.eval.jsonl", run_id="20260102-000000", panel=["openai:gpt-5.6-sol"])
    versions = list_versions(journal_path=journal_path)

    assert [version.run_id for version in versions] == ["20260102-000000", "20260101-000000"]  # newest first
    assert versions[0].is_current and not versions[1].is_current
    assert versions[0].panel == ["openai:gpt-5.6-sol"] and versions[1].panel == ["anthropic:claude-opus-5"]
    pinned = version_for(journal_path=journal_path, run_id="20260101-000000")
    assert pinned is not None and pinned.panel == ["anthropic:claude-opus-5"]
    assert version_for(journal_path=journal_path, run_id="nope") is None


def test_archiving_with_no_evaluation_is_a_no_op(tmp_path: Path):
    assert archive_current(journal_path=tmp_path / "game.jsonl") is None
    assert list_versions(journal_path=tmp_path / "game.jsonl") == []


def test_archived_microsecond_collisions_remain_addressable(tmp_path):
    journal = tmp_path / "game.jsonl"
    run_id = "20260101-000000-123456"
    for _ in range(2):
        _write_eval(tmp_path / "game.eval.jsonl", run_id, ["test:coder"])
        archive_current(journal)
    versions = list_versions(journal)
    assert {v.run_id for v in versions} == {run_id, run_id + "-2"}
    assert version_for(journal, run_id + "-2") is not None
