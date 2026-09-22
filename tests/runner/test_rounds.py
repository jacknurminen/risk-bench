"""Round boundaries survive old checkpoints, historical forks and note-taking pauses."""

import copy
import json

import pytest

from risk_bench.runner import checkpoint
from risk_bench.runner.forking import apply_additional_context, fork_game
from risk_bench.runner.journal import (
    JournalHeader,
    JournalWriter,
    expand_records,
    prefix_hash,
    raw_journal,
    restore_game,
    select_prefix,
)
from risk_bench.viewer.prompts import preview_next_prompts
from risk_bench.viewer.snapshots import build_view_model
from tests.runner.test_continuation import NAMES, players, runner


@pytest.mark.parametrize("legacy", [False, True])
def test_round_boundary_restores_identical_requests_and_supports_forks(tmp_path, monkeypatch, legacy):
    monkeypatch.setattr("risk_bench.runner.journal.os.fsync", lambda fd: None)
    source = tmp_path / "parent" / "game.jsonl"
    writer = JournalWriter(source, False)
    writer.write_header(JournalHeader(4, 19, dict.fromkeys(NAMES, "random"), 4, True, 3, []))
    original = players()
    expected = runner(original, writer, turns=4).run()
    writer.close()
    records = raw_journal(source)
    if legacy:
        # Pre-round schema-4 checkpoints, including deltas, must remain resumable.
        writer = JournalWriter(source, False)
        for record in records:
            old = copy.deepcopy(record)
            if old["type"] == "transition":
                old["checkpoint"]["state"]["fields"].pop("round_number")
                old["checkpoint"]["state"]["fields"].pop("previous_round_number")
            writer._write_line(old)
        writer.close()
        assert any("checkpoint_delta" in json.loads(line) for line in source.read_text().splitlines())
    original_bytes = source.read_bytes()
    source_hash = prefix_hash(raw_journal(source))
    final = restore_game(source)
    assert final.state.round_number == 2
    model = build_view_model(source)
    assert {step["turn"]: step["round"] for step in model["steps"]} == {0: 0, 1: 1, 2: 1, 3: 1, 4: 2, 5: 2}

    boundary = next(
        record
        for record in records[1:]
        if record["checkpoint"]["state"]["fields"]["turn_number"] == 4
        and record["checkpoint"]["progress"]["stage"] == "note_request"
    )
    for side in ("before", "after"):
        cursor = f"{boundary['id']}:{side}"
        restored = restore_game(source, cursor)
        resumed_players = players()
        resumed = runner(resumed_players)
        assert resumed.resume(restored) == expected
        assert resumed.final_engine == (checkpoint.encode(final.state), final.rng.getstate())
        for name in NAMES:
            future = resumed_players[name].requests
            assert future == original[name].requests[-len(future) :]
        branch = tmp_path / side / "game.jsonl"
        fork_game(source, branch, cursor)
        assert restore_game(branch).state.round_number == 2
        assert restore_game(branch).header.ancestry["parent_prefix_hash"] == prefix_hash(
            select_prefix(raw_journal(source), cursor)
        )

    branch = tmp_path / "after" / "game.jsonl"
    preview = next(p for p in preview_next_prompts(branch, {}) if p.is_next_request)
    assert preview.request_kind == "noting"
    assert "Round 1 · Turn 3 just ended" in preview.before
    assert "Current board: Round 2 · Turn 4" in preview.before

    # Append directly to an old prefix, then continue: new deltas must use the old on-disk base.
    prefix = select_prefix(raw_journal(source), f"{boundary['id']}:after")
    paused = tmp_path / "paused" / "game.jsonl"
    paused.parent.mkdir()
    paused.write_text("".join(json.dumps(record) + "\n" for record in prefix))
    apply_additional_context(paused, {}, append=True)
    writer = JournalWriter(paused, True)
    assert runner(players(), writer).resume(restore_game(paused)) == expected
    writer.close()
    assert restore_game(paused).state.round_number == final.state.round_number
    assert prefix_hash(raw_journal(source)) == source_hash
    assert source.read_bytes() == original_bytes


def test_old_checkpoint_rounds_skip_eliminated_seats_without_dividing_turn_count():
    # Alice is eliminated during Bob's first turn. Rounds subsequently begin at Bob's seat.
    records: list[dict] = [{"type": "header", "player_specs": dict.fromkeys(NAMES, "random")}]
    for sequence, (turn, actor) in enumerate(
        [(0, "alice"), (1, "alice"), (2, "bob"), (3, "carla"), (4, "bob"), (5, "carla"), (6, "bob")]
    ):
        records.append(
            {
                "type": "transition",
                "id": sequence,
                "stage": "action",
                "records": [],
                "checkpoint": {
                    "state": {"fields": {"turn_number": turn, "current_player": actor}},
                },
            }
        )
    upgraded = [r["checkpoint"]["state"]["fields"] for r in expand_records(records) if r["type"] == "checkpoint"]
    assert [state["round_number"] for state in upgraded] == [0, 1, 1, 1, 2, 2, 3]
    assert [state["previous_round_number"] for state in upgraded] == [0, 0, 1, 1, 1, 2, 2]
    assert all("round_number" not in r["checkpoint"]["state"]["fields"] for r in records[1:])
