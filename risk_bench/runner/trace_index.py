"""The deterministic trace index the evaluator cites and the viewer resolves against.

A game journal is append-only, one record per line, with no per-record id. The
evaluator needs to point at specific places in the trace and have those pointers
be re-derivable on every replay (so two panel members and every re-run cite the
*same* place) and trivially integrity-checkable. The natural handle is therefore
a *derived* ordinal, not a stored random id: ``build_trace_index`` walks the
journal once and numbers every citable line ``0..N-1``. Because the journal only
ever grows, an earlier span's id never moves, so an index built from a paused
game's prefix stays valid after the game resumes.

The index deliberately excludes the inline ``commentary`` records: the live
commentator panel writes its tags into the *same* journal, and the evaluator must
never read another judge's judgments (only the agents' own game data). The four
in-the-moment trace layers plus the agents' notes are what remain.
"""

from dataclasses import dataclass, replace
from typing import Any, Final

from risk_bench.chat.diplomacy import ChatMessage, MessageVisibility
from risk_bench.game.events import PlayerEliminated, TurnStarted
from risk_bench.game.serialization import action_from_json, chat_message_from_json, event_from_json
from risk_bench.game.types import PlayerId
from risk_bench.players.rendering import describe_action, narrate_event

# the record types that contribute a citable span, mapped to the trace layer they render as;
# 'commentary' and 'result' and the header are intentionally absent (never citable)
_THOUGHT: Final[str] = "thought"
_MESSAGE: Final[str] = "message"
_ACTION: Final[str] = "action"
_NOTE: Final[str] = "note"
_EVENT: Final[str] = "event"


@dataclass(frozen=True)
class TraceSpan:
    """One citable line of the trace, addressed by its stable ``span`` ordinal.

    ``turn`` and ``step`` locate the span for the reader: ``turn`` is the game
    turn it belongs to (for hindsight-anchoring checks and turn grouping), and
    ``step`` is the replay step index the viewer shows the board at, so a citation
    resolves to a board position exactly like a commentary line does.
    """

    span: int
    turn: int
    step: int
    layer: str
    player: PlayerId | None
    text: str
    cursor: dict[str, str] | None = None
    inherited: bool = False
    origin: str | None = None


def build_trace_index(records: list[dict[str, Any]]) -> tuple[TraceSpan, ...]:
    """Walk a journal's records into the ordered list of citable spans.

    No engine replay is needed: the turn is tracked from ``TurnStarted`` events
    (and each note's own recorded turn), and the step is the running count of
    applied actions — the same step the viewer assigns, so spans and replay steps
    line up. Header, ``result``, and ``commentary`` records contribute no span.

    Args:
        records: The journal records in file order, as ``read_journal`` returns
            them (the header record may be included; it is skipped).

    Returns:
        The citable spans in journal order, numbered ``0..N-1`` by position.
    """
    spans: list[TraceSpan] = []
    ancestry = records[0].get("ancestry", {}) if records and records[0].get("type") == "header" else {}
    inherited_through = ancestry.get("inherited_through", -1)
    current_turn = 0
    step = 0
    for record in records:
        start = len(spans)
        match record.get("type"):
            case "action":
                step += 1
                action = action_from_json(record["action"])
                _append(
                    spans,
                    turn=current_turn,
                    step=step,
                    layer=_ACTION,
                    player=str(record["player"]),
                    text=describe_action(player_id=str(record["player"]), action=action),
                )
            case "events":
                for event_data in record["events"]:
                    event = event_from_json(event_data)
                    if isinstance(event, TurnStarted):
                        current_turn = event.turn_number
                    _append(
                        spans, turn=current_turn, step=step, layer=_EVENT, player=None, text=narrate_event(event=event)
                    )
            case "message":
                message = chat_message_from_json(record["message"])
                _append(
                    spans,
                    turn=message.turn_number,
                    step=step,
                    layer=_MESSAGE,
                    player=message.sender,
                    text=_render_message(message=message),
                )
            case "thought":
                _append(
                    spans,
                    turn=current_turn,
                    step=step,
                    layer=_THOUGHT,
                    player=str(record["player"]),
                    text=str(record["text"]),
                )
            case "note":
                _append(
                    spans,
                    turn=int(record["turn"]),
                    step=step,
                    layer=_NOTE,
                    player=str(record["player"]),
                    text=str(record["text"]),
                )
            # 'header', 'result', and 'commentary' are not citable and contribute no span
        if record.get("cursor"):
            for index in range(start, len(spans)):
                spans[index] = replace(
                    spans[index],
                    cursor=record["cursor"],
                    inherited=int(record["cursor"]["after"].split(":")[0]) <= inherited_through,
                    origin=record.get("origin"),
                )
    return tuple(spans)


def living_players_by_turn(
    records: list[dict[str, Any]], player_ids: set[PlayerId], turns: list[int]
) -> dict[int, frozenset[PlayerId]]:
    """Map each requested turn to the players still alive on it.

    A player is alive on turn K until the turn it is eliminated on (inclusive: it acted and was
    acted upon during that turn), and gone thereafter. This is the per-turn denominator the
    evaluator's forced consideration enumerates.

    Args:
        records: The journal records in file order, as ``read_journal`` returns them.
        player_ids: Every player that played the game.
        turns: The turns to report living rosters for.

    Returns:
        Each requested turn mapped to the frozenset of players alive on it.
    """
    elimination_turn = _elimination_turns(records=records)
    return {
        turn: frozenset(
            player for player in player_ids if elimination_turn.get(player) is None or elimination_turn[player] >= turn
        )
        for turn in turns
    }


def _elimination_turns(records: list[dict[str, Any]]) -> dict[PlayerId, int]:
    current_turn = 0
    elimination_turn: dict[PlayerId, int] = {}
    for record in records:
        if record.get("type") != "events":
            continue
        for event_data in record["events"]:
            event = event_from_json(event_data)
            if isinstance(event, TurnStarted):
                current_turn = event.turn_number
            elif isinstance(event, PlayerEliminated):
                elimination_turn[event.eliminated_player] = current_turn
    return elimination_turn


def _append(spans: list[TraceSpan], turn: int, step: int, layer: str, player: PlayerId | None, text: str) -> None:
    spans.append(TraceSpan(span=len(spans), turn=turn, step=step, layer=layer, player=player, text=text))


def _render_message(message: ChatMessage) -> str:
    channel = "to everyone" if message.visibility is MessageVisibility.PUBLIC else f"privately to {message.recipient}"
    return f"{message.sender} ({channel}): {message.text}"
