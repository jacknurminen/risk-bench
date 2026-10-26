import json
from concurrent.futures import ThreadPoolExecutor
from itertools import permutations

import pytest

from risk_bench import config
from risk_bench.agents.base import ReasoningEffort
from risk_bench.api import app as api_app
from risk_bench.api.schemas import NewGameRequest
from risk_bench.config import Settings, settings_for_new_game
from risk_bench.launch import run_new_game
from risk_bench.runner.journal import read_journal
from risk_bench.runner.tournament_evaluation import create_batch
from tests.api.test_tournament_continue import launch
from tests.api.test_tournament_continue import server as shared_server

server = shared_server


def play_saved(directory, game_id):
    saved = NewGameRequest.model_validate_json((directory / game_id / "launch.json").read_text())
    journal = directory / game_id / "game.jsonl"
    run_new_game(
        saved.players,
        saved.seed,
        settings_for_new_game(journal),
        [],
        journal,
        saved.max_turns,
        False,
        api_app._starting_context(saved),
        seat_order=saved.seating_orders[0] if saved.seating_orders else None,
    )


def finish_all(directory, manager):
    for game in json.loads((directory / "tournament.json").read_text())["game_ids"]:
        manager.pause_game(game)
        play_saved(directory, game)


@pytest.mark.parametrize("seating", ["cycle", "random"])
def test_replay_replaces_one_slot_preserves_original_and_does_not_advance_schedule(server, tmp_path, seating):
    client, manager = server
    directory = launch(client, tmp_path, seating_mode=seating, common_context="Shared", player_context=["A", "B", "C"])
    finish_all(directory, manager)
    manifest_path = directory / "tournament.json"
    original = json.loads(manifest_path.read_text())
    game_id = original["game_ids"][2]
    source = directory / game_id
    (source / "game.eval.error.json").write_text(json.dumps({"detail": "Pilot evaluation failed"}))
    originals = {path.name: path.read_bytes() for path in source.iterdir() if path.is_file()}
    batch = create_batch(directory, original, ["test:judge"], False)
    batch_bytes = batch.read_bytes()
    response = client.post(f"/api/tournaments/{directory.name}/games/{game_id}/replay")
    assert response.status_code == 202, response.text
    replacement = response.json()["id"]
    updated = json.loads(manifest_path.read_text())
    assert response.json()["total_games"] == 5
    assert updated["game_ids"] == original["game_ids"][:2] + [replacement] + original["game_ids"][3:]
    for key in ["seeds", "seating_orders", "seating_mode", "launch_config", "created_at", "parallel_games"]:
        assert updated[key] == original[key]
    assert {path.name: path.read_bytes() for path in source.iterdir() if path.is_file()} == originals
    assert batch.read_bytes() == batch_bytes
    replay = directory / replacement
    assert not (replay / "game.eval.error.json").exists()
    saved = json.loads((replay / "launch.json").read_text())
    assert saved["seed"] == 102
    assert saved["common_context"] == "Shared" and saved["player_context"] == ["A", "B", "C"]
    original_records = read_journal(source / "game.jsonl")
    names = list(api_app.specs.named_specs(specs=saved["players"]))
    assert [names[i - 1] for i in saved["seating_orders"][0]] == list(original_records[0]["player_specs"])
    view = client.get(f"/api/tournaments/{directory.name}").json()
    assert view["total_games"] == 5
    assert view["output_volume"]["game_count"] == 4  # Old attempt stops contributing immediately.
    assert game_id not in [game["id"] for game in view["games"]]
    assert view["previous_attempts"][0]["previous_game_id"] == game_id
    assert view["previous_attempts"][0]["available"]
    assert client.get(f"/api/games/{game_id}").status_code == 200
    manager.pause_game(replacement)
    play_saved(directory, replacement)
    records = read_journal(replay / "game.jsonl")
    assert records[0]["seed"] == original_records[0]["seed"]
    assert list(records[0]["player_specs"]) == list(original_records[0]["player_specs"])
    # Same initial deal, missions and board, including originally random seats.
    assert next(row["checkpoint"]["state"] for row in records if row["type"] == "checkpoint") == next(
        row["checkpoint"]["state"] for row in original_records if row["type"] == "checkpoint"
    )
    view = client.get(f"/api/tournaments/{directory.name}").json()
    assert view["output_volume"]["game_count"] == 5
    assert all(row["finished_games"] == 5 for row in view["table"])
    pinned = client.get(f"/api/tournaments/{directory.name}", params={"version": batch.stem})
    assert pinned.status_code == 200, pinned.text
    assert pinned.json()["research"]["availability"][2]["status"] == "Not included in this evaluation version"
    listing = client.get("/api/games").json()
    assert len(listing["games"]) == 5
    assert game_id not in [game["id"] for game in listing["games"]]
    # Next new game consumes seat permutation six, regardless of the replay.
    assert (
        client.post(
            f"/api/tournaments/{directory.name}/continue", json={"games": 1, "expected_total_games": 5}
        ).status_code
        == 202
    )
    continued = json.loads(manifest_path.read_text())
    assert continued["seeds"] == list(range(100, 106))
    assert len(continued["game_ids"]) == 6
    if seating == "cycle":
        assert continued["seating_orders"] == [list(order) for order in permutations([1, 2, 3])]


