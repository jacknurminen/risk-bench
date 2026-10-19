import json
import threading
from itertools import permutations
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from risk_bench.api import app as api_app
from risk_bench.api.processes import GameProcessManager
from risk_bench.config import Settings
from risk_bench.launch import run_new_game
from risk_bench.runner.journal import read_journal
from tests.runner.test_journal import write_game_journal


class FakeProcess:
    def __init__(self, owner, **kwargs):
        self.owner = owner
        self.arguments = kwargs["args"]
        self.alive = False

    def start(self):
        if Path(self.arguments[0]).parent.name in self.owner.failures:
            raise OSError("Cannot spawn")
        self.alive = True
        self.owner.started.append(self)
        self.owner.peak = max(self.owner.peak, sum(p.alive for p in self.owner.started))
        self.owner.changed.set()

    def is_alive(self):
        return self.alive

    def terminate(self):
        self.alive = False

    kill = terminate

    def join(self, timeout=0):
        pass


class FakeContext:
    def __init__(self):
        self.started = []
        self.failures = set()
        self.peak = 0
        self.changed = threading.Event()

    def Process(self, **kwargs):
        return FakeProcess(self, **kwargs)


@pytest.fixture
def manager():
    manager = GameProcessManager()
    manager._context = FakeContext()
    yield manager
    manager.shutdown()


def enqueue(manager, root, game_id):
    folder = root / game_id
    folder.mkdir(exist_ok=True)
    manager.launch_game(game_id, ["random"] * 3, 1, [], 1, [], folder / "game.jsonl", folder / "worker.log")


def test_ten_games_use_two_slots_and_refill_without_browser_polling(tmp_path, manager):
    ids = [f"g{i}" for i in range(10)]
    manager.register_tournament("t", ids, 2)
    for game_id in ids:
        enqueue(manager, tmp_path, game_id)
    context = manager._context
    assert len(context.started) == 2
    assert all(process.arguments[-1] is None for process in context.started)
    assert all(manager.is_queued(game_id) for game_id in ids[2:])
    for expected in range(3, 11):
        context.changed.clear()
        next(p for p in context.started if p.alive).alive = False
        assert context.changed.wait(2), "Queue did not advance without a request"
        assert len(context.started) == expected
    assert context.peak == 2
    assert [Path(p.arguments[0]).parent.name for p in context.started] == ids


def test_cancel_resume_and_shutdown_respect_the_limit(tmp_path, manager):
    manager.register_tournament("t", ["a", "b", "c"], 1)
    for game_id in ["a", "b", "c"]:
        enqueue(manager, tmp_path, game_id)
    assert manager.pause_game("b")
    assert not manager.is_queued("b")
    manager.resume_game("b", tmp_path / "b/game.jsonl", [], tmp_path / "b/worker.log")
    assert manager.is_queued("b")
    assert len(manager._context.started) == 1
    assert manager.pause_game("a")
    manager._drain_queue()
    assert manager.is_running("c") and manager.is_queued("b")
    manager.shutdown()
    manager._drain_queue()
    assert not any(p.alive for p in manager._context.started)
    assert len(manager._context.started) == 2
    assert not manager.is_queued("b")


def test_failed_worker_and_failed_spawn_release_slots(tmp_path, manager):
    manager.register_tournament("t", ["a", "b", "c"], 1)
    manager._context.failures.add("b")
    for game_id in ["a", "b", "c"]:
        enqueue(manager, tmp_path, game_id)
    manager._context.started[0].alive = False
    manager._drain_queue()
    assert manager.is_running("c")
    assert not manager.is_queued("b")
    assert "Cannot spawn" in (tmp_path / "b/worker.log").read_text()
    assert manager._context.peak == 1


def test_separate_tournaments_have_independent_limits(tmp_path, manager):
    for group in ["first", "second"]:
        ids = [group + str(i) for i in range(3)]
        manager.register_tournament(group, ids, 1)
        for game_id in ids:
            enqueue(manager, tmp_path, game_id)
    assert len(manager._context.started) == 2
    assert manager.is_running("first0") and manager.is_running("second0")


def test_api_lists_all_queued_games_and_can_cancel_and_requeue(tmp_path, manager, monkeypatch):
    monkeypatch.setattr(api_app, "GameProcessManager", lambda: manager)
    monkeypatch.setattr(api_app, "_wait_for_journal_header", lambda *args, **kwargs: None)
    with TestClient(api_app.create_app(tmp_path)) as client:
        response = client.post("/api/games", json={
            "players": ["random"] * 3, "seed": 100, "games": 10, "parallel_games": 2,
        })
        assert response.status_code == 201
        tournament = response.json()["id"]
        listing = client.get("/api/games").json()
        assert len(listing["games"]) == 10
        assert sum(game["running"] for game in listing["games"]) == 2
        assert sum(game["queued"] for game in listing["games"]) == 8
        assert [g["seed"] for g in listing["games"]] == list(range(100, 110))
        game_id = f"{tournament}-g3"
        assert client.delete(f"/api/games/{game_id}").status_code == 409
        assert client.post(f"/api/games/{game_id}/pause").status_code == 204
        assert not manager.is_queued(game_id)
        assert client.post(f"/api/games/{game_id}/resume").status_code == 204
        assert manager.is_queued(game_id)
        assert client.post(f"/api/games/{game_id}/resume").status_code == 409
        assert len(manager._context.started) == 2


