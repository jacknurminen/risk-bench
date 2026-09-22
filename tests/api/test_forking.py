from risk_bench.config import Settings
from risk_bench.launch import run_new_game
from risk_bench.players.human import mailbox_for
from risk_bench.runner.journal import raw_journal, restore_game
from tests.api.test_app import client_for
from tests.api.test_human import wait_for


def test_historical_api_fork_runs_independently_and_exposes_cursors(tmp_path):
    source = tmp_path / "games" / "parent" / "game.jsonl"
    run_new_game(["random"] * 3, 19, Settings(), [], source, 1, False)
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
