import hashlib
import json
import os
import random
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Final

from risk_bench.chat.diplomacy import ChatMessage
from risk_bench.game.actions import Action
from risk_bench.game.engine import setup_game
from risk_bench.game.events import GameEvent
from risk_bench.game.ruleset import CURRENT_RULESET, LEGACY_RULESET, Ruleset
from risk_bench.game.serialization import (
    action_to_json,
    chat_message_from_json,
    chat_message_to_json,
    event_to_json,
)
from risk_bench.game.state import GameState
from risk_bench.game.types import PlayerId
from risk_bench.players.rendering import GAME_RULES
from risk_bench.runner.checkpoint import apply_changes, changes, decode, restore_engine
from risk_bench.version import EXPERIMENT_VERSION, require_v3

# v2 folded the separate occupy action into the attack that conquers; v3 games are played under the
# current ruleset (minimum advance on capture, forced trade during attack), earlier ones under the legacy one
SCHEMA_VERSION: Final[int] = 4
# Storage changes must not move the historical boundary at which game rules changed.
_FIRST_CURRENT_RULESET_SCHEMA_VERSION: Final[int] = 3


@dataclass(frozen=True)
class JournalHeader:
    """The first line of every journal: everything needed to replay or resume the game."""

    schema_version: int
    seed: int
    player_specs: dict[PlayerId, str]  # player name -> CLI spec ('random', 'anthropic:lo', ...), in seat order
    max_turns: int
    allow_private_messages: bool
    max_negotiation_rounds: int
    commentators: list[str]  # the commentator panel: one model spec per member, in panel order (empty for none)

    configuration: dict[str, Any] = field(default_factory=dict)
    ancestry: dict[str, Any] = field(default_factory=dict)
    experiment_version: int = EXPERIMENT_VERSION
    game_rules: str = GAME_RULES


@dataclass
class RestoredGame:
    """A game reconstructed from a journal: the live state plus everything needed to continue."""

    header: JournalHeader
    state: GameState
    rng: random.Random
    messages: list[ChatMessage]
    is_finished: bool
    checkpoint: dict[str, Any] | None = None
    records: list[dict[str, Any]] = field(default_factory=list)


