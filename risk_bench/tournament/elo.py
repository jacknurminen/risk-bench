from typing import Final

from risk_bench.game.types import PlayerId

INITIAL_RATING: Final[float] = 1000.0


def update_ratings(ratings: dict[PlayerId, float], winner: PlayerId | None, k_factor: float) -> dict[PlayerId, float]:
    """Update Elo ratings after one multiplayer game, winner-takes-all.

    The winner is treated as having beaten each loser in a pairwise game;
    losers neither gain nor lose against each other, so the update is zero-sum.
    All expected scores are computed from the pre-game ratings. Draws (no
    winner) leave all ratings unchanged.

    Args:
        ratings: Pre-game rating of each participant in the game.
        winner: The winning player, or None for a draw.
        k_factor: Elo K-factor controlling update magnitude.

    Returns:
        The updated ratings for the same participants.
    """
    updated = dict(ratings)
    if winner is None:
        return updated
    for opponent, opponent_rating in ratings.items():
        if opponent == winner:
            continue
        expected_win_probability = _expected_score(rating=ratings[winner], opponent_rating=opponent_rating)
        rating_change = k_factor * (1.0 - expected_win_probability)
        updated[winner] += rating_change
        updated[opponent] -= rating_change
    return updated


def _expected_score(rating: float, opponent_rating: float) -> float:
    return 1.0 / (1.0 + 10.0 ** ((opponent_rating - rating) / 400.0))
