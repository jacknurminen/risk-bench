"""Aggregating a tournament's member games into one cross-game statistics view model.

The sample sizes are small (a handful of games), so the aggregation leans on the
nonparametric path. For every per-turn metric the model carries several distinct
things, kept separate because they answer different questions: the cross-game mean
line, a 95% bootstrap confidence interval for that mean (how well we know the
average — needs no normality assumption, in the same spirit as the Wilson win-rate
intervals), the parametric 95% Student-t interval for the same mean (a closed-form
alternative offered for side-by-side comparison during exploration), and the raw
min-max envelope (how far individual games spread, the extremes rather than a
confidence statement). Every individual game's own series rides along for the
charts too.
"""

import json
import math
import random
from pathlib import Path
from typing import Any

from risk_bench.viewer.snapshots import build_view_model

# resamples per per-turn bootstrap interval; 500 gives a visually stable 2.5/97.5
# band while keeping the whole-page rebuild well under the live poll interval, since
# this view model is recomputed uncached on every poll of a running tournament
_BOOTSTRAP_ITERATIONS = 500

# a fixed seed makes the bands identical across re-renders of the same tournament,
# so a live page does not jitter its confidence bands between polls
_BOOTSTRAP_SEED = 2026

# two-sided 95% Student-t critical values by degrees of freedom; the table covers the
# small-sample range where t departs sharply from the normal 1.96 (the regime these
# few-game tournaments live in), and larger samples fall back to the 1.96 it converges to
_T_CRITICAL_VALUES_95 = {
    1: 12.706,
    2: 4.303,
    3: 3.182,
    4: 2.776,
    5: 2.571,
    6: 2.447,
    7: 2.365,
    8: 2.306,
    9: 2.262,
    10: 2.228,
    11: 2.201,
    12: 2.179,
    13: 2.160,
    14: 2.145,
    15: 2.131,
    16: 2.120,
    17: 2.110,
    18: 2.101,
    19: 2.093,
    20: 2.086,
    21: 2.080,
    22: 2.074,
    23: 2.069,
    24: 2.064,
    25: 2.060,
    26: 2.056,
    27: 2.052,
    28: 2.048,
    29: 2.045,
    30: 2.042,
}
_NORMAL_CRITICAL_VALUE_95 = 1.96

CHART_METRICS = (
    "army_share",
    "territories",
    "income",
    "cards",
    "skill_strong",
    "skill_weak",
    "conduct_cooperative",
    "conduct_adversarial",
    "conduct_deceptive",
)


def build_tournament_view_model(tournament_directory: Path) -> dict[str, Any]:
    """Replay a tournament's member games and merge them into one statistics view model.

    Per player and metric the model carries the per-turn cross-game mean, a 95%
    bootstrap confidence interval for that mean, the parametric Student-t interval
    for it, the min-max envelope, and each game's own series; at every turn only the
    games still running contribute, so finished games never bias late-game averages
    (and both confidence bands widen there as fewer games remain).

    Args:
        tournament_directory: The tournament's folder, holding ``tournament.json``
            and one subfolder per member game.

    Returns:
        A JSON-serializable dict with players, merged metric series, the member
        game list, and per-player cross-game aggregates.

    Raises:
        ValueError: If the directory holds no tournament manifest or no member
            game has a readable journal yet.
    """
    manifest_path = tournament_directory / "tournament.json"
    if not manifest_path.is_file():
        raise ValueError(f"{tournament_directory} is not a tournament (missing tournament.json).")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    members = []
    for game_id in manifest["game_ids"]:
        journal_path = tournament_directory / game_id / "game.jsonl"
        if journal_path.is_file():
            members.append((game_id, build_view_model(journal_path=journal_path)))
    if not members:
        raise ValueError(f"Tournament {manifest['id']} has no readable game journals yet.")

    player_names = [player["name"] for player in members[0][1]["players"]]
    member_series = [model["stats"]["series"] for _, model in members]
    turns = list(range(max(len(series["turns"]) for series in member_series)))
    bootstrap_rng = random.Random(_BOOTSTRAP_SEED)

    return {
        "id": manifest["id"],
        "players": player_names,
        "turns": turns,
        "game_count_by_turn": [sum(1 for series in member_series if turn < len(series["turns"])) for turn in turns],
        "metrics": {
            metric: [
                _merge_metric(
                    member_series=member_series, seat=seat, metric=metric, turns=turns, bootstrap_rng=bootstrap_rng
                )
                for seat in range(len(player_names))
            ]
            for metric in CHART_METRICS
        },
        "games": [_game_row(game_id=game_id, model=model) for game_id, model in members],
        "table": _aggregate_table(player_names=player_names, members=members),
    }


