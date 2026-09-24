import json

import pytest
from fastapi.testclient import TestClient

from risk_bench.api.app import create_app
from risk_bench.runner.forking import fork_game
from risk_bench.runner.journal import JournalWriter, restore_game


def test_copied_v2_games_cannot_be_read_resumed_forked_or_appended(tmp_path):
    journal = tmp_path / "old" / "game.jsonl"
    journal.parent.mkdir()
    original = (
        json.dumps(
            {"type": "header", "schema_version": 4, "player_specs": {"a": "random", "b": "random", "c": "random"}}
        )
        + "\n"
    )
    journal.write_text(original)
    client = TestClient(create_app(games_directory=tmp_path))
    assert client.get("/api/games").json()["games"] == []
    for method, url, body in (
        ("get", "/games/old", None),
        ("get", "/api/games/old", None),
        ("post", "/api/games/old/resume", None),
        ("post", "/api/games/old/fork", {"cursor": "0:after"}),
    ):
        response = client.request(method, url, json=body)
        assert response.status_code == 409
        assert "v2 Git tag" in response.json()["detail"]
    for operation in (
        lambda: restore_game(journal),
        lambda: JournalWriter(journal, True),
        lambda: fork_game(journal, tmp_path / "branch" / "game.jsonl", "0:after"),
    ):
        with pytest.raises(ValueError, match="v2 Git tag"):
            operation()
    assert journal.read_text() == original
    assert not (tmp_path / "branch").exists()
