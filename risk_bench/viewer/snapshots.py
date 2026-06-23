import random
from pathlib import Path
from typing import Any, Final

from risk_bench.agents.tracing import per_turn_usage, trace_path_for_journal, usage_by_caller
from risk_bench.game.actions import (
    Action,
    Attack,
    EndAttackPhase,
    Fortify,
    Occupy,
    PlaceArmies,
    SkipFortify,
    TradeCards,
)
from risk_bench.game.board import BOARD
from risk_bench.game.engine import apply_action, setup_game
from risk_bench.game.events import BattleResolved, CardsTraded, GameEvent, PlayerEliminated, TerritoryConquered
from risk_bench.game.rules import reinforcement_count
from risk_bench.game.serialization import action_from_json, event_from_json
from risk_bench.game.state import GameState
from risk_bench.game.types import Continent, PlayerId, Territory
from risk_bench.players.rendering import narrate_event
from risk_bench.runner.commentary import BEHAVIORS, axis_and_valence
from risk_bench.runner.journal import read_journal


def build_view_model(journal_path: Path) -> dict[str, Any]:
    """Replay a game journal into the JSON view model embedded in the HTML viewer.

    The journal's actions are replayed through the real engine (the viewer never
    duplicates game logic), producing one board snapshot per step.

    Args:
        journal_path: The game journal to replay.

    Returns:
        A JSON-serializable dict with players, per-step snapshots, chat, and outcome.

    Raises:
        ValueError: If the file is not a game journal.
    """
    records = read_journal(path=journal_path)
    if not records or records[0].get("type") != "header":
        raise ValueError(f"{journal_path} is not a game journal (missing header line).")
    header = records[0]
    trace_path = trace_path_for_journal(journal_path=journal_path)

    player_names: list[PlayerId] = list(header["player_specs"])
    rng = random.Random(int(header["seed"]))
    state = setup_game(player_ids=player_names, rng=rng)

    steps = [_step(state=state, player_names=player_names, action_text=None, events=[])]
    messages = []
    thoughts = []
    commentary = []
    result = None
    combat_totals = {
        name: {"attacks": 0, "conquests": 0, "armies_destroyed": 0, "armies_lost": 0} for name in player_names
    }
    markers: list[dict[str, Any]] = []
    continent_controllers = _continent_controllers(state=state)
    for record in records[1:]:
        match record["type"]:
            case "action":
                action = action_from_json(record["action"])
                acting_player = str(record["player"])
                state = apply_action(state=state, action=action, rng=rng).state
                steps.append(
                    _step(
                        state=state,
                        player_names=player_names,
                        action_text=_describe_action(player_id=acting_player, action=action),
                        events=[],
                    )
                )
                if isinstance(action, Attack):
                    combat_totals[acting_player]["attacks"] += 1
                current_controllers = _continent_controllers(state=state)
                markers.extend(
                    _continent_change_markers(
                        previous=continent_controllers,
                        current=current_controllers,
                        step_index=len(steps) - 1,
                        turn=state.turn_number,
                    )
                )
                continent_controllers = current_controllers
            case "events":
                events = [event_from_json(data) for data in record["events"]]
                steps[-1]["events"] = [narrate_event(event=event) for event in events]
                _accumulate_combat_totals(combat_totals=combat_totals, events=events)
                markers.extend(_event_markers(events=events, step_index=len(steps) - 1, turn=steps[-1]["turn"]))
            case "message":
                messages.append({**record["message"], "step": len(steps) - 1})
            case "thought":
                thoughts.append({"player": record["player"], "text": record["text"], "step": len(steps) - 1})
            case "commentary":
                commentary.append(
                    {
                        "turn": int(record["turn"]),
                        "rubric": record.get("rubric", ""),  # pre-axis journals had a single unlabelled commentator
                        "text": record["text"],
                        "tags": record["tags"],
                        "step": len(steps) - 1,
                    }
                )
            case "result":
                result = {"winner": record["winner"], "turn_count": int(record["turn_count"])}

    return {
        "title": journal_path.name,
        "seed": int(header["seed"]),
        "max_turns": int(header["max_turns"]),
        "players": [{"name": name, "mission": state.players[name].mission.description} for name in player_names],
        "continents": [
            {
                "name": str(continent),
                "bonus": BOARD.continent_info(continent).army_bonus,
                "territories": sorted(str(t) for t in BOARD.continent_info(continent).territories),
            }
            for continent in Continent
        ],
        "steps": steps,
        "messages": messages,
        "thoughts": thoughts,
        "commentary": commentary,
        "result": result,
        "stats": {
            **_build_stats(
                steps=steps,
                player_names=player_names,
                messages=messages,
                commentary=commentary,
                combat_totals=combat_totals,
                markers=markers,
            ),
            "usage": usage_by_caller(trace_path=trace_path),
            "per_turn_usage": per_turn_usage(trace_path=trace_path),
        },
    }


