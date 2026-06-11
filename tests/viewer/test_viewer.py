import json
import threading
import urllib.request
from pathlib import Path

from risk_bench.game.types import Territory
from risk_bench.runner.journal import SCHEMA_VERSION, JournalHeader, JournalWriter
from risk_bench.viewer.html import render_viewer_html
from risk_bench.viewer.server import create_viewer_server
from risk_bench.viewer.snapshots import build_view_model
from tests.runner.test_journal import PLAYER_NAMES, write_game_journal


def journal_with_game(tmp_path: Path) -> Path:
    path = tmp_path / "game.jsonl"
    write_game_journal(path=path, seed=11, max_turns=2_000)
    return path


def test_view_model_has_one_snapshot_per_action_plus_the_start(tmp_path: Path):
    path = journal_with_game(tmp_path=tmp_path)
    model = build_view_model(journal_path=path)

    action_count = sum(1 for line in path.read_text().splitlines() if '"type": "action"' in line)
    assert len(model["steps"]) == action_count + 1
    assert model["steps"][0]["action"] is None
    assert all(step["action"] for step in model["steps"][1:])


def test_view_model_snapshots_track_the_board_and_outcome(tmp_path: Path):
    model = build_view_model(journal_path=journal_with_game(tmp_path=tmp_path))

    first_board = model["steps"][0]["board"]
    assert len(first_board) == 42
    assert all(armies == 1 for _, armies in first_board.values())
    assert all(player["income"] >= 3 for step in model["steps"] for player in step["players"])

    assert model["result"] is not None
    assert model["result"]["winner"] in PLAYER_NAMES
    assert [player["name"] for player in model["players"]] == PLAYER_NAMES
    assert all(player["mission"] for player in model["players"])
    assert model["thoughts"] == []  # random players never think out loud
    json.dumps(model)  # must be embeddable in the HTML


def test_commentary_records_appear_in_the_view_model(tmp_path: Path):
    path = journal_with_game(tmp_path=tmp_path)
    commentary_line = json.dumps({"type": "commentary", "turn": 1, "text": "A cagey opening.", "tags": []})
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(lines[:2] + [commentary_line] + lines[2:]) + "\n", encoding="utf-8")

    model = build_view_model(journal_path=path)

    assert len(model["commentary"]) == 1
    assert model["commentary"][0]["text"] == "A cagey opening."
    assert model["commentary"][0]["step"] >= 0


def test_rendered_html_is_self_contained(tmp_path: Path):
    model = build_view_model(journal_path=journal_with_game(tmp_path=tmp_path))

    html = render_viewer_html(view_model=model, live=False)

    assert html.startswith("<!DOCTYPE html>")
    assert "const GAME =" in html
    assert "const LIVE = false" in html
    assert model["players"][0]["name"] in html
    assert "<script src=" not in html  # no external dependencies


def test_rendered_html_embeds_the_board_map_with_every_territory(tmp_path: Path):
    html = render_viewer_html(
        view_model=build_view_model(journal_path=journal_with_game(tmp_path=tmp_path)), live=False
    )

    assert "<svg" in html
    assert all(f'id="{territory}"' in html for territory in Territory)
    assert "CC BY-SA 3.0" in html  # the map's attribution requirement


def test_live_rendering_enables_journal_polling(tmp_path: Path):
    html = render_viewer_html(
        view_model=build_view_model(journal_path=journal_with_game(tmp_path=tmp_path)), live=True
    )

    assert "const LIVE = true" in html
    assert "game.json" in html


def test_a_header_only_journal_builds_a_startable_view_model(tmp_path: Path):
    # following can start before the first action: the journal then holds only the header
    path = tmp_path / "fresh.jsonl"
    journal = JournalWriter(path=path, append=False)
    journal.write_header(
        header=JournalHeader(
            schema_version=SCHEMA_VERSION,
            seed=5,
            player_specs={name: "random" for name in PLAYER_NAMES},
            max_turns=100,
            allow_private_messages=False,
            max_negotiation_rounds=3,
        )
    )
    journal.close()

    model = build_view_model(journal_path=path)

    assert len(model["steps"]) == 1
    assert model["result"] is None


def fetch(port: int, path: str) -> bytes:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}") as response:
        return response.read()


def test_the_live_server_serves_the_page_and_the_growing_game(tmp_path: Path):
    finished_path = tmp_path / "finished.jsonl"
    write_game_journal(path=finished_path, seed=11, max_turns=2_000)
    finished_lines = finished_path.read_text(encoding="utf-8").splitlines()

    # serve a journal frozen mid-game, as if the game were still running
    live_path = tmp_path / "live.jsonl"
    live_path.write_text("\n".join(finished_lines[: len(finished_lines) // 2]) + "\n", encoding="utf-8")
    server = create_viewer_server(journal_path=live_path, host="127.0.0.1", port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    try:
        page = fetch(port=port, path="/")
        assert b"const LIVE = true" in page and b"<svg" in page

        mid_game = json.loads(fetch(port=port, path="/game.json"))
        assert mid_game["result"] is None

        # the game progresses to its end; the next poll must pick it up
        live_path.write_text("\n".join(finished_lines) + "\n", encoding="utf-8")
        final = json.loads(fetch(port=port, path="/game.json"))
        assert len(final["steps"]) > len(mid_game["steps"])
        # append-only journal: the prefix is stable, except the last mid-game step,
        # which may gain its events (the journal writes them on a separate line)
        assert final["steps"][: len(mid_game["steps"]) - 1] == mid_game["steps"][:-1]
        assert final["result"] is not None
    finally:
        server.shutdown()
        server.server_close()
