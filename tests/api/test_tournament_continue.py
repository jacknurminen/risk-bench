import json
from concurrent.futures import ThreadPoolExecutor
from itertools import permutations

import pytest
from fastapi.testclient import TestClient

from risk_bench.api import app as api_app
from risk_bench.api.processes import GameProcessManager
from risk_bench.runner.tournament_evaluation import create_batch
from tests.api.test_tournament_queue import FakeContext
from tests.runner.test_journal import write_game_journal


@pytest.fixture
def server(tmp_path, monkeypatch):
    manager = GameProcessManager()
    manager._context = FakeContext()
    monkeypatch.setattr(api_app, "GameProcessManager", lambda: manager)
    monkeypatch.setattr(api_app, "_wait_for_journal_header", lambda *args, **kwargs: None)
    with TestClient(api_app.create_app(tmp_path)) as client:
        yield client, manager


def launch(client, root, **options):
    response = client.post(
        "/api/games",
        json={
            "players": ["random"] * 3,
            "seed": 100,
            "games": 5,
            "parallel_games": 2,
            "max_turns": 1,
            "evaluation_method": "whole_game",
            **options,
        },
    )
    assert response.status_code == 201, response.text
    return root / response.json()["id"]


def finish(directory, manager):
    manifest = json.loads((directory / "tournament.json").read_text())
    for game_id, seed in zip(manifest["game_ids"], manifest["seeds"]):
        manager.pause_game(game_id)
        journal = directory / game_id / "game.jsonl"
        if not journal.exists():
            write_game_journal(journal, seed=seed, max_turns=1)


@pytest.mark.parametrize("mode", ["random", "cycle"])
def test_five_then_five_then_two_keep_setup_schedule_and_aggregate_all_games(server, tmp_path, monkeypatch, mode):
    client, manager = server
    monkeypatch.setattr(api_app.specs, "build_evaluators", lambda **kwargs: [])
    directory = launch(
        client,
        tmp_path,
        seating_mode=mode,
        common_context="Shared context",
        player_context=["First", "Second", "Third"],
        evaluators=["test:judge"],
    )
    finish(directory, manager)
    originals = {path: path.read_bytes() for path in directory.glob("*/game.jsonl")}
    original = json.loads((directory / "tournament.json").read_text())
    batch_path = create_batch(directory, original, ["test:judge"], False)
    batch_bytes = batch_path.read_bytes()
    url = f"/api/tournaments/{directory.name}/continue"
    response = client.post(url, json={"games": 5, "expected_total_games": 5})
    assert response.status_code == 202, response.text
    assert response.json()["total_games"] == 10
    assert [manager.is_running(game) for game in response.json()["game_ids"]] == [True, True, False, False, False]
    assert all(manager.is_queued(game) for game in response.json()["game_ids"][2:])
    assert all(path.read_bytes() == content for path, content in originals.items())
    assert batch_path.read_bytes() == batch_bytes
    finish(directory, manager)
    # Old evaluation collections do not fall back to new games' current evaluations.
    view = client.get(f"/api/tournaments/{directory.name}", params={"version": batch_path.stem})
    assert view.status_code == 200, view.text
    assert all(
        row["status"] == "Not included in this evaluation version"
        for row in view.json()["research"]["availability"][5:]
    )
    response = client.post(url, json={"games": 2, "expected_total_games": 10})
    assert response.status_code == 202, response.text
    manifest = json.loads((directory / "tournament.json").read_text())
    assert manifest["created_at"] == original["created_at"]
    assert manifest["seeds"] == list(range(100, 112))
    assert len(set(manifest["game_ids"])) == 12
    orders = [list(order) for order in permutations([1, 2, 3])] * 2 if mode == "cycle" else None
    assert manifest["seating_orders"] == orders
    for index, game_id in enumerate(manifest["game_ids"]):
        saved = json.loads((directory / game_id / "launch.json").read_text())
        assert saved["players"] == ["random"] * 3
        assert saved["seed"] == 100 + index
        assert saved["max_turns"] == 1
        assert saved["common_context"] == "Shared context"
        assert saved["player_context"] == ["First", "Second", "Third"]
        assert saved["seating_orders"] == ([orders[index]] if orders else None)
        assert saved["evaluation_method"] == "whole_game"
        assert saved["evaluators"] == ["test:judge"]
        assert json.loads((directory / game_id / "whole-game-evaluators.json").read_text())["evaluators"] == [
            "test:judge"
        ]
    finish(directory, manager)
    view = client.get(f"/api/tournaments/{directory.name}").json()
    assert view["total_games"] == len(view["games"]) == view["output_volume"]["game_count"] == 12
    assert all(row["finished_games"] == 12 for row in view["table"])
    assert view["game_count_by_turn"][0] == 12
    assert manager._context.peak == 2
    listing = client.get("/api/games").json()
    assert len(listing["tournaments"]) == 1
    assert len(listing["tournaments"][0]["game_ids"]) == 12