def _step(
    state: GameState, player_names: list[PlayerId], action_text: str | None, events: list[str]
) -> dict[str, Any]:
    player_index = {name: index for index, name in enumerate(player_names)}
    return {
        "turn": state.turn_number,
        "phase": str(state.phase),
        "current_player": state.current_player,
        "action": action_text,
        "events": events,
        "board": {str(t): [player_index[state.owner[t]], state.armies[t]] for t in Territory},
        "players": [
            {
                "territories": len(state.territories_owned_by(player_id=name)),
                "armies": state.army_count(player_id=name),
                "cards": len(state.players[name].cards),
                "income": reinforcement_count(state=state, player_id=name),
                "eliminated": state.players[name].eliminated_by is not None,
            }
            for name in player_names
        ],
    }


# the scored cumulative series, as (series key, the tag valence it counts); skill and conduct
# are charted on separate axes, and descriptive 'affect' tags are deliberately not scored
SCORED_SERIES: Final[tuple[tuple[str, str], ...]] = (
    ("skill_strong", "strong"),
    ("skill_weak", "weak"),
    ("conduct_cooperative", "cooperative"),
    ("conduct_adversarial", "adversarial"),
    ("conduct_deceptive", "deceptive"),
)

_VALENCE_BY_BEHAVIOR: Final[dict[str, str | None]] = {
    behavior: axis_and_valence(behavior)[1] for behavior in BEHAVIORS
}


def _build_stats(
    steps: list[dict[str, Any]],
    player_names: list[PlayerId],
    messages: list[dict[str, Any]],
    commentary: list[dict[str, Any]],
    combat_totals: dict[PlayerId, dict[str, int]],
    markers: list[dict[str, Any]],
) -> dict[str, Any]:
    message_counts = {name: {"public_messages": 0, "private_messages": 0} for name in player_names}
    for message in messages:
        message_counts[message["sender"]][f"{message['visibility']}_messages"] += 1

    behavior_tag_counts: dict[PlayerId, dict[str, int]] = {name: {} for name in player_names}
    for comment in commentary:
        for tag in comment["tags"]:
            counts = behavior_tag_counts.get(tag["player"])
            if counts is not None:  # the commentator occasionally invents player names; drop those
                counts[tag["behavior"]] = counts.get(tag["behavior"], 0) + 1

    return {
        "series": _end_of_turn_series(steps=steps, player_names=player_names, commentary=commentary),
        "markers": markers,
        "tag_valence": _VALENCE_BY_BEHAVIOR,
        "per_player": [
            {**combat_totals[name], **message_counts[name], "tags": behavior_tag_counts[name]} for name in player_names
        ],
    }


def _end_of_turn_series(
    steps: list[dict[str, Any]], player_names: list[PlayerId], commentary: list[dict[str, Any]]
) -> dict[str, Any]:
    last_step_by_turn: dict[int, dict[str, Any]] = {}
    for step in steps:
        last_step_by_turn[step["turn"]] = step
    turns = sorted(last_step_by_turn)
    sampled_steps = [last_step_by_turn[turn] for turn in turns]
    army_totals = [sum(player["armies"] for player in step["players"]) for step in sampled_steps]
    tag_increments = _valenced_tag_increments(commentary=commentary, player_names=player_names)
    return {
        "turns": turns,
        "players": [
            {
                "armies": [step["players"][seat]["armies"] for step in sampled_steps],
                "army_share": [
                    round(step["players"][seat]["armies"] / total, 4)
                    for step, total in zip(sampled_steps, army_totals)
                ],
                "territories": [step["players"][seat]["territories"] for step in sampled_steps],
                "income": [step["players"][seat]["income"] for step in sampled_steps],
                "cards": [step["players"][seat]["cards"] for step in sampled_steps],
                **{
                    series_key: _cumulative(increments=tag_increments[name], turns=turns, valence=valence)
                    for series_key, valence in SCORED_SERIES
                },
            }
            for seat, name in enumerate(player_names)
        ],
    }