@pytest.mark.parametrize("field,value", [("games", 0), ("games", 1001), ("parallel_games", 0), ("parallel_games", 21)])
def test_invalid_tournament_sizes_are_rejected(tmp_path, field, value):
    with TestClient(api_app.create_app(tmp_path)) as client:
        assert client.post("/api/games", json={"players": ["random"] * 3, field: value}).status_code == 422


def test_cycle_generates_exact_schedule_and_restores_queued_seats(tmp_path, manager, monkeypatch):
    monkeypatch.setattr(api_app, "GameProcessManager", lambda: manager)
    monkeypatch.setattr(api_app, "_wait_for_journal_header", lambda *args, **kwargs: None)
    orders = [list(order) for order in permutations([1, 2, 3])] * 2
    with TestClient(api_app.create_app(tmp_path)) as client:
        response = client.post("/api/games", json={
            "players": ["random"] * 3, "seed": 100, "games": 12, "parallel_games": 2,
            "seating_mode": "cycle", "player_context": ["first", "second", "third"],
        })
        assert response.status_code == 201, response.text
        tournament = response.json()["id"]
        root = tmp_path / tournament
        manifest = json.loads((root / "tournament.json").read_text())
        assert manifest["seating_orders"] == orders
        assert manifest["seating_mode"] == "cycle"
        assert manifest["seeds"] == list(range(100, 112))
        for index, game_id in enumerate(manifest["game_ids"]):
            saved = json.loads((root / game_id / "launch.json").read_text())
            assert saved["seating_orders"] == [orders[index]]
            assert saved["player_context"] == ["first", "second", "third"]
        # A queued game resumed from its launch record retains the exact order.
        game_id = manifest["game_ids"][4]
        assert client.post(f"/api/games/{game_id}/pause").status_code == 204
        assert client.post(f"/api/games/{game_id}/resume").status_code == 204
        assert manager._pending[game_id][1][-1] == orders[4]
        for process, order in zip(manager._context.started, orders):
            assert process.arguments[-1] == order


@pytest.mark.parametrize("orders", [[], [[1, 2, 3]], [[1, 1, 3]] * 2, [[0, 1, 2]] * 2,
                                   [[1, 2, 3, 4]] * 2, [[True, 2, 3]] * 2])
def test_invalid_seating_schedule_is_rejected_before_launch(tmp_path, orders):
    with TestClient(api_app.create_app(tmp_path)) as client:
        response = client.post("/api/games", json={
            "players": ["random"] * 3, "games": 2, "seating_orders": orders,
        })
        assert response.status_code == 422
        assert not list(tmp_path.glob("*/launch.json"))


@pytest.mark.parametrize("order", list(permutations([1, 2, 3])))
def test_played_game_uses_exact_seats_and_keeps_player_context(tmp_path, order):
    journal = tmp_path / "game.jsonl"
    run_new_game(["random"] * 3, 19, Settings(), [], journal, 1, False,
                 ["first", "second", "third"], seat_order=list(order))
    records = read_journal(journal)
    names = ["random", "random#2", "random#3"]
    assert list(records[0]["player_specs"]) == [names[i - 1] for i in order]
    # Player identity stays tied to the roster, even with identical model specs.
    checkpoint = next(row for row in records if row["type"] == "checkpoint")
    assert checkpoint["checkpoint"]["additional_context"] == dict(zip(names, ["first", "second", "third"]))


def test_tournament_view_counts_games_that_have_not_started(tmp_path):
    folder = tmp_path / "tournament"
    game = folder / "g1"
    game.mkdir(parents=True)
    write_game_journal(game / "game.jsonl", seed=19, max_turns=1)
    (folder / "tournament.json").write_text(json.dumps({
        "id": "tournament", "game_ids": ["g1", "g2", "g3"], "parallel_games": 1,
    }))
    with TestClient(api_app.create_app(tmp_path)) as client:
        response = client.get("/api/tournaments/tournament")
        assert response.status_code == 200
        view = response.json()
        assert view["total_games"] == 3 and view["parallel_games"] == 1
        assert len(view["games"]) == 1
        assert view["games"][0]["finished"]
        page = client.get("/tournaments/tournament")
        assert page.status_code == 200
