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
from typing import Any, Final

from risk_bench.agents.tracing import TOKEN_CATEGORY_ORDER
from risk_bench.runner.evaluation_versions import list_versions, version_for
from risk_bench.runner.tournament_evaluation import get_batch, list_batches
from risk_bench.specs import named_specs
from risk_bench.viewer.evaluation import build_evaluation_view_model
from risk_bench.viewer.research import tournament_research
from risk_bench.viewer.snapshots import build_view_model

# the scored valences a stacked agreement bar is drawn for; affect tags are still counted toward the
# overall unanimous share but have no cumulative chart, so they get no per-valence breakdown here
_SCORED_VALENCES: Final[tuple[str, ...]] = ("strong", "weak", "cooperative", "adversarial", "deceptive")

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


def build_tournament_view_model(tournament_directory: Path, evaluation_version: str | None = None) -> dict[str, Any]:
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
    batches = list_batches(tournament_directory)
    selected = get_batch(tournament_directory, evaluation_version) if evaluation_version else None

    members = []
    for game_id in manifest["game_ids"]:
        journal_path = tournament_directory / game_id / "game.jsonl"
        if journal_path.is_file():
            members.append((game_id, build_view_model(journal_path=journal_path)))
    if not members and not manifest.get("replays"):
        raise ValueError(f"Tournament {manifest['id']} has no readable game journals yet.")

    player_names = ([player["name"] for player in members[0][1]["players"]] if members
                    else list(named_specs(specs=manifest["players"])))
    member_series = [
        _series_in_player_order(series=model["stats"]["series"], model=model, player_names=player_names)
        for _, model in members
    ]
    turns = list(range(max((len(series["turns"]) for series in member_series), default=0)))
    bootstrap_rng = random.Random(_BOOTSTRAP_SEED)
    research_games = []
    for game_id, model in members:
        if selected and game_id not in selected["games"]:
            research_games.append((game_id, model["research_context"],
                                   {"error": "Not included in this evaluation version", "method": None}))
            continue
        path = tournament_directory / game_id / "game.jsonl"
        latest = {}
        for version in list_versions(path):
            latest.setdefault(version.method, version)
        job = selected["games"].get(game_id, {}) if selected else None
        versions = ([version_for(path, run_id) for run_id in job.get("versions", [])]
                    if job is not None else list(latest.values()))
        for version in versions or [None]:
            try:
                report = build_evaluation_view_model(path, version)["research_report"]
            except (ValueError, OSError, KeyError) as error:
                report = {"error": f"Evaluation unavailable: {error}",
                          "method": version.method if version else None}
            if job is not None:
                if job["status"] in {"queued", "running", "failed", "interrupted"}:
                    report["error"] = job.get("error") or job["status"].capitalize()
                    report["completion_status"] = job["status"]
                elif version is None and job.get("versions"):
                    report["error"] = "Saved evaluation version is missing"
            research_games.append((game_id, model["research_context"], report))


    return {
        "id": manifest["id"],
        "evaluation_version": evaluation_version,
        "evaluation_versions": batches,
        "evaluation_batch": selected,
        "total_games": len(manifest["game_ids"]),
        "parallel_games": manifest.get("parallel_games", len(manifest["game_ids"])),
        "seating_mode": manifest.get("seating_mode", "random"),
        "can_continue": len(manifest["game_ids"]) < 1000 and (
            bool(manifest.get("launch_config")) or any(
                (tournament_directory / game / "launch.json").is_file() for game in manifest["game_ids"]
            )
        ),
        "seating_schedule": [
            {"game_id": game_id, "seed": manifest["seeds"][index],
             "players": [manifest["players"][position - 1] for position in order], "order": order}
            for index, (game_id, order) in enumerate(zip(manifest["game_ids"], manifest.get("seating_orders") or []))
        ],
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
        "per_turn_usage": _merge_per_turn_usage(member_models=[model for _, model in members]),
        "games": [_game_row(game_id=game_id, model=model) for game_id, model in members],
        "table": _aggregate_table(player_names=player_names, members=members),
        "agreement": _aggregate_agreement(player_names=player_names, members=members),
        "research": tournament_research(research_games),
        "output_volume": _aggregate_output_volume(members),
    }


