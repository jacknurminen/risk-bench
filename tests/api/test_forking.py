import json

import pytest

from risk_bench.config import Settings
from risk_bench.launch import run_new_game
from risk_bench.players.human import mailbox_for
from risk_bench.runner.forking import fork_game
from risk_bench.runner.journal import raw_journal, restore_game
from tests.api.test_app import client_for
from tests.api.test_human import wait_for


def test_historical_api_fork_runs_independently_and_exposes_cursors(tmp_path):
    source = tmp_path / "games" / "parent" / "game.jsonl"
    run_new_game(["random"] * 3, 19, Settings(), [], source, 1, False, [])
    parent_bytes = source.read_bytes()
    with client_for(tmp_path) as client:
        view = client.get("/api/games/parent").json()
        assert view["cursors"] and view["steps"][1]["cursor"]
        response = client.post("/api/games/parent/fork", json={"cursor": "0:after"})
        assert response.status_code == 201, response.text
        branch = response.json()["id"]
        restored = wait_for(client, f"/api/games/{branch}", lambda view: view["result"] is not None)
        assert restored["steps"] == view["steps"] or [s["board"] for s in restored["steps"]] == [
            s["board"] for s in view["steps"]
        ]
        assert restored["result"] == view["result"]
        assert restored["ancestry"]["parent_game_id"] == "parent"
        assert source.read_bytes() == parent_bytes
        branch_path = tmp_path / "games" / branch / "game.jsonl"
        assert mailbox_for(branch_path) != mailbox_for(source)
        assert restore_game(branch_path).is_finished
        final = raw_journal(source)[-1]
        response = client.post("/api/games/parent/fork", json={"cursor": f"{final['id']}:after"})
        assert response.status_code == 409