class JournalWriter:
    """Appends one JSON line per game occurrence, flushed immediately.

    A crash at any point leaves a valid prefix on disk, so the game can be
    restored up to its last applied action.
    """

    def __init__(self, path: Path, append: bool) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._pending: list[dict[str, Any]] | None = None
        self._last_checkpoint = None
        self.evidence_context: dict[str, Any] = {}
        if append and path.exists():
            require_v3(raw_journal(path)[0])
            raw_journal(path)  # Reject interior corruption before repairing a torn tail.
            # Remove only a torn final transaction before appending a new committed one.
            raw = path.read_bytes()
            end = 0
            for line in raw.splitlines(keepends=True):
                try:
                    record = json.loads(line)
                    if record.get("type") == "transition" and not line.endswith(b"\n"):
                        break
                except json.JSONDecodeError:
                    break
                end += len(line)
            if end != len(raw):
                with path.open("r+b") as repair:
                    repair.truncate(end)
        if append and path.exists():
            committed = raw_journal(path)
            self._last_checkpoint = next(
                (r["checkpoint"] for r in reversed(committed) if r["type"] == "transition"), None
            )
        self._file = path.open("a" if append else "w", encoding="utf-8")

    def write_header(self, header: JournalHeader) -> None:
        """Write the header line; must be the first write of a fresh journal.

        Args:
            header: The game's replay/resume metadata.
        """
        self._write_line(record={"type": "header", **asdict(header)})

    def write_action(self, player_id: PlayerId, action: Action, origin: str | None = None) -> None:
        """Record an applied action.

        Args:
            player_id: The player who took the action.
            action: The action, already applied to the state.
        """
        self._write_line(
            record={
                "type": "action",
                "player": player_id,
                "action": action_to_json(action),
                **({"origin": origin} if origin else {}),
            }
        )

    def write_events(self, events: tuple[GameEvent, ...]) -> None:
        """Record the events the last action produced (skipped when empty).

        Args:
            events: The events to record.
        """
        if events:
            self._write_line(record={"type": "events", "events": [event_to_json(event) for event in events]})

    def write_message(self, message: ChatMessage, origin: str | None = None) -> None:
        """Record a diplomacy message.

        Args:
            message: The message as accepted into the transcript.
        """
        self._write_line(
            record={
                "type": "message",
                "message": chat_message_to_json(message),
                **({"origin": origin} if origin else {}),
            }
        )

    def write_thought(self, player_id: PlayerId, text: str) -> None:
        """Record a player's private rationale; observational only, never replayed into the game.

        Args:
            player_id: The thinking player.
            text: The rationale as the player stated it.
        """
        self._write_line(record={"type": "thought", "player": player_id, "text": text})

    def write_note(self, player_id: PlayerId, turn_number: int, text: str) -> None:
        """Record a player's rewritten notes after a turn; observational only, never replayed.

        Each version is its own record (the player rewrites in full each turn), so
        the journal keeps the entire history of how a player's notes evolved.

        Args:
            player_id: The player whose notes these are.
            turn_number: The turn that had just ended when the notes were written.
            text: The notes as the player rewrote them.
        """
        self._write_line(record={"type": "note", "player": player_id, "turn": turn_number, "text": text})

    def write_commentary(
        self, turn_number: int, commentator: str, commentator_index: int, text: str, tags: list[dict[str, str]]
    ) -> None:
        """Record one panel member's take on a completed turn; observational only.

        Every member annotates the same turn over the full vocabulary, and each member's record
        is kept separately (never merged) so inter-annotator agreement can be read off the panel.

        Args:
            turn_number: The turn the commentary covers.
            commentator: The member's model spec (its stable panel identity).
            commentator_index: The member's position in the panel roster; the key that
                distinguishes one member's records from another's within a turn.
            text: The commentary prose.
            tags: Structured behavior tags as {player, behavior, note} dicts.
        """
        self._write_line(
            record={
                "type": "commentary",
                "turn": turn_number,
                "commentator": commentator,
                "commentator_index": commentator_index,
                "text": text,
                "tags": tags,
            }
        )

    def write_result(self, winner: PlayerId | None, turn_count: int) -> None:
        """Record the game outcome; marks the journal as finished.

        Args:
            winner: The winning player, or None for a draw.
            turn_count: The turn the game ended on.
        """
        self._write_line(record={"type": "result", "winner": winner, "turn_count": turn_count})

    def close(self) -> None:
        """Close the underlying file."""
        self._file.close()

    def begin_transition(self) -> None:
        self._pending = []

    def commit_transition(self, checkpoint: dict, stage: str) -> None:
        records, self._pending = self._pending or [], None
        self._write_line(
            {
                "type": "transition",
                "id": checkpoint["sequence"],
                "stage": stage,
                "records": records,
                "checkpoint": checkpoint,
            }
        )

    def write_intervention(self, origin: str, details: dict[str, Any]) -> None:
        """Record a human intervention; it commits with the transition that applies it.

        Args:
            origin: Who made the change; 'human' for an experimenter's edit.
            details: What was changed, in plain JSON.
        """
        self._write_line(record={"type": "intervention", "origin": origin, **details})

    def write_configuration_change(self, changes: list[str]) -> None:
        """Record that the game continued under different code than it was started with.

        Args:
            changes: One human-readable line per difference, as ``launch`` reports them.
        """
        self._write_line(record={"type": "configuration_change", "changes": changes})

    def abort_transition(self) -> None:
        self._pending = None

    def _write_line(self, record: dict[str, Any]) -> None:
        if record["type"] in {"thought", "message", "action", "events", "note"}:
            record = {**self.evidence_context, **record}
        if self._pending is not None:
            self._pending.append(record)
            return
        if record["type"] == "transition":
            current = record["checkpoint"]
            if self._last_checkpoint is not None and current["sequence"] % 256:
                delta = {"base": self._last_checkpoint["sequence"], "changes": changes(self._last_checkpoint, current)}
                record = {key: value for key, value in record.items() if key != "checkpoint"}
                record["checkpoint_delta"] = delta
            self._last_checkpoint = current
        self._file.write(json.dumps(record, separators=(",", ":")) + "\n")
        self._file.flush()
        os.fsync(self._file.fileno())


def raw_journal(path: Path) -> list[dict[str, Any]]:
    """Read one consistent committed prefix, ignoring only an incomplete tail."""
    records = []
    previous = None
    lines = path.read_bytes().splitlines(keepends=True)
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            if index != len(lines) - 1:
                raise ValueError("Corrupt journal record before the final line") from None
            break
        if record.get("type") == "transition" and not line.endswith(b"\n"):
            break
        if record.get("type") == "transition":
            if previous is None and record["id"] != 0:
                raise ValueError("Missing initial execution checkpoint")
            if previous is not None and record["id"] != previous["sequence"] + 1:
                raise ValueError("Non-contiguous or duplicated transition ID")
            if "checkpoint_delta" in record:
                delta = record.pop("checkpoint_delta")
                if previous is None or delta["base"] != previous["sequence"]:
                    raise ValueError("Checkpoint delta has no matching committed base")
                record["checkpoint"] = apply_changes(previous, delta["changes"])
            previous = record["checkpoint"]
        records.append(record)
    return records


