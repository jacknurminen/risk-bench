from pathlib import Path

from risk_bench.runner.annotation_store import (
    annotations_path_for_journal,
    delete_annotation,
    read_annotations,
    write_annotation,
)


def _annotation(annotation_id: str, note: str) -> dict:
    return {
        "type": "annotation",
        "id": annotation_id,
        "author": "you",
        "player": "alice",
        "behavior": "deception",
        "turn": 1,
        "confidence": 2,
        "note": note,
        "evidence": [{"span": 1, "quote": None}],
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }


def test_the_sidecar_sits_next_to_the_journal():
    path = annotations_path_for_journal(journal_path=Path("/games/g/game.jsonl"))
    assert path == Path("/games/g/game.annotations.jsonl")


def test_reading_a_missing_sidecar_yields_no_annotations(tmp_path: Path):
    assert read_annotations(path=tmp_path / "game.annotations.jsonl") == []


def test_writes_read_back_in_insertion_order(tmp_path: Path):
    path = tmp_path / "game.annotations.jsonl"
    write_annotation(path=path, annotation=_annotation(annotation_id="a", note="first"))
    write_annotation(path=path, annotation=_annotation(annotation_id="b", note="second"))

    assert [record["id"] for record in read_annotations(path=path)] == ["a", "b"]


def test_rewriting_an_id_replaces_it_in_place(tmp_path: Path):
    path = tmp_path / "game.annotations.jsonl"
    write_annotation(path=path, annotation=_annotation(annotation_id="a", note="first"))
    write_annotation(path=path, annotation=_annotation(annotation_id="b", note="second"))
    write_annotation(path=path, annotation=_annotation(annotation_id="a", note="edited"))

    records = read_annotations(path=path)
    assert [record["id"] for record in records] == ["a", "b"]  # an edit must not reorder the feed
    assert records[0]["note"] == "edited"


def test_delete_removes_by_id_and_reports_whether_it_existed(tmp_path: Path):
    path = tmp_path / "game.annotations.jsonl"
    write_annotation(path=path, annotation=_annotation(annotation_id="a", note="first"))
    write_annotation(path=path, annotation=_annotation(annotation_id="b", note="second"))

    assert delete_annotation(path=path, annotation_id="a") is True
    assert [record["id"] for record in read_annotations(path=path)] == ["b"]
    assert delete_annotation(path=path, annotation_id="missing") is False


def test_reading_tolerates_a_truncated_final_line(tmp_path: Path):
    path = tmp_path / "game.annotations.jsonl"
    write_annotation(path=path, annotation=_annotation(annotation_id="a", note="first"))
    with path.open("a", encoding="utf-8") as file:
        file.write('{"type": "annotation", "id": "b"')  # a half-written record from a crash

    assert [record["id"] for record in read_annotations(path=path)] == ["a"]
