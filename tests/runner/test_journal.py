import json
import random
from pathlib import Path

import pytest

from risk_bench.game.ruleset import CURRENT_RULESET, LEGACY_RULESET
from risk_bench.game.types import Phase, PlayerId, Territory
from risk_bench.players.base import Player
from risk_bench.players.random import RandomPlayer
from risk_bench.runner.game_runner import GameRunner
from risk_bench.runner.journal import (
    SCHEMA_VERSION,
    JournalHeader,
    JournalWriter,
    read_journal,
    restore_game,
    ruleset_for_schema_version,
)

PLAYER_NAMES = ["alice", "bob", "carla"]
LEGACY_GAME_HEADERS = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "legacy-game-headers.json").read_text(encoding="utf-8")
)


def journaled_runner(journal: JournalWriter | None, seed: int, max_turns: int) -> GameRunner:
    players: dict[PlayerId, Player] = {
        name: RandomPlayer(player_id=name, rng=random.Random(f"{seed}:{name}")) for name in PLAYER_NAMES
    }
    return GameRunner(
        players=players,
        additional_context={},
        seed=seed,
        max_turns=max_turns,
        allow_private_messages=False,
        max_negotiation_rounds=3,
        commentators=(),
        max_concurrent_commentators=1,
        journal=journal,
        trace=None,
        verbose=False,
    )


def write_game_journal(path: Path, seed: int, max_turns: int):
    journal = JournalWriter(path=path, append=False)
    journal.write_header(
        header=JournalHeader(
            schema_version=SCHEMA_VERSION,
            seed=seed,
            player_specs={name: "random" for name in PLAYER_NAMES},
            max_turns=max_turns,
            allow_private_messages=False,
            max_negotiation_rounds=3,
            commentators=[],
        )
    )
    record = journaled_runner(journal=journal, seed=seed, max_turns=max_turns).run()
    journal.close()
    return record


def test_a_finished_game_replays_to_the_recorded_outcome(tmp_path: Path):
    path = tmp_path / "game.jsonl"
    record = write_game_journal(path=path, seed=11, max_turns=2_000)

    restored = restore_game(path=path)

    assert restored.is_finished
    assert restored.state.winner == record.winner
    assert restored.state.turn_number == record.turn_count
    assert restored.header.player_specs == {name: "random" for name in PLAYER_NAMES}


def test_journal_records_actions_events_and_result_lines(tmp_path: Path):
    path = tmp_path / "game.jsonl"
    write_game_journal(path=path, seed=11, max_turns=2_000)

    records = read_journal(path=path)
    record_types = {record["type"] for record in records}

    assert records[0]["type"] == "header"
    assert records[-1]["type"] == "result"
    assert {"action", "events"} <= record_types