def _series_in_player_order(
    series: dict[str, Any], model: dict[str, Any], player_names: list[str]
) -> dict[str, Any]:
    # seating differs per game (cycle or shuffled seating), so a seat index means a different player in each
    # game; reorder every game's per-seat series to the tournament's player order before merging by index
    seats = _seats_by_name(model=model)
    return {**series, "players": [series["players"][seats[name]] for name in player_names]}


def _seats_by_name(model: dict[str, Any]) -> dict[str, int]:
    return {player["name"]: seat for seat, player in enumerate(model["players"])}


def _aggregate_output_volume(members: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    """Pool recorded output and denominators, including silent and unfinished games."""
    players: dict[str, dict[str, Any]] = {}
    includes_setup = False
    for _, model in members:
        turns = {step["turn"] for step in model["steps"]}
        names = {player["name"] for player in model["players"]}
        includes_setup |= 0 in turns
        for player in model["players"]:
            name = player["name"]
            row = players.setdefault(name, {
                "name": name,
                **dict.fromkeys(("games", "recorded_turns", "messages", "public_messages", "private_messages",
                                 "message_words", "thoughts", "thought_words"), 0),
            })
            row["games"] += 1
            row["recorded_turns"] += len(turns)
        for message in model.get("messages", []):
            if message["sender"] not in names:
                continue
            row = players[message["sender"]]
            row["messages"] += 1
            row["private_messages" if message["visibility"] == "private" else "public_messages"] += 1
            row["message_words"] += len((message.get("text") or "").split())
        for thought in model.get("thoughts", []):
            if thought["player"] not in names:
                continue
            row = players[thought["player"]]
            row["thoughts"] += 1
            row["thought_words"] += len((thought.get("text") or "").split())
    for row in players.values():
        for key, numerator, denominator in (
            ("messages_per_game", "messages", "games"),
            ("messages_per_turn", "messages", "recorded_turns"),
            ("words_per_message", "message_words", "messages"),
            ("thoughts_per_game", "thoughts", "games"),
            ("thoughts_per_turn", "thoughts", "recorded_turns"),
            ("words_per_thought", "thought_words", "thoughts"),
        ):
            row[key] = row[numerator] / row[denominator] if row[denominator] else None
    return {
        "game_count": len(members),
        "unfinished_games": sum(model["result"] is None for _, model in members),
        "includes_setup": includes_setup,
        "players": list(players.values()),
    }


def _aggregate_agreement(player_names: list[str], members: list[tuple[str, dict[str, Any]]]) -> dict[str, Any] | None:
    """Pool every game's per-instance annotator agreement into a cross-game composition per player.

    An instance is a single ``(turn, player, behavior)`` judgement; its agreement level is how many
    of that game's panel members made it. Per player we count, by behavior valence, how many instances
    landed at each level (1..panel_size), plus the overall total and the unanimous subset — the raw
    material for the stacked agreement bars and the per-player unanimous share. Returns None when no
    member game ran a commentator panel.
    """
    commentated = [model for _, model in members if model.get("commentators")]
    if not commentated:
        return None
    panel_size = max(len(model["commentators"]) for model in commentated)
    by_valence = {name: {valence: [0] * panel_size for valence in _SCORED_VALENCES} for name in player_names}
    totals = {name: 0 for name in player_names}
    unanimous = {name: 0 for name in player_names}
    for model in commentated:
        game_panel_size = len(model["commentators"])
        tag_valence = model["stats"]["tag_valence"]
        instances = _instances_by_player(commentary=model["commentary"])
        for name in player_names:
            for behavior, level in instances.get(name, []):
                totals[name] += 1
                if level == game_panel_size:
                    unanimous[name] += 1
                valence = tag_valence.get(behavior)
                if valence in by_valence[name] and 1 <= level <= panel_size:
                    by_valence[name][valence][level - 1] += 1
    return {
        "panel_size": panel_size,
        "games": len(commentated),
        "levels": list(range(1, panel_size + 1)),
        "per_player": [
            {"by_valence": by_valence[name], "total": totals[name], "unanimous": unanimous[name]}
            for name in player_names
        ],
    }


def _instances_by_player(commentary: list[dict[str, Any]]) -> dict[str, list[tuple[str, int]]]:
    # one pass over a game's commentary: each player's (turn, behavior) instances, each paired with the
    # number of distinct members that flagged it (its agreement level within this game's panel)
    members_by_instance: dict[str, dict[tuple[int, str], set[int]]] = {}
    for comment in commentary:
        for tag in comment["tags"]:
            instance = (int(comment["turn"]), tag["behavior"])
            members_by_instance.setdefault(tag["player"], {}).setdefault(instance, set()).add(
                comment["commentator_index"]
            )
    return {
        player: [(behavior, len(members)) for (_, behavior), members in instances.items()]
        for player, instances in members_by_instance.items()
    }


def _merge_per_turn_usage(member_models: list[dict[str, Any]]) -> dict[str, Any]:
    """Average each game's per-turn token and cost series across the tournament.

    Merges both the by-category and by-caller breakdowns. At every turn only the
    games that reached it contribute, so a group a game happened not to use that
    turn counts as zero rather than being skipped, and finished games never lift
    or drop the late-game means by dropping out silently.
    """
    per_game = [model["stats"]["per_turn_usage"] for model in member_models]
    turns = sorted({turn for game in per_game for turn in game["turns"]})
    categories = [
        category for category in TOKEN_CATEGORY_ORDER if any(category in game["categories"] for game in per_game)
    ]
    callers: list[str] = []
    for game in per_game:
        callers.extend(caller for caller in game["callers"] if caller not in callers)
    turn_positions = [{turn: position for position, turn in enumerate(game["turns"])} for game in per_game]
    return {
        "turns": turns,
        "categories": categories,
        "callers": callers,
        "by_category": {
            category: _mean_usage_series(per_game, turn_positions, "by_category", category, turns)
            for category in categories
        },
        "by_caller": {
            caller: _mean_usage_series(per_game, turn_positions, "by_caller", caller, turns) for caller in callers
        },
    }


def _mean_usage_series(
    per_game: list[dict[str, Any]],
    turn_positions: list[dict[int, int]],
    breakdown: str,
    group: str,
    turns: list[int],
) -> dict[str, list[float]]:
    measures = ("input_tokens", "output_tokens", "input_cost", "output_cost")
    means: dict[str, list[float]] = {measure: [] for measure in measures}
    for turn in turns:
        collected: dict[str, list[float]] = {measure: [] for measure in measures}
        for game, positions in zip(per_game, turn_positions):
            position = positions.get(turn)
            if position is None:
                continue  # this game never reached this turn
            series = game[breakdown].get(group)
            for measure in measures:
                collected[measure].append(series[measure][position] if series is not None else 0)
        for measure in measures:
            values = collected[measure]
            means[measure].append(round(sum(values) / len(values), 4) if values else 0)
    return means


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
        "context": model["research_context"],
    }