def test_a_paused_branch_is_briefed_before_it_runs(tmp_path):
    source = tmp_path / "games" / "parent" / "game.jsonl"
    run_new_game(["random"] * 3, 19, Settings(), [], source, 2, False, [])
    with client_for(tmp_path) as client:
        view = client.get("/api/games/parent").json()
        cursor = view["cursors"][len(view["cursors"]) // 2]["before"]
        response = client.post("/api/games/parent/fork", json={"cursor": cursor, "start": False})
        assert response.status_code == 201, response.text
        branch = response.json()["id"]
        branch_view = client.get(f"/api/games/{branch}").json()
        assert branch_view["running"] is False and branch_view["result"] is None
        assert branch_view["ancestry"]["parent_cursor"] == cursor
        assert not any(branch_view["additional_context"].values())

        player = branch_view["players"][0]["name"]
        preview = client.post(f"/api/games/{branch}/prompt-preview", json={"context": {player: "Asia is contested."}})
        assert preview.status_code == 200, preview.text
        briefed = next(entry for entry in preview.json()["prompts"] if entry["player"] == player)
        assert "Asia is contested." in briefed["after"] and "Asia is contested." not in briefed["before"]
        assert sum(entry["is_next_request"] for entry in preview.json()["prompts"]) == 1

        edit = client.post(f"/api/games/{branch}/context", json={"context": {player: "Asia is contested."}})
        assert edit.status_code == 204, edit.text
        assert client.get(f"/api/games/{branch}").json()["additional_context"][player] == "Asia is contested."
        assert client.post(f"/api/games/{branch}/context", json={"context": {"nobody": "x"}}).status_code == 409

        client.post(f"/api/games/{branch}/resume")
        finished = wait_for(client, f"/api/games/{branch}", lambda view: view["result"] is not None)
        assert finished["additional_context"][player] == "Asia is contested."
        assert not any(client.get("/api/games/parent").json()["additional_context"].values())


def test_a_worker_that_dies_at_startup_is_reported_instead_of_looking_resumed(tmp_path):
    source = tmp_path / "games" / "parent" / "game.jsonl"
    run_new_game(["random"] * 3, 19, Settings(), [], source, 2, False, [])
    branch = tmp_path / "games" / "broken" / "game.jsonl"
    with client_for(tmp_path) as client:
        cursor = client.get("/api/games/parent").json()["cursors"][8]["before"]
        fork_game(source, branch, cursor)
        # a controller the worker cannot build: the API reads only the header, so it resumes happily
        # and the failure lands in the worker, which is exactly the case that used to pass silently
        lines = branch.read_text(encoding="utf-8").splitlines(keepends=True)
        header = json.loads(lines[0])
        first_player = next(iter(header["player_specs"]))
        header["player_specs"][first_player] = "notaprovider:model"
        branch.write_text(json.dumps(header, separators=(",", ":")) + "\n" + "".join(lines[1:]), encoding="utf-8")
        untouched = branch.read_bytes()

        response = client.post("/api/games/broken/resume")

        assert response.status_code == 409, response.text
        assert "worker exited at startup" in response.json()["detail"]
        assert "Unknown provider" in response.json()["detail"]
        assert branch.read_bytes() == untouched


def test_a_game_can_be_launched_with_shared_and_per_player_context(tmp_path):
    with client_for(tmp_path) as client:
        response = client.post(
            "/api/games",
            json={
                "players": ["random", "random", "random"],
                "seed": 5,
                "max_turns": 2,
                "common_context": "  The table has played together before.  ",
                "player_context": ["", "You owe random a favour.", ""],
            },
        )
        assert response.status_code == 201, response.text
        game_id = response.json()["id"]
        view = wait_for(client, f"/api/games/{game_id}", lambda view: view["result"] is not None)
        # the shared line reaches every seat; the per-seat line only its own, joined beneath it
        assert sorted(view["additional_context"].values()) == [
            "The table has played together before.",
            "The table has played together before.",
            "The table has played together before.\n\nYou owe random a favour.",
        ]

        mismatched = client.post("/api/games", json={"players": ["random"] * 3, "player_context": ["only one"]})
        assert mismatched.status_code == 400
        assert "one entry per player" in mismatched.json()["detail"]


@pytest.mark.parametrize("first_operation", ["context", "resume"])
def test_context_save_and_worker_start_are_serialized(tmp_path, monkeypatch, first_operation):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from risk_bench.api import app as api
    from risk_bench.api.processes import GameProcessManager

    monkeypatch.setattr("risk_bench.runner.journal.os.fsync", lambda fd: None)
    source = tmp_path / "games" / "parent" / "game.jsonl"
    run_new_game(["random"] * 3, 19, Settings(), [], source, 1, False, [])
    branch = tmp_path / "games" / "branch" / "game.jsonl"
    fork_game(source, branch, "0:after")
    player = next(iter(restore_game(branch).header.player_specs))
    first_entered, release_first, second_attempted, running = (Event() for _ in range(4))
    observed_at_start = []
    edit_context = api.apply_additional_context
    control_lock = api.journal_control_lock

    def observe_attempt(path):
        if first_entered.is_set():
            second_attempted.set()
        return control_lock(path)

    def save_context(**kwargs):
        if first_operation == "context":
            first_entered.set()
            assert release_first.wait(5)
        edit_context(**kwargs)

    def start_worker(self, game_id, journal_path, commentators, log_path):
        if first_operation == "resume":
            first_entered.set()
            assert release_first.wait(5)
        restored = restore_game(journal_path)
        assert restored.checkpoint is not None
        observed_at_start.append(restored.checkpoint["additional_context"].get(player))
        running.set()

    monkeypatch.setattr(api, "journal_control_lock", observe_attempt)
    monkeypatch.setattr(api, "apply_additional_context", save_context)
    monkeypatch.setattr(GameProcessManager, "is_running", lambda self, game_id: running.is_set())
    monkeypatch.setattr(GameProcessManager, "resume_game", start_worker)
    monkeypatch.setattr(api, "_confirm_worker_started", lambda **kwargs: None)

    with client_for(tmp_path) as client, ThreadPoolExecutor(max_workers=2) as pool:

        def request(operation):
            if operation == "context":
                return client.post("/api/games/branch/context", json={"context": {player: "New briefing."}})
            return client.post("/api/games/branch/resume")

        first = pool.submit(request, first_operation)
        try:
            assert first_entered.wait(5)
            second = pool.submit(request, "resume" if first_operation == "context" else "context")
            assert second_attempted.wait(5)
            assert not second.done()
        finally:
            release_first.set()
        assert first.result(timeout=5).status_code == 204
        assert second.result(timeout=5).status_code == (204 if first_operation == "context" else 409)

    assert observed_at_start == (["New briefing."] if first_operation == "context" else [None])
    restored = restore_game(branch)
    assert restored.checkpoint is not None
    assert restored.checkpoint["additional_context"].get(player) == observed_at_start[0]
