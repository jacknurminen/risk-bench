import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from risk_bench.chat.diplomacy import ChatMessage
from risk_bench.game.actions import Action
from risk_bench.game.engine import apply_action, setup_game
from risk_bench.game.events import GameEvent
from risk_bench.game.serialization import (
    action_from_json,
    action_to_json,
    chat_message_from_json,
    chat_message_to_json,
    event_to_json,
)
from risk_bench.game.state import GameState
from risk_bench.game.types import PlayerId

SCHEMA_VERSION: Final[int] = 1


@dataclass(frozen=True)
class JournalHeader:
    """The first line of every journal: everything needed to replay or resume the game."""

    schema_version: int
    seed: int
    player_specs: dict[PlayerId, str]  # player name -> CLI spec ('random', 'anthropic:lo', ...), in seat order
    max_turns: int
    allow_private_messages: bool
    max_negotiation_rounds: int


@dataclass
class RestoredGame:
    """A game reconstructed from a journal: the live state plus everything needed to continue."""

    header: JournalHeader
    state: GameState
    rng: random.Random
    messages: list[ChatMessage]
    is_finished: bool


class JournalWriter:
    """Appends one JSON line per game occurrence, flushed immediately.

    A crash at any point leaves a valid prefix on disk, so the game can be
    restored up to its last applied action.
    """

    def __init__(self, path: Path, append: bool) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._file = path.open("a" if append else "w", encoding="utf-8")

    def write_header(self, header: JournalHeader) -> None:
        """Write the header line; must be the first write of a fresh journal.

        Args:
            header: The game's replay/resume metadata.
        """
        self._write_line(
            record={
                "type": "header",
                "schema_version": header.schema_version,
                "seed": header.seed,
                "player_specs": header.player_specs,
                "max_turns": header.max_turns,
                "allow_private_messages": header.allow_private_messages,
                "max_negotiation_rounds": header.max_negotiation_rounds,
            }
        )

    def write_action(self, player_id: PlayerId, action: Action) -> None:
        """Record an applied action.

        Args:
            player_id: The player who took the action.
            action: The action, already applied to the state.
        """
        self._write_line(record={"type": "action", "player": player_id, "action": action_to_json(action)})

    def write_events(self, events: tuple[GameEvent, ...]) -> None:
        """Record the events the last action produced (skipped when empty).

        Args:
            events: The events to record.
        """
        if events:
            self._write_line(record={"type": "events", "events": [event_to_json(event) for event in events]})

    def write_message(self, message: ChatMessage) -> None:
        """Record a diplomacy message.

        Args:
            message: The message as accepted into the transcript.
        """
        self._write_line(record={"type": "message", "message": chat_message_to_json(message)})

    def write_thought(self, player_id: PlayerId, text: str) -> None:
        """Record a player's private rationale; observational only, never replayed into the game.

        Args:
            player_id: The thinking player.
            text: The rationale as the player stated it.
        """
        self._write_line(record={"type": "thought", "player": player_id, "text": text})

    def write_commentary(self, turn_number: int, rubric: str, text: str, tags: list[dict[str, str]]) -> None:
        """Record one commentator's take on a completed turn; observational only.

        Args:
            turn_number: The turn the commentary covers.
            rubric: The rubric this commentary was produced under (e.g. 'skill', 'conduct').
            text: The commentary prose.
            tags: Structured behavior tags as {player, behavior, note} dicts.
        """
        self._write_line(
            record={"type": "commentary", "turn": turn_number, "rubric": rubric, "text": text, "tags": tags}
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

    def _write_line(self, record: dict[str, Any]) -> None:
        self._file.write(json.dumps(record) + "\n")
        self._file.flush()


def read_journal(path: Path) -> list[dict[str, Any]]:
    """Read a journal's records, tolerating a truncated final line after a crash.

    Args:
        path: The journal file to read.

    Returns:
        The parsed records in file order.
    """
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            break  # a crash mid-write leaves at most one partial trailing line
    return records


def restore_game(path: Path) -> RestoredGame:
    """Reconstruct a game's exact state by replaying the journal's actions.

    The engine is a pure seed-deterministic reducer, so replaying the header's
    seed and the recorded actions through it lands on the same state (and the
    same rng stream position) the game crashed in.

    Args:
        path: The journal file to restore from.

    Returns:
        The restored game, ready to be continued via ``GameRunner.resume``.

    Raises:
        ValueError: If the journal has no header line.
    """
    records = read_journal(path=path)
    if not records or records[0].get("type") != "header":
        raise ValueError(f"{path} is not a game journal (missing header line).")
    header = _header_from_json(data=records[0])

    rng = random.Random(header.seed)
    state = setup_game(player_ids=list(header.player_specs), rng=rng)
    messages = []
    is_finished = False
    for record in records[1:]:
        match record["type"]:
            case "action":
                action = action_from_json(record["action"])
                state = apply_action(state=state, action=action, rng=rng).state
            case "message":
                messages.append(chat_message_from_json(record["message"]))
            case "result":
                is_finished = True

    return RestoredGame(header=header, state=state, rng=rng, messages=messages, is_finished=is_finished)


def _header_from_json(data: dict[str, Any]) -> JournalHeader:
    return JournalHeader(
        schema_version=int(data["schema_version"]),
        seed=int(data["seed"]),
        player_specs={str(name): str(spec) for name, spec in data["player_specs"].items()},
        max_turns=int(data["max_turns"]),
        allow_private_messages=bool(data["allow_private_messages"]),
        # journals written before multi-pass negotiation ran exactly one pass per turn
        max_negotiation_rounds=int(data.get("max_negotiation_rounds", 1)),
    )
