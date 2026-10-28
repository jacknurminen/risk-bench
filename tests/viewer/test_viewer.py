import json
from collections import Counter
from pathlib import Path

import pytest

from risk_bench.game.types import Territory
from risk_bench.runner.evaluation_versions import version_for
from risk_bench.runner.journal import SCHEMA_VERSION, JournalHeader, JournalWriter, read_journal, restore_game
from risk_bench.viewer.evaluation import build_evaluation_view_model
from risk_bench.viewer.html import render_reader_export_html, render_reader_html
from risk_bench.viewer.snapshots import build_view_model
from tests.runner.test_journal import LEGACY_GAME_HEADERS, PLAYER_NAMES, write_game_journal


def journal_with_game(tmp_path: Path) -> Path:
    path = tmp_path / "game.jsonl"
    write_game_journal(path=path, seed=11, max_turns=2_000)
    return path


@pytest.mark.parametrize("game_id", ["20260909-123257-seed1", "20260910-092103-seed7", "20260911-071339-seed8"])
def test_schema_three_reference_headers_require_the_v2_application(game_id: str, tmp_path: Path):
    path = tmp_path / "game.jsonl"
    path.write_text(json.dumps(LEGACY_GAME_HEADERS[game_id]) + "\n", encoding="utf-8")
    for read in (read_journal, build_view_model, restore_game):
        with pytest.raises(ValueError, match="v2 Git tag"):
            read(path)


def test_view_model_has_one_snapshot_per_action_plus_the_start(tmp_path: Path):
    path = journal_with_game(tmp_path=tmp_path)
    model = build_view_model(journal_path=path)

    action_count = sum(record["type"] == "action" for record in read_journal(path))
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
    assert all(player["experiment_profile"] is None for player in model["players"])
    assert model["thoughts"] == []  # random players never think out loud
    json.dumps(model)  # must be embeddable in the HTML


def test_human_hands_follow_draws_and_trades_in_historical_snapshots(tmp_path: Path):
    path = journal_with_game(tmp_path=tmp_path)
    records = [json.loads(line) for line in path.read_text().splitlines()]
    human = PLAYER_NAMES[0]
    # Keep the real played actions; mark the seat as human to exercise viewer identity.
    records[0]["player_specs"][human] = "human"
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n")
    model = build_view_model(journal_path=path)
    assert model["human_players"] == [human]
    assert model["steps"][0]["hands"][human] == []
    for step in model["steps"]:
        assert set(step["hands"]) == {human}
        assert len(step["hands"][human]) == step["players"][0]["cards"]

    def card_counts(cards):
        return Counter((card["design"], card["territory"]) for card in cards)

    step_index = 0
    draws = trades = 0
    for record in read_journal(path):
        if record["type"] == "action":
            step_index += 1
            if record["player"] == human and record["action"]["kind"] == "trade_cards":
                before = model["steps"][step_index - 1]["hands"][human]
                after = model["steps"][step_index]["hands"][human]
                assert card_counts(after) == card_counts(before) - card_counts(record["action"]["cards"])
                trades += 1
        elif record["type"] == "events":
            if any(event["kind"] == "card_drawn" and event["player_id"] == human for event in record["events"]):
                before = model["steps"][step_index - 1]["hands"][human]
                after = model["steps"][step_index]["hands"][human]
                assert len(after) == len(before) + 1
                assert card_counts(before) <= card_counts(after)
                draws += 1
    assert draws > 0 and trades > 0


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
    # tags roll up into the per-player stats as distinct (turn, behavior) instances — the same behavior
    # tagged twice for one player-turn counts once; tags for invented player names are dropped
    assert model["stats"]["per_player"][0]["tags"] == {"turtling": 1}
    assert all(not player_stats["tags"] for player_stats in model["stats"]["per_player"][1:])


def _journal_with_panel(tmp_path: Path, commentators: list[str], commentary: list[dict]) -> Path:
    """A played game whose header carries a commentator panel, with the given commentary records spliced in."""
    path = journal_with_game(tmp_path=tmp_path)
    lines = path.read_text(encoding="utf-8").splitlines()
    header = json.loads(lines[0])
    header["commentators"] = commentators
    comment_lines = [json.dumps({"type": "commentary", **comment}) for comment in commentary]
    rewritten = [json.dumps(header)] + lines[1:-1] + comment_lines + lines[-1:]
    path.write_text("\n".join(rewritten) + "\n", encoding="utf-8")
    return path


def test_the_panel_roster_resolves_member_labels_in_order(tmp_path: Path):
    path = _journal_with_panel(
        tmp_path=tmp_path, commentators=["anthropic:claude-opus-5", "google:gemini-3.1-pro-preview"], commentary=[]
    )

    roster = build_view_model(journal_path=path)["commentators"]

    assert [member["index"] for member in roster] == [0, 1]
    assert [member["spec"] for member in roster] == ["anthropic:claude-opus-5", "google:gemini-3.1-pro-preview"]
    assert [member["label"] for member in roster] == ["claude-opus-5", "gemini-3.1-pro-preview"]