def expand_records(records: list[dict]) -> list[dict]:
    result = []
    for record in records:
        if record["type"] != "transition":
            result.append(record)
            continue
        saved = record["checkpoint"]
        cursor = {"before": f"{record['id']}:before", "after": f"{record['id']}:after"}
        start = len(result)
        result.extend(
            {**entry, "cursor": cursor, **({"_checkpoint": saved} if entry["type"] == "action" else {})}
            for entry in record["records"]
        )
        result.append({"type": "checkpoint", "checkpoint": saved, "cursor": cursor, "stage": record["stage"]})
        if record["records"] and record["records"][-1]["type"] == "result":
            result.insert(start, result.pop())
    return result


def read_journal(path: Path) -> list[dict[str, Any]]:
    """Expose the traditional journal records plus committed execution checkpoints."""
    records = raw_journal(path)
    if records and records[0].get("type") == "header":
        require_v3(records[0])
    return expand_records(records)


def select_prefix(records: list[dict], cursor: str | None) -> list[dict]:
    if cursor is None:
        return records
    for index, record in enumerate(records):
        if record["type"] == "transition":
            if cursor == f"{record['id']}:after":
                return records[: index + 1]
            if cursor == f"{record['id']}:before" and index > 1:
                return records[:index]
    if any(record["type"] == "transition" for record in records):
        raise ValueError(f"Unknown execution cursor {cursor!r}; initial setup offers only 0:after.")
    if records and records[0].get("schema_version", 0) >= 4:
        raise ValueError("No committed execution checkpoint is available yet.")
    raise ValueError("Cursor unavailable: legacy journals lack execution stages, pending batches and player RNGs.")


def prefix_hash(records: list[dict]) -> str:
    return hashlib.sha256(json.dumps(records, separators=(",", ":"), sort_keys=True).encode()).hexdigest()


def ruleset_for_schema_version(schema_version: int) -> Ruleset:
    """Return the ruleset a journal of the given schema version was played under.

    Rule clauses cannot be migrated on disk the way a record layout can, so a
    replay has to judge the recorded actions by the rules in force at the time.

    Args:
        schema_version: The journal header's schema version.

    Returns:
        The ruleset to replay the journal with.
    """
    if schema_version < _FIRST_CURRENT_RULESET_SCHEMA_VERSION:
        return LEGACY_RULESET
    return CURRENT_RULESET


def restore_game(path: Path, cursor: str | None = None) -> RestoredGame:
    """Restore the selected committed execution checkpoint without provider calls.

    Only v3 checkpoints and fresh header-only games are accepted.

    Args:
        path: The journal file to restore from.

    Returns:
        The restored game, ready to be continued via ``GameRunner.resume``.

    Raises:
        ValueError: If the journal has no header line.
    """
    raw = raw_journal(path)
    if raw and raw[0].get("type") == "header":
        require_v3(raw[0])
    records = expand_records(select_prefix(raw, cursor))
    if not records or records[0].get("type") != "header":
        raise ValueError(f"{path} is not a game journal (missing header line).")
    header = _header_from_json(data=records[0])

    checkpoints = [record["checkpoint"] for record in records if record["type"] == "checkpoint"]
    if checkpoints:
        checkpoint = checkpoints[-1]
        state, rng = restore_engine(checkpoint)
        return RestoredGame(
            header,
            state,
            rng,
            [chat_message_from_json(r["message"]) for r in records if r["type"] == "message"],
            checkpoint["progress"]["stage"] == "done",
            checkpoint,
            records,
        )

    rng = random.Random(header.seed)
    ruleset = ruleset_for_schema_version(schema_version=header.schema_version)
    state = setup_game(player_ids=list(header.player_specs), rng=rng, ruleset=ruleset)
    if len(records) != 1:
        raise ValueError("V3 gameplay requires execution checkpoints; incomplete state cannot be reconstructed.")
    return RestoredGame(header, state, rng, [], False, records=records)


def _header_from_json(data: dict[str, Any]) -> JournalHeader:
    require_v3(data)
    return JournalHeader(
        schema_version=int(data["schema_version"]),
        seed=int(data["seed"]),
        player_specs={str(name): str(spec) for name, spec in data["player_specs"].items()},
        max_turns=int(data["max_turns"]),
        allow_private_messages=bool(data["allow_private_messages"]),
        max_negotiation_rounds=int(data["max_negotiation_rounds"]),
        commentators=[str(spec) for spec in data["commentators"]],
        configuration=data.get("configuration", {}),
        ancestry=data.get("ancestry", {}),
        experiment_version=data["experiment_version"],
        game_rules=data.get("game_rules", GAME_RULES),
    )


def restored_context(game: RestoredGame) -> dict:
    """Restore canonical player knowledge, or empty context before initial setup."""
    if game.checkpoint is not None:
        return decode(game.checkpoint["context"])
    names = list(game.header.player_specs)
    context = {
        "_current_turn_events": {name: [] for name in names},
        "_last_turn_events": {name: [] for name in names},
        "_notes": dict.fromkeys(names),
        "_turn_log": [],
        "_previous_commentary": [[] for _ in game.header.commentators],
    }
    return context
