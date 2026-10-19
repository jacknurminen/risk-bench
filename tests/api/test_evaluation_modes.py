import pytest

from risk_bench.api import app as api_app
from risk_bench.api.processes import GameProcessManager
from risk_bench.runner.evaluation_config import configure_panel, configured_panel
from risk_bench.runner.evaluation_store import EvaluationWriter, evaluation_path_for_journal
from risk_bench.runner.live_evaluation import _mark_review_active
from tests.api.test_app import client_for
from tests.runner.test_journal import write_game_journal


@pytest.mark.parametrize("count", [1, 2])
@pytest.mark.parametrize("method", ["live_rounds", "whole_game"])
@pytest.mark.parametrize("panel", [[], ["test:coder:high"]])
def test_launch_selects_one_method_for_every_game(tmp_path, monkeypatch, count, method, panel):
    started = []
    monkeypatch.setattr(api_app, "_validate_launch_request", lambda request: None)
    monkeypatch.setattr(api_app, "_wait_for_journal_header", lambda **kwargs: None)
    monkeypatch.setattr(GameProcessManager, "launch_game", lambda self, **kwargs: started.append(kwargs))
    response = client_for(tmp_path).post("/api/games", json={
        "players": ["random"] * 3, "games": count, "parallel_games": count,
        "evaluators": panel, "evaluation_method": method,
    })
    assert response.status_code == 201
    assert len(started) == count
    for game in started:
        assert configured_panel(game["journal_path"], method) == panel
        other = "whole_game" if method == "live_rounds" else "live_rounds"
        assert configured_panel(game["journal_path"], other) == []


def test_postgame_toggle_selects_one_method_and_reports_whole_game_activity(tmp_path, monkeypatch):
    journal = tmp_path / "games" / "finished" / "game.jsonl"
    journal.parent.mkdir(parents=True)
    write_game_journal(journal, seed=19, max_turns=1)
    original = journal.read_bytes()
    configure_panel(journal, "live_rounds", ["test:round:low"])
    started = []
    monkeypatch.setattr(api_app.specs, "build_evaluators", lambda **kwargs: ())
    monkeypatch.setattr(GameProcessManager, "evaluate_game", lambda self, **kwargs: started.append(kwargs))
    client = client_for(tmp_path)
    response = client.post("/api/games/finished/evaluate", json={"evaluators": ["test:whole:high"], "live": False})
    assert response.status_code == 202
    assert started[-1]["live"] is False
    assert configured_panel(journal, "live_rounds") == []
    assert configured_panel(journal, "whole_game") == ["test:whole:high"]
    _mark_review_active(journal, kind="whole_game", panel=["test:whole:high"])
    view = client.get("/api/games/finished/evaluation").json()
    assert view["reviewing_whole_game"] and view["running"]
    assert view["live_evaluation"] is False
    assert view["completion_status"] == "running"
    assert view["configured_panels"] == {"live_rounds": [], "whole_game": ["test:whole:high"]}
    assert journal.read_bytes() == original
    journal.with_suffix(".eval.active.json").unlink()
    writer = EvaluationWriter(evaluation_path_for_journal(journal))
    writer.write_header(["test:whole:high"], 0, 0)
    writer.close()
    view = client.get("/api/games/finished/evaluation").json()
    assert view["evaluation_method"] == "whole_game"
    assert view["live_evaluation"] is False
    assert view["running"] is False

    response = client.post("/api/games/finished/evaluate", json={
        "evaluators": ["test:whole:high"], "live": True, "restart": True,
    })
    assert response.status_code == 202
    assert started[-1]["live"] is True and started[-1]["restart"] is True
    assert configured_panel(journal, "live_rounds") == ["test:whole:high"]
    assert configured_panel(journal, "whole_game") == []