def test_append_while_running_and_after_restart_preserves_queue_limit_and_resumability(server, tmp_path, monkeypatch):
    client, manager = server
    directory = launch(client, tmp_path, seating_mode="cycle")
    # Exercise compatibility with tournaments created before launch_config was saved.
    path = directory / "tournament.json"
    manifest = json.loads(path.read_text())
    del manifest["launch_config"]
    path.write_text(json.dumps(manifest))
    response = client.post(f"/api/tournaments/{directory.name}/continue", json={"games": 5, "expected_total_games": 5})
    assert response.status_code == 202, response.text
    new_ids = response.json()["game_ids"]
    assert all(manager.is_queued(game) for game in new_ids)
    assert manager._context.peak == 2
    manager.shutdown()
    replacement = GameProcessManager()
    replacement._context = FakeContext()
    monkeypatch.setattr(api_app, "GameProcessManager", lambda: replacement)
    with TestClient(api_app.create_app(tmp_path)) as restarted:
        for game in new_ids[:3]:
            assert restarted.post(f"/api/games/{game}/resume").status_code == 204
        assert replacement.is_queued(new_ids[2])
        assert replacement._context.peak == 2
        saved = json.loads((directory / new_ids[0] / "launch.json").read_text())
        assert replacement._context.started[0].arguments[-1] == saved["seating_orders"][0]


def test_concurrent_duplicate_submissions_only_append_once(server, tmp_path):
    client, _ = server
    directory = launch(client, tmp_path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        replies = list(
            executor.map(
                lambda _: client.post(
                    f"/api/tournaments/{directory.name}/continue", json={"games": 5, "expected_total_games": 5}
                ),
                range(2),
            )
        )
    assert sorted(response.status_code for response in replies) == [202, 409]
    assert len(json.loads((directory / "tournament.json").read_text())["game_ids"]) == 10


@pytest.mark.parametrize("games", [0, -1, 1001, 1.5, True])
def test_invalid_count_does_not_mutate_tournament(server, tmp_path, games):
    client, manager = server
    directory = launch(client, tmp_path)
    original = (directory / "tournament.json").read_bytes()
    response = client.post(
        f"/api/tournaments/{directory.name}/continue", json={"games": games, "expected_total_games": 5}
    )
    assert response.status_code == 422
    assert (directory / "tournament.json").read_bytes() == original
    assert len(manager._pending) == 3


def test_missing_config_and_total_limit_are_reported_before_writing(server, tmp_path):
    client, _ = server
    directory = launch(client, tmp_path)
    url = f"/api/tournaments/{directory.name}/continue"
    assert client.post(url, json={"games": 1000, "expected_total_games": 5}).status_code == 400
    path = directory / "tournament.json"
    manifest = json.loads(path.read_text())
    del manifest["launch_config"]
    path.write_text(json.dumps(manifest))
    for record in directory.glob("*/launch.json"):
        record.unlink()
    original = path.read_bytes()
    assert client.post(url, json={"games": 5, "expected_total_games": 5}).status_code == 409
    assert path.read_bytes() == original


def test_preparation_failure_rolls_back_without_starting_or_adding_games(server, tmp_path, monkeypatch):
    client, manager = server
    directory = launch(client, tmp_path)
    original = (directory / "tournament.json").read_bytes()
    prepare = api_app._prepare_game_launch

    def failing_prepare(folder, request, seed):
        if seed == 106:
            raise OSError("Disk full")
        prepare(folder, request, seed)

    monkeypatch.setattr(api_app, "_prepare_game_launch", failing_prepare)
    with pytest.raises(OSError, match="Disk full"):
        client.post(f"/api/tournaments/{directory.name}/continue", json={"games": 5, "expected_total_games": 5})
    assert (directory / "tournament.json").read_bytes() == original
    assert len(manager._pending) == 3
    assert not (directory / f"{directory.name}-g6").exists()
    assert not (directory / f"{directory.name}-g7").exists()
