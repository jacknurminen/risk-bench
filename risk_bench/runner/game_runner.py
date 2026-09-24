import random
import sys
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace

from risk_bench.agents.tracing import TraceWriter
from risk_bench.chat.diplomacy import ChatMessage, ChatTranscript, MessageVisibility
from risk_bench.game.actions import Attack
from risk_bench.game.engine import apply_action, setup_game
from risk_bench.game.events import ArmiesPlaced, GameEvent, TurnLimitReached, TurnStarted
from risk_bench.game.missions import Mission
from risk_bench.game.rules import legal_actions, validate_action
from risk_bench.game.ruleset import CURRENT_RULESET
from risk_bench.game.serialization import event_from_json
from risk_bench.game.state import GameState
from risk_bench.game.types import Phase, PlayerId
from risk_bench.players.base import DecisionRequest, Player, PlayerView
from risk_bench.players.llm import ProviderUnavailableError
from risk_bench.players.rendering import narrate_event
from risk_bench.players.view import build_player_view
from risk_bench.runner import checkpoint
from risk_bench.runner.commentary import Commentary, Commentator
from risk_bench.runner.journal import JournalWriter, RestoredGame, restored_context

_PREVIOUS_COMMENTARY_KEPT = 5


@dataclass(frozen=True)
class GameRecord:
    """The complete, replayable account of one finished game."""

    seed: int
    player_ids: tuple[PlayerId, ...]
    missions: dict[PlayerId, Mission]
    events: tuple[GameEvent, ...]
    chat: tuple[ChatMessage, ...]
    winner: PlayerId | None
    turn_count: int
    invalid_action_counts: dict[PlayerId, int]