def test_every_members_tags_are_kept_with_a_consensus_series(tmp_path: Path):
    # both members flag the strong move; only member 0 flags deception — a split the panel must preserve
    commentary = [
        {
            "turn": 1,
            "commentator": "anthropic:claude-opus-5",
            "commentator_index": 0,
            "text": "anthropic's read.",
            "tags": [
                {"player": PLAYER_NAMES[0], "behavior": "strong_move", "note": "good odds"},
                {"player": PLAYER_NAMES[0], "behavior": "deception", "note": "said one thing, did another"},
            ],
        },
        {
            "turn": 1,
            "commentator": "google:gemini-3.1-pro-preview",
            "commentator_index": 1,
            "text": "google's read.",
            "tags": [{"player": PLAYER_NAMES[0], "behavior": "strong_move", "note": "agreed: strong"}],
        },
    ]
    path = _journal_with_panel(
        tmp_path=tmp_path,
        commentators=["anthropic:claude-opus-5", "google:gemini-3.1-pro-preview"],
        commentary=commentary,
    )

    model = build_view_model(journal_path=path)

    # each member's records survive separately, labelled by member
    assert {comment["commentator_index"] for comment in model["commentary"]} == {0, 1}
    assert {comment["commentator"] for comment in model["commentary"]} == {"claude-opus-5", "gemini-3.1-pro-preview"}

    # tags count distinct (turn, behavior) instances, not per-member observations: both members flag the
    # same strong move on turn 1, so it counts once. The per-member and agreement breakdowns are derived
    # in the viewer from the raw commentary (each entry keeps its commentator_index), not precomputed here
    first = model["stats"]["per_player"][0]
    assert first["tags"] == {"strong_move": 1, "deception": 1}
    assert "tags_by_commentator" not in first

    # the flat per-valence key is the panel consensus (mean across members); the per-member spread
    # and agreement filter are derived in the viewer from the raw per-member commentary tags
    series = model["stats"]["series"]
    turn_1 = series["turns"].index(1)
    player_series = series["players"][0]
    assert player_series["skill_strong"][turn_1] == 1.0  # both members: mean(1, 1)
    assert player_series["conduct_deceptive"][turn_1] == 0.5  # split: mean(1, 0)


def test_legacy_single_commentator_journal_folds_into_one_anonymous_member(tmp_path: Path):
    # a journal predating the panel: no header roster, records without a member index
    commentary = [
        {
            "turn": 1,
            "text": "A cagey opening.",
            "tags": [
                {"player": PLAYER_NAMES[0], "behavior": "turtling", "note": "hoarding"},
            ],
        }
    ]
    path = journal_with_game(tmp_path=tmp_path)
    lines = path.read_text(encoding="utf-8").splitlines()
    comment_line = json.dumps({"type": "commentary", **commentary[0]})
    path.write_text("\n".join(lines[:-1] + [comment_line] + lines[-1:]) + "\n", encoding="utf-8")

    model = build_view_model(journal_path=path)

    assert model["commentators"] == [{"index": 0, "spec": None, "label": "commentator"}]
    assert model["commentary"][0]["commentator_index"] == 0
    assert model["commentary"][0]["commentator"] == "commentator"
    assert model["stats"]["per_player"][0]["tags"] == {"turtling": 1}


def test_view_model_stats_summarize_the_game(tmp_path: Path):
    model = build_view_model(journal_path=journal_with_game(tmp_path=tmp_path))

    series = model["stats"]["series"]
    turn_count = len(series["turns"])
    assert series["turns"] == sorted({step["turn"] for step in model["steps"]})  # one sample per played turn
    assert series["turns"][-1] == model["steps"][-1]["turn"]
    for player_series in series["players"]:
        per_turn_arrays = [values for values in player_series.values() if isinstance(values, list)]
        assert all(len(values) == turn_count for values in per_turn_arrays)
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


