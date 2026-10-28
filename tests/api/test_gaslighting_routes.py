import json
from pathlib import Path

from risk_bench.api import gaslighting as gaslighting_routes
from risk_bench.api.processes import GameProcessManager
from risk_bench.runner import gaslighting_evaluation
from tests.api.test_app import client_for
from tests.runner.test_journal import PLAYER_NAMES, write_game_journal


def write_gaslighter_game(tmp_path: Path, game_id: str) -> Path:
    journal = tmp_path / "games" / game_id / "game.jsonl"
    journal.parent.mkdir(parents=True)
    write_game_journal(path=journal, seed=19, max_turns=1)
    lines = journal.read_text(encoding="utf-8").splitlines(keepends=True)
    header = json.loads(lines[0])
    header.setdefault("configuration", {})["experiment_player"] = {
        "player_id": PLAYER_NAMES[0],
        "roster_position": 0,
        "id": "gaslighter",
        "label": "Gaslighter",
        "objective": "",
        "instructions": "",
        "sha256": "",
    }
    journal.write_text(json.dumps(header) + "\n" + "".join(lines[1:]), encoding="utf-8")
    return journal


def test_the_reader_offers_the_judge_only_for_gaslighter_games(tmp_path):
    write_gaslighter_game(tmp_path=tmp_path, game_id="gaslit")
    plain = tmp_path / "games" / "plain" / "game.jsonl"
    plain.parent.mkdir(parents=True)
    write_game_journal(path=plain, seed=19, max_turns=1)
    client = client_for(tmp_path=tmp_path)

    assert 'gaslighting: "/api/games/gaslit/gaslighting"' in client.get("/games/gaslit").text
    assert 'gaslighting: ""' in client.get("/games/plain").text
    assert client.get("/api/games/plain/gaslighting").json()["available"] is False
    response = client.post("/api/games/plain/gaslighting", json={"evaluator": "test:judge:low"})
    assert response.status_code == 409


def test_starting_the_judge_runs_a_worker_and_status_serves_the_sidecar(tmp_path, monkeypatch):
    journal = write_gaslighter_game(tmp_path=tmp_path, game_id="gaslit")
    gaslighting_evaluation.gaslighting_error_path_for_journal(journal_path=journal).write_text(
        json.dumps({"detail": "old failure"}), encoding="utf-8"
    )
    started = []
    monkeypatch.setattr(gaslighting_routes.specs, "build_agent", lambda **kwargs: None)
    monkeypatch.setattr(GameProcessManager, "judge_gaslighting", lambda self, **kwargs: started.append(kwargs))
    client = client_for(tmp_path=tmp_path)
    assert client.get("/api/games/gaslit/gaslighting").json()["error"] == "old failure"

    response = client.post("/api/games/gaslit/gaslighting", json={"evaluator": "test:judge:low"})

    assert response.status_code == 202
    assert started == [{"game_id": "gaslit", "journal_path": journal, "evaluator_spec": "test:judge:low"}]
    gaslighting_evaluation.gaslighting_path_for_journal(journal_path=journal).write_text(
        json.dumps({"claims": [], "by_target": []}), encoding="utf-8"
    )
    status = client.get("/api/games/gaslit/gaslighting").json()
    assert status == {
        "available": True,
        "running": False,
        "error": None,
        "assessment": {"claims": [], "by_target": []},
    }