class GameRunner:
    """Runs one full game from setup to a completed mission or the turn limit.

    Owns the game flow: each turn starts with a negotiation session of up to
    ``max_negotiation_rounds`` passes. In every pass the active player speaks
    first and each other living player may react; a pass in which nobody says
    anything ends the round, so quiet turns cost one pass while genuine
    exchanges (proposal, counter, acceptance) resolve within a single turn.
    Negotiation is followed by the decide/apply loop through the turn's phases,
    where a decision may carry one broadcast table-talk message alongside its
    action; replies to it have to wait for the next negotiation session.
    A game that reaches ``max_turns`` without a completed mission is recorded
    as a draw. Setup placements happen before any negotiation, and their events
    are delivered only to the placing player, keeping starting placements blind.
    When ``allow_private_messages`` is off (the table-game default), private
    messages are silently dropped at the transcript boundary.

    When a journal is given, every applied action, its events, and every chat
    message are appended to it as they happen, so a crashed game can be
    restored with ``journal.restore_game`` and continued via ``resume``.

    The commentator panel, when any members are given, is invoked after each
    player's completed turn (and once more when the game ends) with the omniscient
    state and a log of the turn. Every member annotates the same turn over the full
    behaviour vocabulary in one pass; members run concurrently (their calls touch no
    game state) but their records are written in panel order, so the journal is
    stable regardless of which member's provider answered first. Each member's
    commentary is journaled and printed but never shown to the players, a member's
    failure yields no record for it that turn without affecting the others, and the
    panel never affects the game.
    """

    def __init__(
        self,
        players: dict[PlayerId, Player],
        additional_context: dict[PlayerId, str],
        seed: int,
        max_turns: int,
        allow_private_messages: bool,
        max_negotiation_rounds: int,
        commentators: tuple[Commentator, ...],
        max_concurrent_commentators: int,
        journal: JournalWriter | None,
        trace: TraceWriter | None,
        verbose: bool,
    ) -> None:
        self._players = players
        # what the game starts each player knowing beyond the board; a resumed game's checkpoint
        # replaces this, and a fork or a later edit adds to it from its own point onward
        self._starting_context = dict(additional_context)
        self._seed = seed
        self._max_turns = max_turns
        self._allow_private_messages = allow_private_messages
        self._max_negotiation_rounds = max_negotiation_rounds
        self._commentators = commentators
        self._max_concurrent_commentators = max_concurrent_commentators
        self._journal = journal
        self._trace = trace
        self._verbose = verbose
        # per-game progress, (re)created by run()/resume()
        self._transcript = ChatTranscript()
        self._all_events: list[GameEvent] = []
        # events are scoped to a single turn: prompts show only the current turn's, and the
        # end-of-turn noting pass reflects on the turn that just finished
        self._current_turn_events: dict[PlayerId, list[GameEvent]] = {}
        self._last_turn_events: dict[PlayerId, list[GameEvent]] = {}
        # delivered messages grouped by the turn they belong to (their own turn_number)
        self._messages_by_turn: dict[int, list[ChatMessage]] = {}
        # each player's latest notes — its only memory of earlier turns, fed back into its prompts
        self._notes: dict[PlayerId, str | None] = {}
        # experimenter-supplied information per player, carried by the checkpoint so a fork's
        # intervention keeps applying to every later prompt it renders
        self._additional_context: dict[PlayerId, str | None] = {}
        self._turn_log: list[str] = []
        # recent commentary kept per panel member (by index), so each only sees its own continuity
        self._previous_commentary: list[list[str]] = []

    def run(self) -> GameRecord:
        """Play the game from setup to completion.

        Returns:
            The full record of the finished game.
        """
        rng = random.Random(self._seed)
        self._reset_progress(transcript=ChatTranscript())
        state = setup_game(player_ids=list(self._players), rng=rng, ruleset=CURRENT_RULESET)
        return self._play(state=state, rng=rng)

    def resume(self, restored: RestoredGame) -> GameRecord:
        """Continue a game restored from a journal until completion.

        Restores the committed operation, pending responses, player streams and context.

        Args:
            restored: The journal-replayed game; its player specs must match
                the players this runner was constructed with.

        Returns:
            The full record of the finished game.

        Raises:
            ValueError: If the restored game is already finished or the
                players do not match.
        """
        if restored.is_finished or (restored.checkpoint is None and restored.state.phase is Phase.GAME_OVER):
            raise ValueError("This game is already finished; nothing to resume.")
        if list(restored.header.player_specs) != list(self._players):
            raise ValueError("The journal's players do not match this runner's players.")

        transcript = ChatTranscript()
        for message in restored.messages:
            transcript.add(message=message)
        self._reset_progress(transcript=transcript)
        self._seed = restored.header.seed
        self._max_turns = restored.header.max_turns
        self._allow_private_messages = restored.header.allow_private_messages
        self._max_negotiation_rounds = restored.header.max_negotiation_rounds
        self._all_events = [
            event_from_json(e) for record in restored.records if record["type"] == "events" for e in record["events"]
        ]
        for name, value in restored_context(restored).items():
            setattr(self, name, value)
        if restored.checkpoint is not None:
            saved = restored.checkpoint
            self._progress = dict(saved["progress"])
            self._sequence = saved["sequence"] + 1
            self._additional_context.update(saved.get("additional_context") or {})
            for name, player in self._players.items():
                checkpoint.restore_player(player, saved["players"][name])
        else:
            self._progress = self._initial_progress(restored.state.turn_number)
            self._sequence = 0
            self._commit(restored.state, restored.rng, "setup")
        return self._play(state=restored.state, rng=restored.rng)

    def _reset_progress(self, transcript: ChatTranscript) -> None:
        if hasattr(self, "_progress"):
            del self._progress
        self._transcript = transcript
        self._all_events = []
        self._current_turn_events = {player_id: [] for player_id in self._players}
        self._last_turn_events = {player_id: [] for player_id in self._players}
        # group any restored messages by turn so a resumed turn's negotiation is shown again
        self._messages_by_turn = {}
        for message in transcript.full_transcript():
            self._messages_by_turn.setdefault(message.turn_number, []).append(message)
        self._notes = {player_id: None for player_id in self._players}
        self._additional_context = {
            player_id: self._starting_context.get(player_id) or None for player_id in self._players
        }
        self._turn_log = []
        # each member keeps its own recent commentary, so its continuity stays independent of the panel
        self._previous_commentary: list[list[str]] = [[] for _ in self._commentators]

    def _initial_progress(self, turn: int = 0) -> dict:
        return {
            "stage": "route",
            "negotiated_turn": turn,
            "commented_turn": turn,
            "pass": 0,
            "speaker": 0,
            "spoke": False,
            "order": [],
            "note_index": 0,
            "pending": None,
            "action_index": 0,
            "rounds_left": None,
            "message_index": 0,
            "actor": None,
        }

    def _checkpoint(self, state: GameState, rng: random.Random) -> dict:
        return {
            "version": checkpoint.VERSION,
            "sequence": self._sequence,
            "runtime": {"python": sys.version.split()[0], "rng": "python-mt19937-v3", "engine": 1},
            "state": checkpoint.encode(state),
            "rng": checkpoint.encode(rng.getstate()),
            "players": {name: checkpoint.player_state(player) for name, player in self._players.items()},
            "progress": dict(self._progress),
            # plain JSON, kept out of the encoded context so every reader can show and edit it
            "additional_context": dict(self._additional_context),
            "context": checkpoint.encode(
                {
                    name: getattr(self, name)
                    for name in (
                        "_current_turn_events",
                        "_last_turn_events",
                        "_notes",
                        "_turn_log",
                        "_previous_commentary",
                    )
                }
            ),
        }

    def _commit(self, state: GameState, rng: random.Random, stage: str) -> None:
        if self._journal is not None:
            self._journal.commit_transition(self._checkpoint(state, rng), stage)
        self._sequence += 1

    def _play(self, state: GameState, rng: random.Random) -> GameRecord:
        if not hasattr(self, "_progress"):
            self._progress = self._initial_progress()
            self._sequence = 0
            self._commit(state, rng, "setup")
        while self._progress["stage"] != "done":
            stage = self._progress["stage"]
            if stage in {"decision_request", "negotiation_request", "note_request"}:
                # Persist this identity across pauses and pending response delivery.
                self._progress.setdefault("decision_id", f"decision-{self._sequence}")
                self._progress.setdefault(
                    "decision_context",
                    {
                        "phase": str(state.phase),
                        "round": state.round_number,
                        "recorded_turn": state.turn_number,
                    },
                )
            if self._journal is not None:
                self._journal.evidence_context = {
                    "decision_id": self._progress.get("decision_id"),
                    **self._progress.get(
                        "decision_context",
                        {
                            "phase": str(state.phase),
                            "round": state.round_number,
                            "recorded_turn": state.turn_number,
                        },
                    ),
                }
            if self._journal is not None:
                self._journal.begin_transition()
            try:
                state = self._advance(state, rng)
            except ProviderUnavailableError as error:
                # No effects from a failed call are published. Preserve its retry conversation,
                # failure counters and exact request position for an ordinary later resume.
                if self._journal is not None:
                    self._journal.abort_transition()
                self._progress["pause_error"] = str(error)
                self._commit(state, rng, "paused:" + stage)
                raise
            except Exception:
                # Unexpected engine/serialization errors leave the last complete transition intact.
                if self._journal is not None:
                    self._journal.abort_transition()
                raise
            self._progress.pop("pause_error", None)
            if self._progress.get("stage") in {
                "route",
                "commentary",
                "decision_request",
                "negotiation_request",
                "note_request",
            }:
                self._progress.pop("decision_id", None)
                self._progress.pop("decision_context", None)
            self._commit(state, rng, stage)
        return GameRecord(
            seed=self._seed,
            player_ids=tuple(self._players),
            missions={name: player.mission for name, player in state.players.items()},
            events=tuple(self._all_events),
            chat=tuple(self._transcript.full_transcript()),
            winner=state.winner,
            turn_count=state.turn_number,
            invalid_action_counts={name: player.invalid_action_count for name, player in self._players.items()},
        )

    def _advance(self, state: GameState, rng: random.Random) -> GameState:
        p = self._progress
        stage = p["stage"]
        if stage == "route":
            if state.phase is Phase.GAME_OVER or (
                state.turn_number > self._max_turns and state.turn_number == p["commented_turn"]
            ):
                if state.phase is not Phase.GAME_OVER:
                    events = (TurnLimitReached(turn_number=state.turn_number),)
                    self._record_events(state, events)
                    if self._journal is not None:
                        self._journal.write_events(events)
                self._turn_log.append(f"The game ended: {state.winner or 'draw'}.")
                p["stage"] = "final_commentary"
            elif state.turn_number != p["commented_turn"]:
                p.update(stage="note_request", note_index=0)
            elif state.phase is not Phase.SETUP_PLACEMENT and state.turn_number > p["negotiated_turn"]:
                order = [state.current_player] + [
                    name
                    for name, player in state.players.items()
                    if player.eliminated_by is None and name != state.current_player
                ]
                p.update(stage="negotiation_request", order=order, speaker=0, **{"pass": 0, "spoke": False})
            else:
                p["stage"] = "decision_request"
        elif stage == "negotiation_request":
            if p["pass"] >= self._max_negotiation_rounds:
                p.update(stage="decision_request", negotiated_turn=state.turn_number)
            else:
                actor = p["order"][p["speaker"]]
                self._set_trace(state.turn_number, "negotiation")
                response = self._players[actor].compose_messages(self._build_view(state, actor))
                p.update(
                    stage="negotiation_thought", pending=checkpoint.encode(response), actor=actor, message_index=0
                )
        elif stage == "negotiation_thought":
            response = checkpoint.decode(p["pending"])
            self._record_thought(p["actor"], response.thought)
            p["stage"] = "negotiation_delivery"
        elif stage == "negotiation_delivery":
            response = checkpoint.decode(p["pending"])
            if p["message_index"] < len(response.messages):
                p["spoke"] |= self._deliver_message(
                    response.messages[p["message_index"]],
                    origin="human" if p.get("message_override_index") == p["message_index"] else None,
                )
                p["message_index"] += 1
            else:
                p["pending"] = None
                p.pop("message_override_index", None)
                p["speaker"] += 1
                p.pop("decision_id", None)
                if p["speaker"] == len(p["order"]):
                    p["pass"] = p["pass"] + 1 if p["spoke"] else self._max_negotiation_rounds
                    p.update(speaker=0, spoke=False)
                p["stage"] = "negotiation_request"
        elif stage == "decision_request":
            actor = state.current_player
            self._set_trace(state.turn_number, "decision")
            response = self._players[actor].decide_action(
                DecisionRequest(view=self._build_view(state, actor), legal_actions=legal_actions(state))
            )
            p.update(
                stage="action", pending=checkpoint.encode(response), actor=actor, action_index=0, rounds_left=None
            )
        elif stage == "action":
            response = checkpoint.decode(p["pending"])
            if (
                state.phase is Phase.GAME_OVER
                or p["action_index"] >= len(response.actions)
                or state.current_player != p["actor"]
            ):
                p["stage"] = "decision_thought"
            else:
                action = response.actions[p["action_index"]]
                continuation = p["rounds_left"] is not None
                if isinstance(action, Attack) and continuation:
                    action = replace(
                        action, attacker_dice=min(action.attacker_dice, state.armies[action.from_territory] - 1)
                    )
                rejection = validate_action(state, action)
                if rejection is not None:
                    p["action_index"] += 1
                    p["rounds_left"] = None
                else:
                    if isinstance(action, Attack):
                        if not continuation:
                            p["rounds_left"] = action.max_attacks
                        action = replace(action, max_attacks=1)
                    result = apply_action(state, action, rng, shared_rng=True)
                    if self._journal is not None:
                        self._journal.write_action(
                            p["actor"],
                            action,
                            origin="human"
                            if p.get("action_override_index") == p["action_index"]
                            or type(self._players[p["actor"]]).__name__ == "HumanPlayer"
                            else "agent_choice",
                        )
                        self._journal.write_events(result.events)
                    self._record_events(state, result.events)
                    state = result.state
                    if isinstance(action, Attack):
                        p["rounds_left"] -= 1
                        if (
                            p["rounds_left"] == 0
                            or state.owner[action.to_territory] == p["actor"]
                            or state.armies[action.from_territory] < 2
                            or state.phase is Phase.GAME_OVER
                        ):
                            p["rounds_left"] = None
                    if p["rounds_left"] is None:
                        p["action_index"] += 1
        elif stage == "decision_thought":
            self._record_thought(p["actor"], checkpoint.decode(p["pending"]).thought)
            p["stage"] = "decision_delivery"
        elif stage == "decision_delivery":
            message = checkpoint.decode(p["pending"]).chat_message
            if message is not None:
                self._deliver_message(message, origin="human" if p.get("message_override_index") == 0 else None)
            p.pop("action_override_index", None)
            p.pop("message_override_index", None)
            p.update(stage="route", pending=None)
            p.pop("decision_id", None)
        elif stage == "note_request":
            names = list(self._players)
            if p["commented_turn"] < 1 or p["note_index"] == len(names):
                p["stage"] = "commentary"
            else:
                actor = names[p["note_index"]]
                if state.players[actor].eliminated_by is not None:
                    p["note_index"] += 1
                else:
                    self._set_trace(p["commented_turn"], "noting")
                    notes = self._players[actor].update_notes(self._noting_view(state, actor, p["commented_turn"]))
                    p.update(stage="note_apply", actor=actor, pending=notes)
        elif stage == "note_apply":
            if p["pending"] is not None:
                self._notes[p["actor"]] = p["pending"]
                if self._journal is not None:
                    self._journal.write_note(p["actor"], p["commented_turn"], p["pending"])
            p.update(stage="note_request", pending=None, note_index=p["note_index"] + 1)
            p.pop("decision_id", None)
        elif stage == "commentary":
            self._commentate(state, p["commented_turn"])
            p.update(stage="route", commented_turn=state.turn_number)
        elif stage == "final_commentary":
            self._commentate(state, state.turn_number)
            p["stage"] = "result"
        elif stage == "result":
            if self._journal is not None:
                self._journal.write_result(state.winner, state.turn_number)
            p["stage"] = "done"
        else:
            raise ValueError(f"Unknown execution stage: {stage}")
        return state

    def _set_trace(self, turn: int, kind: str) -> None:
        if self._trace is not None:
            self._trace.set_context(turn=turn, kind=kind, decision_id=self._progress.get("decision_id"))

    def _record_events(self, state: GameState, events: tuple[GameEvent, ...]) -> None:
        self._all_events.extend(events)
        for event in events:
            # a new turn beginning closes the previous one: stash its events for that turn's
            # noting pass, then start the new turn's buffer fresh (the TurnStarted lands in it)
            if isinstance(event, TurnStarted):
                self._last_turn_events = self._current_turn_events
                self._current_turn_events = {player_id: [] for player_id in self._players}
            self._turn_log.append(narrate_event(event=event))
            if self._verbose:
                print(event)
            for player_id in self._players:
                if _is_hidden_setup_placement(state=state, event=event, observer=player_id):
                    continue
                self._current_turn_events[player_id].append(event)

    def _record_thought(self, player_id: PlayerId, thought: str | None) -> None:
        if thought is None:
            return
        self._turn_log.append(f"{player_id} (thinks): {thought}")
        if self._journal is not None:
            self._journal.write_thought(player_id=player_id, text=thought)
        if self._verbose:
            print(f"({player_id} thinks) {thought}")

    def _deliver_message(self, message: ChatMessage, origin: str | None = None) -> bool:
        if message.visibility is MessageVisibility.PRIVATE and not self._allow_private_messages:
            return False
        channel = f"privately to {message.recipient}" if message.recipient else "to everyone"
        self._turn_log.append(f"{message.sender} ({channel}): {message.text}")
        self._transcript.add(message=message)
        self._messages_by_turn.setdefault(message.turn_number, []).append(message)
        if self._journal is not None:
            self._journal.write_message(message=message, origin=origin)
        if self._verbose:
            print(message)
        return True

    def _commentate(self, state: GameState, turn_number: int) -> None:
        turn_log = self._turn_log
        self._turn_log = []
        if not self._commentators or not turn_log:
            return
        # the whole panel shares one turn and the generic 'commentary' kind; each member's
        # caller distinguishes its trace records, so the context is set once before the fan-out
        if self._trace is not None:
            self._trace.set_context(turn=turn_number, kind="commentary")
        for index, commentary in self._gather_commentary(state=state, turn_number=turn_number, turn_log=turn_log):
            self._record_commentary(index=index, commentary=commentary, turn_number=turn_number)

    def _gather_commentary(
        self, state: GameState, turn_number: int, turn_log: list[str]
    ) -> list[tuple[int, Commentary]]:
        max_workers = min(self._max_concurrent_commentators, len(self._commentators))
        gathered: list[tuple[int, Commentary]] = []
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(
                    commentator.comment_on_turn,
                    state=state,
                    turn_number=turn_number,
                    turn_log=turn_log,
                    # snapshots, read before the fan-out, so concurrent members never share mutable state
                    previous_commentary=list(self._previous_commentary[index]),
                    notes=dict(self._notes),
                ): index
                for index, commentator in enumerate(self._commentators)
            }
            for future in as_completed(futures):
                index = futures[future]
                commentary = self._commentary_result(future=future, index=index)
                if commentary is not None:
                    gathered.append((index, commentary))
        # write in panel order so the journal and every replay are stable regardless of completion order
        gathered.sort(key=lambda item: item[0])
        return gathered

    def _commentary_result(self, future: Future[Commentary | None], index: int) -> Commentary | None:
        try:
            return future.result()
        except Exception as error:  # noqa: BLE001 - one member must never abort the panel
            print(f"[commentator[{index}]-error] {error}", file=sys.stderr)
            return None

    def _record_commentary(self, index: int, commentary: Commentary, turn_number: int) -> None:
        commentator = self._commentators[index]
        kept = (self._previous_commentary[index] + [commentary.text])[-_PREVIOUS_COMMENTARY_KEPT:]
        self._previous_commentary[index] = kept
        if self._journal is not None:
            self._journal.write_commentary(
                turn_number=turn_number,
                commentator=commentator.spec,
                commentator_index=index,
                text=commentary.text,
                tags=[{"player": tag.player, "behavior": tag.behavior, "note": tag.note} for tag in commentary.tags],
            )
        if self._verbose:
            label = f"commentator[{index}] {commentator.spec}"
            print(f"[{label}] {commentary.text}")
            for tag in commentary.tags:
                print(f"[{label}]   {tag.behavior} — {tag.player}: {tag.note}")

    def _build_view(self, state: GameState, player_id: PlayerId) -> PlayerView:
        return build_player_view(
            state=state,
            player_id=player_id,
            visible_chat=self._visible_turn_messages(player_id=player_id, turn_number=state.turn_number),
            recent_events=tuple(self._current_turn_events[player_id]),
            private_messages_allowed=self._allow_private_messages,
            notes=self._notes[player_id],
            additional_context=self._additional_context[player_id],
        )

    def _noting_view(self, state: GameState, player_id: PlayerId, finished_turn: int) -> PlayerView:
        return build_player_view(
            state=state,
            player_id=player_id,
            visible_chat=self._visible_turn_messages(player_id=player_id, turn_number=finished_turn),
            recent_events=tuple(self._last_turn_events[player_id]),
            private_messages_allowed=self._allow_private_messages,
            notes=self._notes[player_id],
            additional_context=self._additional_context[player_id],
        )

    def _visible_turn_messages(self, player_id: PlayerId, turn_number: int) -> tuple[ChatMessage, ...]:
        return tuple(
            message
            for message in self._messages_by_turn.get(turn_number, [])
            if message.visibility is MessageVisibility.PUBLIC or player_id in (message.sender, message.recipient)
        )


def _is_hidden_setup_placement(state: GameState, event: GameEvent, observer: PlayerId) -> bool:
    if state.phase is not Phase.SETUP_PLACEMENT:
        return False
    return isinstance(event, ArmiesPlaced) and event.player_id != observer
