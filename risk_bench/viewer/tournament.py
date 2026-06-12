"""Aggregating a tournament's member games into one cross-game statistics view model.

The sample sizes are small (a handful of games), so the aggregation stays
assumption-free: per-turn mean with a min-max envelope plus every individual
game's series for the charts, and exact-ish Wilson score intervals only for
win rates, where they are legitimate at any sample size.
"""

import json
import math
from pathlib import Path
from typing import Any

from risk_bench.viewer.snapshots import build_view_model

CHART_METRICS = ("army_share", "territories", "income", "cards", "positive_tags", "negative_tags")


def build_tournament_view_model(tournament_directory: Path) -> dict[str, Any]:
    """Replay a tournament's member games and merge them into one statistics view model.

    Per player and metric the model carries the per-turn cross-game mean, the
    min-max envelope, and each game's own series; at every turn only the games
    still running contribute, so finished games never bias late-game averages.

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

    return {
        "id": manifest["id"],
        "players": player_names,
        "turns": turns,
        "game_count_by_turn": [sum(1 for series in member_series if turn < len(series["turns"])) for turn in turns],
        "metrics": {
            metric: [
                _merge_metric(member_series=member_series, seat=seat, metric=metric, turns=turns)
                for seat in range(len(player_names))
            ]
            for metric in CHART_METRICS
        },
        "games": [_game_row(game_id=game_id, model=model) for game_id, model in members],
        "table": _aggregate_table(player_names=player_names, members=members),
    }


def _merge_metric(member_series: list[dict[str, Any]], seat: int, metric: str, turns: list[int]) -> dict[str, Any]:
    per_game = [series["players"][seat][metric] for series in member_series]
    mean, low, high = [], [], []
    for turn in turns:
        values = [game_values[turn] for game_values in per_game if turn < len(game_values)]
        mean.append(round(sum(values) / len(values), 4))
        low.append(min(values))
        high.append(max(values))
    return {"mean": mean, "min": low, "max": high, "games": per_game}


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