def _valenced_tag_increments(
    commentary: list[dict[str, Any]], player_names: list[PlayerId]
) -> dict[PlayerId, dict[int, dict[str, int]]]:
    increments: dict[PlayerId, dict[int, dict[str, int]]] = {name: {} for name in player_names}
    for comment in commentary:
        for tag in comment["tags"]:
            valence = _VALENCE_BY_BEHAVIOR.get(tag["behavior"])
            player_increments = increments.get(tag["player"])
            if player_increments is None or valence is None:
                continue  # invented player names and descriptive (affect) tags are not scored
            turn_counts = player_increments.setdefault(int(comment["turn"]), {})
            turn_counts[valence] = turn_counts.get(valence, 0) + 1
    return increments


def _cumulative(increments: dict[int, dict[str, int]], turns: list[int], valence: str) -> list[int]:
    totals, running_total = [], 0
    for turn in turns:
        running_total += increments.get(turn, {}).get(valence, 0)
        totals.append(running_total)
    return totals


def _continent_controllers(state: GameState) -> dict[str, PlayerId | None]:
    controllers: dict[str, PlayerId | None] = {}
    for continent in Continent:
        owners = {state.owner[territory] for territory in BOARD.continent_info(continent).territories}
        controllers[str(continent)] = next(iter(owners)) if len(owners) == 1 else None
    return controllers


def _continent_change_markers(
    previous: dict[str, PlayerId | None],
    current: dict[str, PlayerId | None],
    step_index: int,
    turn: int,
) -> list[dict[str, Any]]:
    markers = []
    for continent, controller in current.items():
        former_controller = previous[continent]
        if controller == former_controller:
            continue
        if controller is not None and former_controller is not None:
            player, text = controller, f"{controller} takes {continent} from {former_controller}"
        elif controller is not None:
            player, text = controller, f"{controller} takes control of {continent}"
        else:
            player, text = former_controller, f"{former_controller} loses control of {continent}"
        markers.append({"step": step_index, "turn": turn, "kind": "continent", "player": player, "text": text})
    return markers


def _event_markers(events: list[GameEvent], step_index: int, turn: int) -> list[dict[str, Any]]:
    markers = []
    for event in events:
        match event:
            case PlayerEliminated(eliminated_player=eliminated, eliminated_by=eliminator):
                text = f"{eliminator} eliminates {eliminated}"
                markers.append(
                    {"step": step_index, "turn": turn, "kind": "elimination", "player": eliminator, "text": text}
                )
            case CardsTraded(player_id=player_id, bonus_armies=bonus_armies):
                text = f"{player_id} trades cards for +{bonus_armies} armies"
                markers.append({"step": step_index, "turn": turn, "kind": "trade", "player": player_id, "text": text})
    return markers


def _accumulate_combat_totals(combat_totals: dict[PlayerId, dict[str, int]], events: list[GameEvent]) -> None:
    for event in events:
        match event:
            case BattleResolved(attacker=attacker, defender=defender, outcome=outcome):
                combat_totals[attacker]["armies_lost"] += outcome.attacker_losses
                combat_totals[attacker]["armies_destroyed"] += outcome.defender_losses
                combat_totals[defender]["armies_lost"] += outcome.defender_losses
                combat_totals[defender]["armies_destroyed"] += outcome.attacker_losses
            case TerritoryConquered(player_id=player_id):
                combat_totals[player_id]["conquests"] += 1


def _describe_action(player_id: PlayerId, action: Action) -> str:
    match action:
        case PlaceArmies(territory=territory, count=count):
            return f"{player_id} places {count} armies on {territory}"
        case TradeCards(cards=cards):
            labels = ", ".join(
                "wild" if card.territory is None else f"{card.design}({card.territory})" for card in cards
            )
            return f"{player_id} trades in {labels}"
        case Attack(from_territory=from_territory, to_territory=to_territory, attacker_dice=dice):
            return f"{player_id} attacks {to_territory} from {from_territory} with {dice} dice"
        case Occupy(armies_to_move=armies_to_move):
            return f"{player_id} moves {armies_to_move} armies into the conquered territory"
        case Fortify(from_territory=from_territory, to_territory=to_territory, count=count):
            return f"{player_id} fortifies {count} armies from {from_territory} to {to_territory}"
        case EndAttackPhase():
            return f"{player_id} ends the attack phase"
        case SkipFortify():
            return f"{player_id} ends the turn without fortifying"