def test_a_truncated_journal_restores_to_a_live_resumable_state(tmp_path: Path):
    full_path = tmp_path / "full.jsonl"
    write_game_journal(path=full_path, seed=11, max_turns=2_000)
    full_lines = full_path.read_text(encoding="utf-8").splitlines()

    # simulate a crash: keep the header and roughly half the game, cutting the last line in two
    crashed_path = tmp_path / "crashed.jsonl"
    kept = full_lines[: len(full_lines) // 2]
    crashed_path.write_text("\n".join(kept) + "\n" + kept[-1][: len(kept[-1]) // 2], encoding="utf-8")

    restored = restore_game(path=crashed_path)

    assert not restored.is_finished
    assert restored.state.phase is not Phase.GAME_OVER
    assert restored.state.winner is None
    assert sum(restored.state.armies[territory] for territory in Territory) > 42


def test_a_restored_game_resumes_and_finishes(tmp_path: Path):
    # a tight turn limit ends the run as a draw, leaving a live state in the journal
    path = tmp_path / "game.jsonl"
    write_game_journal(path=path, seed=11, max_turns=3)
    # drop the result line so the journal reads as unfinished, like a crash at the turn limit
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")

    restored = restore_game(path=path)
    assert restored.state.turn_number >= 3

    journal = JournalWriter(path=path, append=True)
    record = journaled_runner(journal=journal, seed=11, max_turns=2_000).resume(restored=restored)
    journal.close()

    # A committed terminal transition remains terminal even if result publication was interrupted.
    assert record.winner is None
    assert record.turn_count == restored.state.turn_number
    assert read_journal(path=path)[-1]["type"] == "result"


def test_resuming_a_finished_game_is_rejected(tmp_path: Path):
    path = tmp_path / "game.jsonl"
    write_game_journal(path=path, seed=11, max_turns=2_000)
    restored = restore_game(path=path)

    with pytest.raises(ValueError, match="already finished"):
        journaled_runner(journal=None, seed=11, max_turns=2_000).resume(restored=restored)


def test_thoughts_are_journaled_but_ignored_by_replay(tmp_path: Path):
    from tests.runner.test_game_runner import whisperers

    path = tmp_path / "game.jsonl"
    journal = JournalWriter(path=path, append=False)
    journal.write_header(
        header=JournalHeader(
            schema_version=SCHEMA_VERSION,
            seed=7,
            player_specs={name: "random" for name in PLAYER_NAMES},
            max_turns=3,
            allow_private_messages=True,
            max_negotiation_rounds=3,
            commentators=[],
        )
    )
    GameRunner(
        players=whisperers(seed=7),
        additional_context={},
        seed=7,
        max_turns=3,
        allow_private_messages=True,
        max_negotiation_rounds=3,
        commentators=(),
        max_concurrent_commentators=1,
        journal=journal,
        trace=None,
        verbose=False,
    ).run()
    journal.close()

    thoughts = [record for record in read_journal(path=path) if record["type"] == "thought"]
    assert thoughts and all(record["text"] == "hoping for a truce" for record in thoughts)
    assert {record["player"] for record in thoughts} == set(PLAYER_NAMES)
    restored = restore_game(path=path)  # thought lines are observational; replay must skip them
    assert restored.is_finished


def test_headers_from_before_multi_pass_negotiation_restore_with_one_round(tmp_path: Path):
    path = tmp_path / "game.jsonl"
    write_game_journal(path=path, seed=11, max_turns=2_000)
    lines = path.read_text(encoding="utf-8").splitlines()
    legacy_header = json.loads(lines[0])
    del legacy_header["max_negotiation_rounds"]
    path.write_text("\n".join([json.dumps(legacy_header)] + lines[1:]) + "\n", encoding="utf-8")

    with pytest.raises(KeyError):
        restore_game(path=path)


def test_journals_from_before_schema_three_replay_under_the_legacy_ruleset(tmp_path: Path):
    assert ruleset_for_schema_version(schema_version=2) is LEGACY_RULESET
    assert ruleset_for_schema_version(schema_version=3) is CURRENT_RULESET
    assert ruleset_for_schema_version(schema_version=SCHEMA_VERSION) is CURRENT_RULESET

    path = tmp_path / "game.jsonl"
    header = {
        "type": "header",
        "schema_version": 2,
        "seed": 0,
        "player_specs": {name: "random" for name in PLAYER_NAMES},
    }
    header.update({"max_turns": 10, "allow_private_messages": False, "max_negotiation_rounds": 1, "commentators": []})
    path.write_text(json.dumps(header) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="v2 Git tag"):
        restore_game(path=path)


def test_legacy_reference_game_header_is_rejected(tmp_path: Path):
    path = tmp_path / "game.jsonl"
    path.write_text(json.dumps(LEGACY_GAME_HEADERS["20260908-092609-seed0"]) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="v2 Git tag"):
        restore_game(path=path)


def test_restore_rejects_files_without_a_header(tmp_path: Path):
    path = tmp_path / "not-a-journal.jsonl"
    path.write_text('{"type": "action"}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="missing header"):
        restore_game(path=path)


def test_note_records_are_observational_and_ignored_on_restore(tmp_path: Path):
    path = tmp_path / "game.jsonl"
    journal = JournalWriter(path=path, append=False)
    journal.write_header(
        header=JournalHeader(
            schema_version=SCHEMA_VERSION,
            seed=1,
            player_specs={name: "random" for name in PLAYER_NAMES},
            max_turns=10,
            allow_private_messages=False,
            max_negotiation_rounds=3,
            commentators=[],
        )
    )
    journal.write_note(player_id="alice", turn_number=2, text="bob is massing on my border")
    journal.close()

    with pytest.raises(ValueError, match="requires execution checkpoints"):
        restore_game(path=path)
