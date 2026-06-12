import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

from risk_bench.api.app import create_app
from tests.runner.test_journal import PLAYER_NAMES, write_game_journal


def client_for(tmp_path: Path) -> TestClient:
    return TestClient(create_app(games_directory=tmp_path / "games"))


def client_with_finished_game(tmp_path: Path) -> tuple[TestClient, str]:
    games_directory = tmp_path / "games"
    games_directory.mkdir()
    write_game_journal(path=games_directory / "first-game.jsonl", seed=11, max_turns=2_000)
    return client_for(tmp_path=tmp_path), "first-game"


def test_an_empty_games_directory_lists_no_games(tmp_path: Path):
    response = client_for(tmp_path=tmp_path).get("/api/games")

    assert response.status_code == 200
    assert response.json() == {"games": []}


def test_finished_games_are_listed_with_their_outcome(tmp_path: Path):
    client, game_id = client_with_finished_game(tmp_path=tmp_path)

    [game] = client.get("/api/games").json()["games"]

    assert game["id"] == game_id
    assert game["players"] == PLAYER_NAMES
    assert game["finished"] is True
    assert game["winner"] in PLAYER_NAMES
    assert game["turn_count"] >= 1
    assert game["has_commentary"] is False


def test_the_game_view_model_is_served_as_json(tmp_path: Path):
    client, game_id = client_with_finished_game(tmp_path=tmp_path)

    model = client.get(f"/api/games/{game_id}").json()

    assert len(model["steps"]) > 1
    assert model["result"]["winner"] in PLAYER_NAMES


def test_the_game_page_renders_the_viewer(tmp_path: Path):
    client, game_id = client_with_finished_game(tmp_path=tmp_path)

    page = client.get(f"/games/{game_id}")

    assert page.status_code == 200
    assert "<svg" in page.text
    assert "const LIVE = false" in page.text  # the game is finished: no polling


def test_an_unfinished_game_page_polls_the_api(tmp_path: Path):
    games_directory = tmp_path / "games"
    games_directory.mkdir()
    path = games_directory / "running.jsonl"
    write_game_journal(path=path, seed=11, max_turns=2_000)
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")  # drop the result line

    page = client_for(tmp_path=tmp_path).get("/games/running")

    assert "const LIVE = true" in page.text
    assert '"/api/games/running"' in page.text


def test_unknown_and_malformed_game_ids_are_rejected(tmp_path: Path):
    client = client_for(tmp_path=tmp_path)

    assert client.get("/api/games/no-such-game").status_code == 404
    assert client.get("/api/games/..%2Fescape").status_code == 404


def test_the_index_page_is_served_at_the_root(tmp_path: Path):
    response = client_for(tmp_path=tmp_path).get("/")

    assert response.status_code == 200
    assert "risk-bench" in response.text


def test_the_model_catalog_offers_tiers_and_the_baseline(tmp_path: Path):
    catalog = client_for(tmp_path=tmp_path).get("/api/models").json()

    assert catalog["baseline"] == "random"
    assert catalog["tiers_by_provider"]["anthropic"]["lo"] == "claude-haiku-4-5"
    assert set(catalog["tiers_by_provider"]) == {"anthropic", "google", "openai"}


def test_launch_requests_are_validated(tmp_path: Path):
    client = client_for(tmp_path=tmp_path)

    too_few = client.post("/api/games", json={"players": ["random", "random"]})
    assert too_few.status_code == 400
    assert "3-6" in too_few.json()["detail"]

    bad_provider = client.post("/api/games", json={"players": ["random", "random", "frontier:hi"]})
    assert bad_provider.status_code == 400
    assert "Unknown provider" in bad_provider.json()["detail"]


