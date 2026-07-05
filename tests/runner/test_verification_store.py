from pathlib import Path

from risk_bench.runner.verification_store import (
    delete_verification,
    read_verifications,
    verifications_path_for_journal,
    write_verification,
)


def verification(behavior: str, verified_at: str) -> dict:
    return {
        "type": "verification",
        "evaluator_index": 0,
        "player": "alice",
        "behavior": behavior,
        "turn": 1,
        "verified_at": verified_at,
    }


def test_the_sidecar_path_sits_next_to_the_journal():
    path = verifications_path_for_journal(journal_path=Path("/games/g1/game.jsonl"))

    assert path == Path("/games/g1/game.verifications.jsonl")


def test_a_missing_sidecar_reads_as_no_verifications(tmp_path: Path):
    assert read_verifications(path=tmp_path / "game.verifications.jsonl") == []


def test_verifications_accumulate_one_per_tag_key(tmp_path: Path):
    path = tmp_path / "game.verifications.jsonl"

    write_verification(path=path, verification=verification(behavior="deception", verified_at="t1"))
    write_verification(path=path, verification=verification(behavior="betrayal", verified_at="t1"))

    assert [record["behavior"] for record in read_verifications(path=path)] == ["deception", "betrayal"]


def test_reverifying_the_same_tag_replaces_the_record_instead_of_duplicating(tmp_path: Path):
    path = tmp_path / "game.verifications.jsonl"
    write_verification(path=path, verification=verification(behavior="deception", verified_at="t1"))

    write_verification(path=path, verification=verification(behavior="deception", verified_at="t2"))

    (record,) = read_verifications(path=path)
    assert record["verified_at"] == "t2"


def test_deleting_removes_only_the_matching_tag_key(tmp_path: Path):
    path = tmp_path / "game.verifications.jsonl"
    write_verification(path=path, verification=verification(behavior="deception", verified_at="t1"))
    write_verification(path=path, verification=verification(behavior="betrayal", verified_at="t1"))

    removed = delete_verification(path=path, evaluator_index=0, player="alice", behavior="deception", turn=1)

    assert removed is True
    assert [record["behavior"] for record in read_verifications(path=path)] == ["betrayal"]
    # deleting again reports that nothing matched
    assert delete_verification(path=path, evaluator_index=0, player="alice", behavior="deception", turn=1) is False


def test_a_truncated_final_line_is_tolerated(tmp_path: Path):
    path = tmp_path / "game.verifications.jsonl"
    write_verification(path=path, verification=verification(behavior="deception", verified_at="t1"))
    with path.open("a", encoding="utf-8") as file:
        file.write('{"type": "verification", "evaluator_index": 1, "pla')  # a crash mid-write

    assert [record["behavior"] for record in read_verifications(path=path)] == ["deception"]
