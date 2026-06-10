import random
from pathlib import Path

import pytest

from risk_bench.game.types import PlayerId
from risk_bench.players.base import Player
from risk_bench.players.random import RandomPlayer
from risk_bench.tournament.elo import INITIAL_RATING
from risk_bench.tournament.results import TournamentReport, write_report
from risk_bench.tournament.runner import TournamentRunner


def random_player_factory(player_id: PlayerId, rng: random.Random) -> Player:
    return RandomPlayer(player_id=player_id, rng=rng)


def run_tournament(games: int, participant_names: list[str], players_per_game: int) -> TournamentReport:
    runner = TournamentRunner(
        participants={name: random_player_factory for name in participant_names},
        games=games,
        players_per_game=players_per_game,
        base_seed=100,
        max_turns=2_000,
        allow_private_messages=False,
        k_factor=32.0,
        verbose=False,
    )
    return runner.run()


def test_tournament_runs_all_games_and_builds_consistent_standings():
    report = run_tournament(games=4, participant_names=["alpha", "beta", "gamma"], players_per_game=3)

    assert len(report.results) == 4
    assert sum(standing.wins for standing in report.standings) == sum(
        1 for result in report.results if result.winner is not None
    )
    assert all(standing.games_played == 4 for standing in report.standings)  # one matchup, everyone seated
    assert sum(standing.rating for standing in report.standings) == pytest.approx(3 * INITIAL_RATING)
    assert [standing.rating for standing in report.standings] == sorted(
        (standing.rating for standing in report.standings), reverse=True
    )


def test_seat_order_rotates_across_games_of_the_same_matchup():
    report = run_tournament(games=3, participant_names=["alpha", "beta", "gamma"], players_per_game=3)

    first_seats = [result.player_names[0] for result in report.results]
    assert first_seats == ["alpha", "beta", "gamma"]


def test_matchups_cycle_through_all_combinations():
    participant_names = ["alpha", "beta", "gamma", "delta"]
    report = run_tournament(games=4, participant_names=participant_names, players_per_game=3)

    matchups = {frozenset(result.player_names) for result in report.results}
    assert len(matchups) == 4  # every 3-of-4 combination appears once
    assert all(len(matchup) == 3 for matchup in matchups)


def test_seat_counts_outside_3_to_6_are_rejected():
    with pytest.raises(ValueError, match="3-6"):
        run_tournament(games=1, participant_names=["alpha", "beta"], players_per_game=2)


def test_tournaments_are_reproducible_from_the_base_seed():
    first = run_tournament(games=3, participant_names=["alpha", "beta", "gamma"], players_per_game=3)
    second = run_tournament(games=3, participant_names=["alpha", "beta", "gamma"], players_per_game=3)

    assert first.results == second.results
    assert [standing.rating for standing in first.standings] == [standing.rating for standing in second.standings]


def test_too_few_participants_for_the_seat_count_is_rejected():
    with pytest.raises(ValueError, match="Cannot seat"):
        TournamentRunner(
            participants={"alpha": random_player_factory, "beta": random_player_factory},
            games=1,
            players_per_game=3,
            base_seed=0,
            max_turns=100,
            allow_private_messages=False,
            k_factor=32.0,
            verbose=False,
        )


def test_write_report_round_trips_through_json(tmp_path: Path):
    report = run_tournament(games=1, participant_names=["alpha", "beta", "gamma"], players_per_game=3)
    path = tmp_path / "reports" / "tournament.json"

    write_report(report=report, path=path)

    assert TournamentReport.model_validate_json(path.read_text(encoding="utf-8")) == report