def test_tags_accumulate_into_per_axis_cumulative_series(tmp_path: Path):
    path = journal_with_game(tmp_path=tmp_path)
    comments = [
        {
            "turn": 1,
            "rubric": "skill",
            "text": "Sharp start.",
            "tags": [
                {"player": PLAYER_NAMES[0], "behavior": "strong_move", "note": "good odds"},
                {"player": PLAYER_NAMES[0], "behavior": "taunting", "note": "affect: not scored"},
                {"player": PLAYER_NAMES[0], "behavior": "deception", "note": "said one thing, did another"},
                {"player": PLAYER_NAMES[1], "behavior": "strategic_blunder", "note": "bad odds"},
                {"player": PLAYER_NAMES[1], "behavior": "betrayal", "note": "turned on an ally"},
            ],
        },
        {
            "turn": 2,
            "rubric": "skill",
            "text": "Still sharp.",
            "tags": [
                {"player": PLAYER_NAMES[0], "behavior": "continent_prioritization", "note": "kept it up"},
            ],
        },
    ]
    lines = path.read_text(encoding="utf-8").splitlines()
    comment_lines = [json.dumps({"type": "commentary", **comment}) for comment in comments]
    path.write_text("\n".join(lines[:-1] + comment_lines + lines[-1:]) + "\n", encoding="utf-8")

    model = build_view_model(journal_path=path)
    series = model["stats"]["series"]

    turn_1, turn_2 = series["turns"].index(1), series["turns"].index(2)
    first_player, second_player = series["players"][0], series["players"][1]
    assert first_player["skill_strong"][turn_1] == 1
    assert first_player["skill_strong"][turn_2] == 2  # cumulative
    assert first_player["skill_strong"][-1] == 2  # flat after the last tag
    assert first_player["skill_weak"][-1] == 0  # the taunting is affect: not scored
    assert first_player["conduct_deceptive"][turn_1] == 1
    assert second_player["skill_weak"][turn_1] == 1
    assert second_player["conduct_adversarial"][turn_1] == 1

    valence = model["stats"]["tag_valence"]
    assert valence["betrayal"] == "adversarial"
    assert valence["deception"] == "deceptive"
    assert valence["strong_move"] == "strong"
    assert valence["taunting"] is None  # affect is descriptive, never scored


def test_usage_and_cost_aggregate_from_the_trace_sidecar(tmp_path: Path):
    path = journal_with_game(tmp_path=tmp_path)
    trace_records = [
        {
            "seq": 1,
            "caller": PLAYER_NAMES[0],
            "model": "claude-sonnet-5",
            "input_tokens": 1_000,
            "output_tokens": 100,
            "error": None,
        },
        {
            "seq": 2,
            "caller": PLAYER_NAMES[0],
            "model": "claude-sonnet-5",
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
    assert player_usage["cost_usd"] == pytest.approx((3_000 * 2.00 + 300 * 10.00) / 1_000_000)
    assert usage["commentator"]["errors"] == 1
    assert usage["commentator"]["cost_usd"] is None  # unknown model: no price invented


def test_a_game_without_a_trace_reports_no_usage(tmp_path: Path):
    model = build_view_model(journal_path=journal_with_game(tmp_path=tmp_path))

    assert model["stats"]["usage"] == {}


def test_exported_reader_html_is_self_contained(tmp_path: Path):
    journal_path = journal_with_game(tmp_path=tmp_path)
    model = build_view_model(journal_path=journal_path)

    html = render_reader_export_html(
        game_view_model=model,
        evaluation_view_model=build_evaluation_view_model(
            journal_path=journal_path, version=version_for(journal_path=journal_path, run_id=None)
        ),
    )

    assert html.startswith("<!DOCTYPE html>")
    assert "window.BOOT" in html
    assert "live: false" in html
    assert model["players"][0]["name"] in html
    # every stylesheet and script is inlined; the file must work from disk with no server behind it
    assert "<script src=" not in html
    assert 'href="/assets/' not in html


def test_exported_reader_html_embeds_the_board_map_with_every_territory(tmp_path: Path):
    journal_path = journal_with_game(tmp_path=tmp_path)

    html = render_reader_export_html(
        game_view_model=build_view_model(journal_path=journal_path),
        evaluation_view_model=build_evaluation_view_model(
            journal_path=journal_path, version=version_for(journal_path=journal_path, run_id=None)
        ),
    )

    assert "<svg" in html
    assert all(f'id="{territory}"' in html for territory in Territory)
    assert "CC BY-SA 3.0" in html  # the map's attribution requirement


def test_reader_rendering_polls_the_given_url(tmp_path: Path):
    journal_path = journal_with_game(tmp_path=tmp_path)

    html = render_reader_html(
        game_view_model=build_view_model(journal_path=journal_path),
        evaluation_view_model=build_evaluation_view_model(
            journal_path=journal_path, version=version_for(journal_path=journal_path, run_id=None)
        ),
        live=True,
        live_running=True,
        poll_url="/api/games/some-game",
        eval_poll_url="/api/games/some-game/evaluation",
        evaluate_url="/api/games/some-game/evaluate",
        verifications_url="/api/games/some-game/verifications",
        trace_url="/api/games/some-game/trace",
        analysis_url="/api/games/some-game/analysis",
        experiment_judge_url="",
    )

    assert "live: true" in html
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
            commentators=[],
        )
    )
    journal.close()

    model = build_view_model(journal_path=path)

    assert len(model["steps"]) == 1
    assert model["result"] is None
