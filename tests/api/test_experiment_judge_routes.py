import json
from pathlib import Path

import pytest

from risk_bench.api import experiment_judge as experiment_judge_routes
from risk_bench.api.processes import GameProcessManager
from risk_bench.runner.judges import core as judge_core
from risk_bench.runner.judges import registry as judge_registry
from tests.api.test_app import client_for
from tests.runner.test_journal import PLAYER_NAMES, write_game_journal


def write_experiment_game(tmp_path: Path, game_id: str, profile_id: str) -> Path:
    journal = tmp_path / "games" / game_id / "game.jsonl"
    journal.parent.mkdir(parents=True)
    write_game_journal(path=journal, seed=19, max_turns=1)
    lines = journal.read_text(encoding="utf-8").splitlines(keepends=True)
    header = json.loads(lines[0])
    header.setdefault("configuration", {})["experiment_player"] = {
        "player_id": PLAYER_NAMES[0],
        "roster_position": 0,
        "id": profile_id,
        "label": profile_id,
        "objective": "",
        "instructions": "",
        "sha256": "",
    }
    journal.write_text(json.dumps(header) + "\n" + "".join(lines[1:]), encoding="utf-8")
    return journal


def test_the_reader_offers_the_judge_only_for_experiment_games(tmp_path):
    write_experiment_game(tmp_path=tmp_path, game_id="probed", profile_id="mission_prober")
    plain = tmp_path / "games" / "plain" / "game.jsonl"
    plain.parent.mkdir(parents=True)
    write_game_journal(path=plain, seed=19, max_turns=1)
    client = client_for(tmp_path=tmp_path)

    assert 'experiment_judge: "/api/games/probed/experiment-judge"' in client.get("/games/probed").text
    assert 'experiment_judge: ""' in client.get("/games/plain").text
    assert client.get("/api/games/plain/experiment-judge").json()["available"] is False
    response = client.post("/api/games/plain/experiment-judge", json={"evaluator": "test:judge:low"})
    assert response.status_code == 409


@pytest.mark.parametrize("profile_id", ["mission_prober", "deceiver", "gaslighter"])
def test_starting_the_judge_runs_a_worker_and_status_serves_the_sidecar(tmp_path, monkeypatch, profile_id):
    journal = write_experiment_game(tmp_path=tmp_path, game_id="experiment", profile_id=profile_id)
    judge = judge_registry.judge_for_profile(profile_id=profile_id)
    assert judge is not None
    judge_core.error_path_for_journal(journal_path=journal, definition=judge).write_text(
        json.dumps({"detail": "old failure"}), encoding="utf-8"
    )
    started = []
    monkeypatch.setattr(experiment_judge_routes.specs, "build_agent", lambda **kwargs: None)
    monkeypatch.setattr(GameProcessManager, "run_experiment_judge", lambda self, **kwargs: started.append(kwargs))
    client = client_for(tmp_path=tmp_path)
    assert client.get("/api/games/experiment/experiment-judge").json()["error"] == "old failure"

    response = client.post("/api/games/experiment/experiment-judge", json={"evaluator": "test:judge:low"})

    assert response.status_code == 202
    assert started == [{"game_id": "experiment", "journal_path": journal, "evaluator_spec": "test:judge:low"}]
    judge_core.sidecar_path_for_journal(journal_path=journal, definition=judge).write_text(
        json.dumps({"tables": []}), encoding="utf-8"
    )
    status = client.get("/api/games/experiment/experiment-judge").json()
    assert status == {
        "available": True,
        "judge": {"name": judge.name, "title": judge.title},
        "running": False,
        "error": None,
        "assessment": {"tables": []},
    }
