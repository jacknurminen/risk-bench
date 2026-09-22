"""Independent branches from an immutable committed journal prefix."""

import copy
import os
import random
import tempfile
from dataclasses import replace
from pathlib import Path

from risk_bench.game.rules import validate_action
from risk_bench.game.serialization import action_from_json, chat_message_from_json
from risk_bench.game.types import Phase
from risk_bench.runner import checkpoint
from risk_bench.runner.journal import (
    JournalWriter,
    _header_from_json,
    prefix_hash,
    raw_journal,
    select_prefix,
)
from risk_bench.specs import resolve_model


def fork_game(
    source: Path,
    destination: Path,
    cursor: str,
    *,
    instructions: dict[str, str] | None = None,
    controllers: dict[str, str] | None = None,
    redecide: bool = False,
    action: dict | None = None,
    message: dict | None = None,
    continuation_seed: int | None = None,
) -> Path:
    # Read once: a live parent's subsequent growth cannot enter this branch.
    prefix = select_prefix(raw_journal(source), cursor)
    transitions = [r for r in prefix if r["type"] == "transition"]
    if not transitions:
        raise ValueError("Exact forks require execution checkpoints; this legacy cursor is unrecoverable.")
    saved = copy.deepcopy(transitions[-1]["checkpoint"])
    state, rng = checkpoint.restore_engine(saved)
    p = saved["progress"]
    if state.phase is Phase.GAME_OVER or p["stage"] in {"done", "result", "final_commentary"}:
        raise ValueError("Cannot continue a terminal cursor; choose an earlier nonterminal cursor.")
    header = _header_from_json(prefix[0])
    instructions, controllers = instructions or {}, controllers or {}
    unknown = (instructions.keys() | controllers.keys()) - header.player_specs.keys()
    if unknown:
        raise ValueError(f"Unknown player identities: {sorted(unknown)}")
    player_specs = {**header.player_specs, **controllers}
    if list(player_specs.values()).count("human") > 1:
        raise ValueError("Only one human controller is supported")
    for name, spec in controllers.items():
        if spec not in {"human", "random"}:
            resolve_model(spec)
        saved["players"][name].pop("custom", None)
        saved["players"][name].pop("_retry_conversation", None)
        saved["players"][name].pop("_retry_index", None)
        if "rng" not in saved["players"][name]:
            saved["players"][name]["rng"] = checkpoint.encode(random.Random(f"{header.seed}:{name}").getstate())
    for name, text in instructions.items():
        prior = saved["players"][name].get("additional_instructions", "")
        saved["players"][name]["additional_instructions"] = "\n\n".join(filter(None, (prior, text)))
    discarded = None
    if redecide:
        if p["stage"] in {"action", "decision_thought", "decision_delivery", "decision_request"}:
            next_stage = "decision_request"
        elif p["stage"] in {"negotiation_request", "negotiation_thought", "negotiation_delivery"}:
            next_stage = "negotiation_request"
        else:
            raise ValueError("Re-decision requires a decision or negotiation cursor")
        discarded = p["pending"]
        p.pop("action_override_index", None)
        p.pop("message_override_index", None)
        p.update(stage=next_stage, pending=None, action_index=0, rounds_left=None, message_index=0)
        for data in saved["players"].values():
            data["_retry_conversation"] = None
            data["_retry_index"] = 0
    if action is not None:
        if p["stage"] != "action" or p["actor"] != state.current_player:
            raise ValueError("An action override requires an unapplied pending action")
        pending = checkpoint.decode(p["pending"])
        index = p["action_index"]
        if index >= len(pending.actions):
            raise ValueError("There is no pending action to replace")
        replacement = action_from_json(action)
        rejection = validate_action(state, replacement)
        if rejection:
            raise ValueError(rejection)
        actions = list(pending.actions)
        actions[index] = replacement
        p.update(
            pending=checkpoint.encode(replace(pending, actions=tuple(actions))),
            rounds_left=None,
            action_override_index=index,
        )
    if message is not None:
        if p["stage"] not in {"decision_delivery", "negotiation_delivery"}:
            raise ValueError("There is no undelivered message at this cursor")
        pending = checkpoint.decode(p["pending"])
        if p["stage"] == "decision_delivery":
            original = pending.chat_message
        else:
            original = pending.messages[p["message_index"]] if p["message_index"] < len(pending.messages) else None
        if original is None:
            raise ValueError("There is no undelivered message at this cursor")
        replacement = chat_message_from_json(message)
        if (
            replacement.sender != p["actor"]
            or replacement.turn_number != original.turn_number
            or replacement.recipient is not None
            and replacement.recipient not in state.players
        ):
            raise ValueError("Message sender, turn or recipient does not match the pending opportunity")
        if replacement.visibility.value == "private":
            if not header.allow_private_messages or not replacement.recipient or replacement.recipient == p["actor"]:
                raise ValueError("Private messages are unavailable or have an invalid recipient")
        elif replacement.recipient is not None:
            raise ValueError("Public messages cannot have a private recipient")
        if p["stage"] == "decision_delivery":
            pending = replace(pending, chat_message=replacement)
        else:
            messages = list(pending.messages)
            messages[p["message_index"]] = replacement
            pending = replace(pending, messages=tuple(messages))
        p["pending"] = checkpoint.encode(pending)
        p["message_override_index"] = p["message_index"] if p["stage"] == "negotiation_delivery" else 0
    if continuation_seed is not None:
        rng.seed(f"continuation-v1:{continuation_seed}:engine")
        saved["rng"] = checkpoint.encode(rng.getstate())
        for name, data in saved["players"].items():
            data["rng"] = checkpoint.encode(
                random.Random(f"continuation-v1:{continuation_seed}:player:{name}").getstate()
            )
    ancestry = {
        "parent_game_id": source.parent.name,
        "parent_cursor": cursor,
        "parent_prefix_hash": prefix_hash(prefix),
        "root_game_id": header.ancestry.get("root_game_id", source.parent.name),
        "ancestors": [*header.ancestry.get("ancestors", []), source.parent.name],
        "inherited_through": transitions[-1]["id"],
        "randomness": "inherit" if continuation_seed is None else "new-continuation",
        "continuation_seed": continuation_seed,
        "derivation": "continuation-v1",
        "affected_streams": [] if continuation_seed is None else ["engine", *player_specs],
        "interventions": {
            "origin": "human",
            "instructions": instructions,
            "controllers": controllers,
            "redecide": redecide,
            "discarded_pending": discarded,
            "action": action,
            "message": message,
        },
    }
    header = replace(header, player_specs=player_specs, ancestry=ancestry)
    # Exclusive creation protects existing games, and each directory owns its mailbox/trace/results.
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(destination)
    with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".fork-", delete=False) as temporary:
        temporary_path = Path(temporary.name)
    writer = JournalWriter(temporary_path, append=False)
    try:
        writer.write_header(header)
        for record in prefix[1:]:
            writer._write_line(record)
        saved["sequence"] += 1
        writer.commit_transition(saved, "fork_intervention")
        writer.close()
        os.link(temporary_path, destination)  # Atomically publish without overwriting any game.
    finally:
        writer.close()
        temporary_path.unlink(missing_ok=True)
    return destination
