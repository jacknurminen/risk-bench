import json

import pytest

from risk_bench.api import app as api_app
from risk_bench.players.profiles import load_profile
from tests.api.test_tournament_continue import server as shared_server

server = shared_server
MODEL = "google:gemini-4-argon:medium"


def test_catalog_exposes_only_the_two_enabled_profiles(server):
    client, _ = server
    response = client.get("/api/experiment-profiles")
    assert response.status_code == 200
    profiles = response.json()
    assert [profile["id"] for profile in profiles] == ["mission_prober", "deceiver"]
    for profile in profiles:
        assert "EXPERIMENT PLAYER" in profile["instructions"]
        assert len(profile["sha256"]) == 64
        assert profile["objective"]


@pytest.mark.parametrize(
    "selection",
    [
        {"player": 4, "profile": "mission_prober"},
        {"player": 1, "profile": "deceiver"},
        {"player": 3, "profile": "locksmith"},
        {"player": 0, "profile": "mission_prober"},
        {"player": True, "profile": "mission_prober"},
    ],
)
def test_invalid_experiment_selection_never_starts_a_worker(server, selection):
    client, manager = server
    response = client.post("/api/games", json={"players": ["random", "random", MODEL], "experiment_player": selection})
    assert response.status_code == 422
    assert not manager._context.started


@pytest.mark.parametrize("profile_id", ["mission_prober", "deceiver"])
def test_tournament_continue_and_replay_keep_the_frozen_profile(server, tmp_path, monkeypatch, profile_id):
    client, manager = server
    monkeypatch.setattr(api_app.specs, "build_agent", lambda **kwargs: None)
    response = client.post(
        "/api/games",
        json={
            "players": ["random", "random", MODEL],
            "games": 2,
            "parallel_games": 1,
            "seating_mode": "cycle",
            "experiment_player": {"player": 3, "profile": profile_id},
        },
    )
    assert response.status_code == 201, response.text
    directory = tmp_path / response.json()["id"]
    manifest = json.loads((directory / "tournament.json").read_text())
    saved = manifest["launch_config"]["experiment_player"]
    assert saved["snapshot"] == load_profile(profile_id=profile_id).model_dump()
    assert manager._context.started[0].arguments[-2].model_dump() == saved
    for game_id in manifest["game_ids"]:
        manager.pause_game(game_id)
        assert json.loads((directory / game_id / "launch.json").read_text())["experiment_player"] == saved
    # A later change to profile files must not alter continuation or replacement attempts.
    monkeypatch.setattr(api_app, "load_profile", lambda **kwargs: pytest.fail("Reloaded a frozen profile"))
    extended = client.post(f"/api/tournaments/{directory.name}/continue", json={"games": 1, "expected_total_games": 2})
    assert extended.status_code == 202, extended.text
    new_id = extended.json()["game_ids"][0]
    manager.pause_game(new_id)
    assert json.loads((directory / new_id / "launch.json").read_text())["experiment_player"] == saved
    replayed = client.post(f"/api/tournaments/{directory.name}/games/{manifest['game_ids'][0]}/replay")
    assert replayed.status_code == 202, replayed.text
    replay = json.loads((directory / replayed.json()["id"] / "launch.json").read_text())
    assert replay["experiment_player"] == saved
    assert replay["seating_orders"] == [[1, 2, 3]]
