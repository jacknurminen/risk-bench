import json
from pathlib import Path

import pytest

from risk_bench.game.types import Territory
from risk_bench.runner.journal import SCHEMA_VERSION, JournalHeader, JournalWriter
from risk_bench.viewer.html import render_live_viewer_html, render_viewer_html
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
    tags = [
        {"player": PLAYER_NAMES[0], "behavior": "turtling", "note": "hoarding on one border"},
        {"player": PLAYER_NAMES[0], "behavior": "turtling", "note": "still hoarding"},
        {"player": "someone-not-in-the-game", "behavior": "betrayal", "note": "an invented player"},
    ]
    commentary_line = json.dumps({"type": "commentary", "turn": 1, "text": "A cagey opening.", "tags": tags})
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(lines[:2] + [commentary_line] + lines[2:]) + "\n", encoding="utf-8")

    model = build_view_model(journal_path=path)

    assert len(model["commentary"]) == 1
    assert model["commentary"][0]["text"] == "A cagey opening."
    assert model["commentary"][0]["step"] >= 0
    # tags roll up into the per-player stats; tags for invented player names are dropped
    assert model["stats"]["per_player"][0]["tags"] == {"turtling": 2}
    assert all(not player_stats["tags"] for player_stats in model["stats"]["per_player"][1:])


def test_view_model_stats_summarize_the_game(tmp_path: Path):
    model = build_view_model(journal_path=journal_with_game(tmp_path=tmp_path))

    series = model["stats"]["series"]
    turn_count = len(series["turns"])
    assert series["turns"] == sorted({step["turn"] for step in model["steps"]})  # one sample per played turn
    assert series["turns"][-1] == model["steps"][-1]["turn"]
    for player_series in series["players"]:
        assert all(len(values) == turn_count for values in player_series.values())
    for turn_index in range(turn_count):
        total_share = sum(player_series["army_share"][turn_index] for player_series in series["players"])
        assert abs(total_share - 1.0) < 1e-3

    per_player = model["stats"]["per_player"]
    assert len(per_player) == len(PLAYER_NAMES)
    assert sum(player_stats["attacks"] for player_stats in per_player) > 0
    assert sum(player_stats["conquests"] for player_stats in per_player) > 0
    destroyed = sum(player_stats["armies_destroyed"] for player_stats in per_player)
    lost = sum(player_stats["armies_lost"] for player_stats in per_player)
    assert destroyed == lost > 0  # every destroyed army is some other player's loss

    assert all(marker["kind"] in {"elimination", "continent", "trade"} for marker in model["stats"]["markers"])
    assert all(marker["text"] and marker["turn"] in series["turns"] for marker in model["stats"]["markers"])


def test_usage_and_cost_aggregate_from_the_trace_sidecar(tmp_path: Path):
    path = journal_with_game(tmp_path=tmp_path)
    trace_records = [
        {
            "seq": 1,
            "caller": PLAYER_NAMES[0],
            "model": "claude-sonnet-4-6",
            "input_tokens": 1_000,
            "output_tokens": 100,
            "error": None,
        },
        {
            "seq": 2,
            "caller": PLAYER_NAMES[0],
            "model": "claude-sonnet-4-6",
            "input_tokens": 2_000,
            "output_tokens": 200,
            "error": None,
        },
        {
            "seq": 3,
            "caller": "commentator",
            "model": "a-mystery-model",
            "input_tokens": None,
            "output_tokens": None,
            "error": "RuntimeError: boom",
        },
    ]
    trace_text = "".join(json.dumps(record) + "\n" for record in trace_records)
    (tmp_path / "game.trace.jsonl").write_text(trace_text, encoding="utf-8")

    usage = build_view_model(journal_path=path)["stats"]["usage"]

    player_usage = usage[PLAYER_NAMES[0]]
    assert (player_usage["calls"], player_usage["errors"]) == (2, 0)
    assert (player_usage["input_tokens"], player_usage["output_tokens"]) == (3_000, 300)
    assert player_usage["cost_usd"] == pytest.approx((3_000 * 3.00 + 300 * 15.00) / 1_000_000)
    assert usage["commentator"]["errors"] == 1
    assert usage["commentator"]["cost_usd"] is None  # unknown model: no price invented


def test_a_game_without_a_trace_reports_no_usage(tmp_path: Path):
    model = build_view_model(journal_path=journal_with_game(tmp_path=tmp_path))

    assert model["stats"]["usage"] == {}


def test_rendered_html_is_self_contained(tmp_path: Path):
    model = build_view_model(journal_path=journal_with_game(tmp_path=tmp_path))

    html = render_viewer_html(view_model=model, trace_url="")

    assert html.startswith("<!DOCTYPE html>")
    assert "const GAME =" in html
    assert "const LIVE = false" in html
    assert model["players"][0]["name"] in html
    assert "<script src=" not in html  # no external dependencies


def test_rendered_html_embeds_the_board_map_with_every_territory(tmp_path: Path):
    html = render_viewer_html(
        view_model=build_view_model(journal_path=journal_with_game(tmp_path=tmp_path)), trace_url=""
    )

    assert "<svg" in html
    assert all(f'id="{territory}"' in html for territory in Territory)
    assert "CC BY-SA 3.0" in html  # the map's attribution requirement


def test_live_rendering_polls_the_given_url(tmp_path: Path):
    model = build_view_model(journal_path=journal_with_game(tmp_path=tmp_path))

    html = render_live_viewer_html(
        view_model=model, poll_url="/api/games/some-game", trace_url="/api/games/some-game/trace"
    )

    assert "const LIVE = true" in html
    assert '"/api/games/some-game"' in html


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
