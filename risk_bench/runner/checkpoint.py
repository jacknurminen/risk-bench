"""Versioned, non-executable continuation data shared by every restoration consumer."""

import base64
import dataclasses
import random
from enum import Enum
from typing import Any

from risk_bench.agents import base as agent_types
from risk_bench.chat import diplomacy
from risk_bench.game import actions, cards, combat, events, missions, ruleset, state, types
from risk_bench.players import base as player_types

VERSION = 1
# Only these data types can be constructed. No imports or executable objects from disk.
_TYPES = {
    cls.__name__: cls
    for module in (
        agent_types,
        diplomacy,
        actions,
        cards,
        combat,
        events,
        missions,
        ruleset,
        state,
        types,
        player_types,
    )
    for cls in vars(module).values()
    if isinstance(cls, type) and (dataclasses.is_dataclass(cls) or issubclass(cls, Enum))
}


def encode(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"$bytes": base64.b64encode(value).decode("ascii")}
    if isinstance(value, Enum):
        return {"$enum": type(value).__name__, "value": value.value}
    if dataclasses.is_dataclass(value):
        return {
            "$type": type(value).__name__,
            "fields": {field.name: encode(getattr(value, field.name)) for field in dataclasses.fields(value)},
        }
    if isinstance(value, cards.Deck):
        return {"$deck": [encode(value._draw_pile), encode(value._discard_pile)]}
    if isinstance(value, dict):
        return {"$map": [[encode(k), encode(v)] for k, v in value.items()]}
    if isinstance(value, (tuple, frozenset)):
        items = sorted(value, key=str) if isinstance(value, frozenset) else value
        return {"$" + type(value).__name__: [encode(v) for v in items]}
    if isinstance(value, list):
        return [encode(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"Unsupported checkpoint value: {type(value).__name__}")


def decode(value: Any, rng: random.Random | None = None) -> Any:
    if isinstance(value, list):
        return [decode(v, rng) for v in value]
    if not isinstance(value, dict):
        return value
    if "$bytes" in value:
        return base64.b64decode(value["$bytes"], validate=True)
    if "$enum" in value:
        return _TYPES[value["$enum"]](value["value"])
    if "$type" in value:
        return _TYPES[value["$type"]](**{k: decode(v, rng) for k, v in value["fields"].items()})
    if "$deck" in value:
        if rng is None:
            raise ValueError("A restored deck requires its engine RNG")
        deck = cards.Deck(decode(value["$deck"][0], rng), rng)
        deck._discard_pile = decode(value["$deck"][1], rng)
        return deck
    if "$map" in value:
        return {decode(k, rng): decode(v, rng) for k, v in value["$map"]}
    if "$tuple" in value:
        return tuple(decode(v, rng) for v in value["$tuple"])
    if "$frozenset" in value:
        return frozenset(decode(v, rng) for v in value["$frozenset"])
    raise ValueError("Unknown checkpoint data type")


PLAYER_FIELDS = (
    "invalid_action_count",
    "_consecutive_provider_failures",
    "_retry_conversation",
    "_retry_index",
    "additional_instructions",
    "_max_invalid_retries",
    "_max_consecutive_provider_failures",
)


def player_state(player: Any) -> dict:
    data = {name: encode(getattr(player, name)) for name in PLAYER_FIELDS if hasattr(player, name)}
    if hasattr(player, "_rng"):
        data["rng"] = encode(player._rng.getstate())
    if hasattr(player, "checkpoint_state"):
        data["custom"] = encode(player.checkpoint_state())
    return data


def restore_player(player: Any, data: dict) -> None:
    for name in PLAYER_FIELDS:
        if name in data:
            setattr(player, name, decode(data[name]))
    if "rng" in data and hasattr(player, "_rng"):
        player._rng.setstate(decode(data["rng"]))
    if "custom" in data:
        if not hasattr(player, "restore_checkpoint_state"):
            raise ValueError("This controller cannot restore its recorded custom state")
        player.restore_checkpoint_state(decode(data["custom"]))


def restore_engine(checkpoint: dict) -> tuple[state.GameState, random.Random]:
    if checkpoint["version"] != VERSION:
        raise ValueError(f"Unsupported execution checkpoint version: {checkpoint['version']}")
    rng = random.Random()
    rng.setstate(decode(checkpoint["rng"]))
    return decode(checkpoint["state"], rng), rng


def changes(previous: Any, current: Any, path: tuple = ()) -> list:
    """JSON replacement patches; no executable patch language or object reconstruction."""
    if previous == current:
        return []
    if isinstance(previous, dict) and isinstance(current, dict) and previous.keys() == current.keys():
        return [change for key in current for change in changes(previous[key], current[key], (*path, key))]
    if isinstance(previous, list) and isinstance(current, list) and len(previous) == len(current):
        return [
            change for index, value in enumerate(current) for change in changes(previous[index], value, (*path, index))
        ]
    return [[list(path), current]]


def apply_changes(previous: dict, patches: list) -> dict:
    def replace_at(value, path, replacement):
        if not path:
            return replacement
        clone = value.copy()
        key = path[0]
        clone[key] = replace_at(value[key], path[1:], replacement)
        return clone

    result = previous
    for path, value in patches:
        result = replace_at(result, path, value)
    return result
