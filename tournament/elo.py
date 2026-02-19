"""Elo rating calculation — standard K=32 for multi-player games."""

from __future__ import annotations

K = 32.0
INITIAL_ELO = 1000.0


def expected_score(rating_a: float, rating_b: float) -> float:
    """Expected score for player A against player B."""
    return 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / 400.0))


def update_elo(ratings: dict[str, float], winner: str) -> dict[str, float]:
    """
    Update Elo ratings for a multi-player game.
    Winner gets score=1 against each other player.
    Losers get score=0 against the winner and 0.5 against each other.

    Returns new ratings dict.
    """
    players = list(ratings.keys())
    new_ratings = dict(ratings)

    for player in players:
        delta = 0.0
        for opponent in players:
            if player == opponent:
                continue
            expected = expected_score(ratings[player], ratings[opponent])
            if player == winner:
                actual = 1.0
            elif opponent == winner:
                actual = 0.0
            else:
                actual = 0.5  # draw among non-winners
            delta += K * (actual - expected)

        # Normalize by number of opponents
        delta /= len(players) - 1
        new_ratings[player] = ratings[player] + delta

    return new_ratings