def _aggregate_table(player_names: list[str], members: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    finished = [model for _, model in members if model["result"] is not None]
    rows = []
    for name in player_names:
        wins = sum(1 for model in finished if model["result"]["winner"] == name)
        win_low, win_high = _wilson_interval(wins=wins, games=len(finished))
        combined = dict.fromkeys(
            ("attacks", "conquests", "armies_destroyed", "armies_lost", "public_messages", "private_messages"), 0
        )
        tags: dict[str, int] = {}
        input_tokens, output_tokens, costs = 0, 0, []
        for _, model in members:
            per_player = model["stats"]["per_player"][_seats_by_name(model=model)[name]]
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
                "experiment_profile": _experiment_profile_label(name=name, members=members),
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


def _experiment_profile_label(name: str, members: list[tuple[str, dict[str, Any]]]) -> str | None:
    profiles = (
        player.get("experiment_profile")
        for _, model in members
        for player in model["players"]
        if player["name"] == name
    )
    return next((profile["label"] for profile in profiles if profile), None)


def _wilson_interval(wins: int, games: int) -> tuple[float, float]:
    if games == 0:
        return (0.0, 1.0)
    z = 1.96  # 95%
    proportion = wins / games
    denominator = 1 + z**2 / games
    center = (proportion + z**2 / (2 * games)) / denominator
    margin = z * math.sqrt(proportion * (1 - proportion) / games + z**2 / (4 * games**2)) / denominator
    return (max(0.0, center - margin), min(1.0, center + margin))
