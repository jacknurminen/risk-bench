import pytest

from risk_bench.tournament.elo import update_ratings

EQUAL_RATINGS = {"alice": 1000.0, "bob": 1000.0, "carla": 1000.0}


def test_the_winner_gains_what_the_losers_lose():
    updated = update_ratings(ratings=EQUAL_RATINGS, winner="alice", k_factor=32.0)

    assert updated["alice"] == pytest.approx(1000.0 + 32.0)  # 16 from each equally rated loser
    assert updated["bob"] == pytest.approx(1000.0 - 16.0)
    assert updated["carla"] == pytest.approx(1000.0 - 16.0)
    assert sum(updated.values()) == pytest.approx(sum(EQUAL_RATINGS.values()))


def test_beating_stronger_opponents_pays_more():
    underdog_ratings = {"alice": 900.0, "bob": 1100.0, "carla": 1100.0}
    favourite_ratings = {"alice": 1100.0, "bob": 900.0, "carla": 900.0}

    underdog_gain = update_ratings(ratings=underdog_ratings, winner="alice", k_factor=32.0)["alice"] - 900.0
    favourite_gain = update_ratings(ratings=favourite_ratings, winner="alice", k_factor=32.0)["alice"] - 1100.0

    assert underdog_gain > 32.0 > favourite_gain > 0.0


def test_draws_change_nothing():
    assert update_ratings(ratings=EQUAL_RATINGS, winner=None, k_factor=32.0) == EQUAL_RATINGS


def test_expectations_use_pre_game_ratings_simultaneously():
    ratings = {"alice": 1000.0, "bob": 1200.0}
    updated = update_ratings(ratings=ratings, winner="alice", k_factor=32.0)

    expected_alice_win_probability = 1.0 / (1.0 + 10.0 ** ((1200.0 - 1000.0) / 400.0))
    expected_gain = 32.0 * (1.0 - expected_alice_win_probability)
    assert updated["alice"] == pytest.approx(1000.0 + expected_gain)
    assert updated["bob"] == pytest.approx(1200.0 - expected_gain)