def _merge_metric(
    member_series: list[dict[str, Any]], seat: int, metric: str, turns: list[int], bootstrap_rng: random.Random
) -> dict[str, Any]:
    per_game = [series["players"][seat][metric] for series in member_series]
    mean, low, high = [], [], []
    bootstrap_low, bootstrap_high = [], []
    t_low, t_high = [], []
    for turn in turns:
        values = [game_values[turn] for game_values in per_game if turn < len(game_values)]
        mean.append(round(sum(values) / len(values), 4))
        low.append(min(values))
        high.append(max(values))
        bootstrap_interval = _bootstrap_mean_interval(values=values, bootstrap_rng=bootstrap_rng)
        bootstrap_low.append(round(bootstrap_interval[0], 4))
        bootstrap_high.append(round(bootstrap_interval[1], 4))
        t_interval = _t_interval(values=values)
        t_low.append(round(t_interval[0], 4))
        t_high.append(round(t_interval[1], 4))
    return {
        "mean": mean,
        "min": low,
        "max": high,
        "ci_low": bootstrap_low,
        "ci_high": bootstrap_high,
        "t_low": t_low,
        "t_high": t_high,
        "games": per_game,
    }


def _bootstrap_mean_interval(values: list[float], bootstrap_rng: random.Random) -> tuple[float, float]:
    sample_size = len(values)
    if sample_size < 2:
        return values[0], values[0]
    # draw every resample's observations in one C-level call, then sum each
    # consecutive sample_size-sized block — far cheaper than a choices() call per resample
    draws = bootstrap_rng.choices(values, k=sample_size * _BOOTSTRAP_ITERATIONS)
    resampled_means = sorted(
        sum(draws[start : start + sample_size]) / sample_size for start in range(0, len(draws), sample_size)
    )
    return (
        resampled_means[round(0.025 * (_BOOTSTRAP_ITERATIONS - 1))],
        resampled_means[round(0.975 * (_BOOTSTRAP_ITERATIONS - 1))],
    )


def _t_interval(values: list[float]) -> tuple[float, float]:
    sample_size = len(values)
    if sample_size < 2:
        return values[0], values[0]
    mean = sum(values) / sample_size
    variance = sum((value - mean) ** 2 for value in values) / (sample_size - 1)
    standard_error = math.sqrt(variance) / math.sqrt(sample_size)
    margin = _T_CRITICAL_VALUES_95.get(sample_size - 1, _NORMAL_CRITICAL_VALUE_95) * standard_error
    return mean - margin, mean + margin


def _game_row(game_id: str, model: dict[str, Any]) -> dict[str, Any]:
    usage = model["stats"]["usage"].values()
    known_costs = [entry["cost_usd"] for entry in usage if entry["cost_usd"] is not None]
    return {
        "id": game_id,
        "seed": model["seed"],
        "finished": model["result"] is not None,
        "winner": model["result"]["winner"] if model["result"] else None,
        "turn_count": model["steps"][-1]["turn"],
        "cost_usd": sum(known_costs) if known_costs else None,
    }


def _aggregate_table(player_names: list[str], members: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    finished = [model for _, model in members if model["result"] is not None]
    rows = []
    for seat, name in enumerate(player_names):
        wins = sum(1 for model in finished if model["result"]["winner"] == name)
        win_low, win_high = _wilson_interval(wins=wins, games=len(finished))
        combined = dict.fromkeys(
            ("attacks", "conquests", "armies_destroyed", "armies_lost", "public_messages", "private_messages"), 0
        )
        tags: dict[str, int] = {}
        input_tokens, output_tokens, costs = 0, 0, []
        for _, model in members:
            per_player = model["stats"]["per_player"][seat]
            for key in combined:
                combined[key] += per_player[key]
            for behavior, count in per_player["tags"].items():
                tags[behavior] = tags.get(behavior, 0) + count
            usage = model["stats"]["usage"].get(name)
            if usage is not None:
                input_tokens += usage["input_tokens"]
                output_tokens += usage["output_tokens"]
                if usage["cost_usd"] is not None:
                    costs.append(usage["cost_usd"])
        rows.append(
            {
                "name": name,
                "wins": wins,
                "finished_games": len(finished),
                "win_rate_low": round(win_low, 4),
                "win_rate_high": round(win_high, 4),
                **combined,
                "tags": tags,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_usd": sum(costs) if costs else None,
            }
        )
    return rows


def _wilson_interval(wins: int, games: int) -> tuple[float, float]:
    if games == 0:
        return (0.0, 1.0)
    z = 1.96  # 95%
    proportion = wins / games
    denominator = 1 + z**2 / games
    center = (proportion + z**2 / (2 * games)) / denominator
    margin = z * math.sqrt(proportion * (1 - proportion) / games + z**2 / (4 * games**2)) / denominator
    return (max(0.0, center - margin), min(1.0, center + margin))