def unfinished_journal(games_directory: Path, name: str) -> Path:
    games_directory.mkdir(parents=True, exist_ok=True)
    path = games_directory / f"{name}.jsonl"
    write_game_journal(path=path, seed=11, max_turns=2_000)
    # cut the journal mid-game, as if the game had been paused halfway
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(lines[: len(lines) // 2]) + "\n", encoding="utf-8")
    return path


def test_pausing_a_running_game_terminates_its_worker(tmp_path: Path):
    client = client_for(tmp_path=tmp_path)
    # six random players and an unreachable turn limit keep the game running long enough to pause
    response = client.post("/api/games", json={"players": ["random"] * 6, "seed": 5, "max_turns": 1_000_000})
    assert response.status_code == 201
    game_id = response.json()["id"]

    [game] = client.get("/api/games").json()["games"]
    assert game["running"] is True and game["finished"] is False

    assert client.post(f"/api/games/{game_id}/pause").status_code == 204

    [game] = client.get("/api/games").json()["games"]
    assert game["running"] is False and game["finished"] is False  # paused, resumable


def test_pausing_a_game_that_is_not_running_is_rejected(tmp_path: Path):
    client, game_id = client_with_finished_game(tmp_path=tmp_path)

    response = client.post(f"/api/games/{game_id}/pause")

    assert response.status_code == 409
    assert "not running" in response.json()["detail"]


def test_resuming_a_paused_game_plays_it_to_completion(tmp_path: Path):
    unfinished_journal(games_directory=tmp_path / "games", name="paused")
    client = client_for(tmp_path=tmp_path)

    assert client.post("/api/games/paused/resume").status_code == 204

    deadline = time.monotonic() + 90
    finished = False
    while time.monotonic() < deadline and not finished:
        time.sleep(0.5)
        [game] = client.get("/api/games").json()["games"]
        finished = game["finished"]
    assert finished, "the resumed game never finished"
    assert game["winner"] in PLAYER_NAMES


def test_resuming_a_finished_game_is_rejected(tmp_path: Path):
    client, game_id = client_with_finished_game(tmp_path=tmp_path)

    response = client.post(f"/api/games/{game_id}/resume")

    assert response.status_code == 409
    assert "finished" in response.json()["detail"]


def test_trace_endpoints_serve_summaries_and_full_records(tmp_path: Path):
    client, game_id = client_with_finished_game(tmp_path=tmp_path)
    trace_record = {
        "seq": 1,
        "time": "2026-06-12T10:00:00+00:00",
        "turn": 2,
        "kind": "decision",
        "caller": PLAYER_NAMES[0],
        "model": "claude-sonnet-4-6",
        "messages": [{"role": "system", "content": "rules"}, {"role": "user", "content": "a very long prompt"}],
        "tools": [{"name": "attack", "description": "Attack.", "parameters": {"type": "object"}}],
        "response": {"text": None, "tool_calls": [{"id": "c1", "name": "attack", "arguments": {}}]},
        "error": None,
        "input_tokens": 2_341,
        "output_tokens": 89,
        "latency_ms": 1_200,
    }
    trace_path = tmp_path / "games" / f"{game_id}.trace.jsonl"
    trace_path.write_text(json.dumps(trace_record) + "\n", encoding="utf-8")

    [summary] = client.get(f"/api/games/{game_id}/trace").json()["calls"]
    assert summary["seq"] == 1
    assert summary["caller"] == PLAYER_NAMES[0]
    assert summary["tool_call_names"] == ["attack"]
    assert summary["message_count"] == 2
    assert "messages" not in summary  # summaries stay light; bodies come from the detail endpoint

    detail = client.get(f"/api/games/{game_id}/trace/1").json()
    assert detail["messages"][1]["content"] == "a very long prompt"
    assert detail["tools"][0]["name"] == "attack"

    assert client.get(f"/api/games/{game_id}/trace/99").status_code == 404


def test_a_game_without_a_trace_lists_no_calls(tmp_path: Path):
    client, game_id = client_with_finished_game(tmp_path=tmp_path)

    assert client.get(f"/api/games/{game_id}/trace").json() == {"calls": []}


def test_launching_a_game_spawns_a_process_that_plays_it_to_completion(tmp_path: Path):
    client = client_for(tmp_path=tmp_path)

    response = client.post("/api/games", json={"players": ["random", "random", "random"], "seed": 3, "max_turns": 40})
    assert response.status_code == 201
    game_id = response.json()["id"]

    deadline = time.monotonic() + 90
    result = None
    while time.monotonic() < deadline and result is None:
        time.sleep(0.5)
        result = client.get(f"/api/games/{game_id}").json()["result"]
    assert result is not None, "the launched game never finished"
    assert result["turn_count"] >= 1

    [game] = client.get("/api/games").json()["games"]
    assert game["id"] == game_id and game["finished"] is True
