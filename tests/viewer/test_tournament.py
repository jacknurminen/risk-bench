import json
from pathlib import Path

import pytest

from risk_bench.viewer.tournament import build_tournament_view_model
from tests.runner.test_journal import PLAYER_NAMES, write_game_journal


def tournament_directory_with_games(tmp_path: Path, seeds: list[int]) -> Path:
    tournament_directory = tmp_path / "20990101-000000-seed0"
    game_ids = [f"{tournament_directory.name}-g{index}" for index in range(1, len(seeds) + 1)]
    for game_id, seed in zip(game_ids, seeds):
        (tournament_directory / game_id).mkdir(parents=True)
        write_game_journal(path=tournament_directory / game_id / "game.jsonl", seed=seed, max_turns=2_000)
    manifest = {
        "id": tournament_directory.name,
        "players": ["random"] * len(PLAYER_NAMES),
        "game_ids": game_ids,
        "created_at": "2099-01-01T00:00:00+00:00",
    }
    (tournament_directory / "tournament.json").write_text(json.dumps(manifest), encoding="utf-8")
    return tournament_directory


def test_member_games_merge_into_aligned_envelope_series(tmp_path: Path):
    directory = tournament_directory_with_games(tmp_path=tmp_path, seeds=[11, 12, 13])

    model = build_tournament_view_model(tournament_directory=directory)

    assert model["players"] == PLAYER_NAMES
    turn_count = len(model["turns"])
    assert model["game_count_by_turn"][0] == 3  # every game contributes at turn 0
    assert model["game_count_by_turn"][-1] >= 1  # only the longest game(s) reach the last turn
    assert sorted(model["game_count_by_turn"], reverse=True) == model["game_count_by_turn"]

    for metric_series in model["metrics"].values():
        for seat_series in metric_series:
            assert len(seat_series["mean"]) == len(seat_series["min"]) == len(seat_series["max"]) == turn_count
            assert len(seat_series["games"]) == 3
            for turn_index in range(turn_count):  # 1e-3 absorbs the mean's 4-decimal rounding
                assert seat_series["min"][turn_index] <= seat_series["mean"][turn_index] + 1e-3
                assert seat_series["mean"][turn_index] <= seat_series["max"][turn_index] + 1e-3

    json.dumps(model)  # must be embeddable in the HTML


def test_aggregates_count_wins_and_sum_per_game_totals(tmp_path: Path):
    directory = tournament_directory_with_games(tmp_path=tmp_path, seeds=[11, 12])

    model = build_tournament_view_model(tournament_directory=directory)

    assert [game["finished"] for game in model["games"]] == [True, True]
    total_wins = sum(row["wins"] for row in model["table"])
    winners = {game["winner"] for game in model["games"] if game["winner"]}
    assert total_wins == sum(1 for game in model["games"] if game["winner"])
    assert winners <= set(PLAYER_NAMES)
    for row in model["table"]:
        assert row["finished_games"] == 2
        assert 0.0 <= row["win_rate_low"] <= row["wins"] / 2 <= row["win_rate_high"] <= 1.0
        assert row["attacks"] >= 0 and row["cost_usd"] is None  # random players: no trace, no cost


def test_a_directory_without_a_manifest_is_rejected(tmp_path: Path):
    with pytest.raises(ValueError, match="missing tournament.json"):
        build_tournament_view_model(tournament_directory=tmp_path)


def test_a_tournament_whose_games_have_not_started_is_rejected(tmp_path: Path):
    (tmp_path / "tournament.json").write_text(
        json.dumps({"id": "t", "players": [], "game_ids": ["t-g1"], "created_at": "2099-01-01T00:00:00+00:00"}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="no readable game journals"):
        build_tournament_view_model(tournament_directory=tmp_path)
