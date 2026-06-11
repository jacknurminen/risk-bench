import random
from pathlib import Path
from typing import Any

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
from risk_bench.game.rules import reinforcement_count
from risk_bench.game.serialization import action_from_json, event_from_json
from risk_bench.game.state import GameState
from risk_bench.game.types import Continent, PlayerId, Territory
from risk_bench.players.rendering import narrate_event
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

    player_names: list[PlayerId] = list(header["player_specs"])
    rng = random.Random(int(header["seed"]))
    state = setup_game(player_ids=player_names, rng=rng)

    steps = [_step(state=state, player_names=player_names, action_text=None, events=[])]
    messages = []
    result = None
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
            case "events":
                steps[-1]["events"] = [narrate_event(event=event_from_json(data)) for data in record["events"]]
            case "message":
                messages.append({**record["message"], "step": len(steps) - 1})
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
        "result": result,
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