def test_replay_refuses_active_queued_and_evaluated_games_and_accepts_failed_unstarted_game(
    server, tmp_path, monkeypatch
):
    client, manager = server
    directory = launch(client, tmp_path)
    ids = json.loads((directory / "tournament.json").read_text())["game_ids"]

    def url(game):
        return f"/api/tournaments/{directory.name}/games/{game}/replay"

    assert client.post(url(ids[0])).status_code == 409
    assert client.post(url(ids[2])).status_code == 409
    manager.pause_game(ids[2])
    with monkeypatch.context() as patch:
        patch.setattr(manager, "is_evaluating", lambda game_id: game_id == ids[2])
        assert client.post(url(ids[2])).status_code == 409
    response = client.post(url(ids[2]))
    assert response.status_code == 202, response.text
    assert response.json()["queued"]
    assert manager.is_queued(response.json()["id"])
    assert manager._context.peak == 2
    # A stale old-attempt request cannot consume another slot or create another replay.
    assert client.post(url(ids[2])).status_code == 409


def test_concurrent_replays_create_only_one_replacement(server, tmp_path):
    client, manager = server
    directory = launch(client, tmp_path)
    game = json.loads((directory / "tournament.json").read_text())["game_ids"][2]
    manager.pause_game(game)
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(
            pool.map(lambda _: client.post(f"/api/tournaments/{directory.name}/games/{game}/replay"), range(2))
        )
    assert sorted(reply.status_code for reply in replies) == [202, 409]
    manifest = json.loads((directory / "tournament.json").read_text())
    assert len(manifest["game_ids"]) == 5 and len(manifest["replays"]) == 1


def test_replay_controls_remain_available_when_every_replacement_has_not_started(server, tmp_path):
    client, manager = server
    directory = launch(client, tmp_path)
    originals = json.loads((directory / "tournament.json").read_text())["game_ids"]
    for game in originals:
        manager.pause_game(game)
    for game in originals:
        response = client.post(f"/api/tournaments/{directory.name}/games/{game}/replay")
        assert response.status_code == 202, response.text
    response = client.get(f"/api/tournaments/{directory.name}")
    assert response.status_code == 200, response.text
    view = response.json()
    assert view["total_games"] == 5 and view["games"] == []
    assert view["output_volume"]["game_count"] == 0
    assert len(view["replay_slots"]) == len(view["previous_attempts"]) == 5
    assert view["turns"] == []
    assert client.get(f"/tournaments/{directory.name}").status_code == 200


def test_failed_preparation_keeps_current_attempt_and_original_files(server, tmp_path, monkeypatch):
    client, manager = server
    directory = launch(client, tmp_path)
    path = directory / "tournament.json"
    original = path.read_bytes()
    game = json.loads(original)["game_ids"][2]
    manager.pause_game(game)

    def fail(*args):
        raise OSError("Disk full")

    monkeypatch.setattr(api_app, "_prepare_game_launch", fail)
    with pytest.raises(OSError, match="Disk full"):
        client.post(f"/api/tournaments/{directory.name}/games/{game}/replay")
    assert path.read_bytes() == original
    assert (directory / game / "launch.json").is_file()
    assert not list(directory.glob("*-replay-*"))


def test_saved_replay_settings_apply_after_queue_resume_without_restoring_credentials(tmp_path, monkeypatch):
    settings = Settings(
        max_agent_tokens=111, agent_reasoning_effort=ReasoningEffort.LOW, anthropic_aalto_api_key="current-test-key"
    )
    monkeypatch.setattr(config, "load_settings", lambda: settings)
    (tmp_path / "replay-settings.json").write_text(
        json.dumps(
            {
                "max_agent_tokens": 999,
                "agent_reasoning_effort": "high",
                "allow_private_messages": True,
                "max_negotiation_rounds": 7,
                "anthropic_aalto_api_key": "must-not-restore",
            }
        )
    )
    # Re-preparing an unstarted replay does not discard its settings sidecar.
    api_app._prepare_game_launch(tmp_path, NewGameRequest(players=["random"] * 3), 100)
    restored = settings_for_new_game(tmp_path / "game.jsonl")
    assert restored.max_agent_tokens == 999
    assert restored.agent_reasoning_effort is ReasoningEffort.HIGH
    assert restored.allow_private_messages and restored.max_negotiation_rounds == 7
    assert restored.anthropic_aalto_api_key == "current-test-key"
