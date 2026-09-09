import random
import sys
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from risk_bench.agents.tracing import TraceWriter
from risk_bench.chat.diplomacy import ChatMessage, ChatTranscript, MessageVisibility
from risk_bench.game.engine import apply_action, setup_game
from risk_bench.game.events import ArmiesPlaced, GameEvent, TurnLimitReached, TurnStarted
from risk_bench.game.missions import Mission
from risk_bench.game.rules import legal_actions, validate_action
from risk_bench.game.ruleset import CURRENT_RULESET
from risk_bench.game.state import GameState
from risk_bench.game.types import Phase, PlayerId
from risk_bench.players.base import DecisionRequest, Player, PlayerView
from risk_bench.players.rendering import narrate_event
from risk_bench.players.view import build_player_view
from risk_bench.runner.commentary import Commentary, Commentator
from risk_bench.runner.journal import JournalWriter, RestoredGame

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

    Owns the game flow: each turn starts with a negotiation round of up to
    ``max_negotiation_rounds`` passes. In every pass the active player speaks
    first and each other living player may react; a pass in which nobody says
    anything ends the round, so quiet turns cost one pass while genuine
    exchanges (proposal, counter, acceptance) resolve within a single turn.
    Negotiation is followed by the decide/apply loop through the turn's phases,
    where a decision may carry one broadcast table-talk message alongside its
    action; replies to it have to wait for the next negotiation round.
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

        The record returned covers the whole game except per-player
        invalid-action counts, which restart at zero on resume.

        Args:
            restored: The journal-replayed game; its player specs must match
                the players this runner was constructed with.

        Returns:
            The full record of the finished game.

        Raises:
            ValueError: If the restored game is already finished or the
                players do not match.
        """
        if restored.is_finished or restored.state.phase is Phase.GAME_OVER:
            raise ValueError("This game is already finished; nothing to resume.")
        if list(restored.header.player_specs) != list(self._players):
            raise ValueError("The journal's players do not match this runner's players.")

        transcript = ChatTranscript()
        for message in restored.messages:
            transcript.add(message=message)
        self._reset_progress(transcript=transcript)
        return self._play(state=restored.state, rng=restored.rng)

    def _reset_progress(self, transcript: ChatTranscript) -> None:
        self._transcript = transcript
        self._all_events = []
        self._current_turn_events = {player_id: [] for player_id in self._players}
        self._last_turn_events = {player_id: [] for player_id in self._players}
        # group any restored messages by turn so a resumed turn's negotiation is shown again
        self._messages_by_turn = {}
        for message in transcript.full_transcript():
            self._messages_by_turn.setdefault(message.turn_number, []).append(message)
        self._notes = {player_id: None for player_id in self._players}
        self._turn_log = []
        # each member keeps its own recent commentary, so its continuity stays independent of the panel
        self._previous_commentary: list[list[str]] = [[] for _ in self._commentators]

    def _play(self, state: GameState, rng: random.Random) -> GameRecord:
        missions = {player_id: player.mission for player_id, player in state.players.items()}

        # on resume this skips re-running the current turn's negotiation round
        last_negotiated_turn = state.turn_number
        commented_turn = state.turn_number
        while state.phase is not Phase.GAME_OVER:
            if state.turn_number > self._max_turns:
                self._record_events(state=state, events=(TurnLimitReached(turn_number=state.turn_number),))
                break
            if state.phase is not Phase.SETUP_PLACEMENT and state.turn_number > last_negotiated_turn:
                last_negotiated_turn = state.turn_number
                self._run_negotiation_round(state=state)
            state = self._decide_and_apply(state=state, rng=rng)
            if state.turn_number != commented_turn and state.phase is not Phase.GAME_OVER:
                # note before commentating so the turn's fresh notes are available to the commentator
                self._run_noting(state=state, finished_turn=commented_turn)
                self._commentate(state=state, turn_number=commented_turn)
                commented_turn = state.turn_number

        outcome = f"The game ended: {state.winner} won." if state.winner else "The game ended in a draw."
        self._turn_log.append(outcome)
        self._commentate(state=state, turn_number=state.turn_number)

        if self._journal is not None:
            self._journal.write_result(winner=state.winner, turn_count=state.turn_number)
        return GameRecord(
            seed=self._seed,
            player_ids=tuple(self._players),
            missions=missions,
            events=tuple(self._all_events),
            chat=tuple(self._transcript.full_transcript()),
            winner=state.winner,
            turn_count=state.turn_number,
            invalid_action_counts={
                player_id: player.invalid_action_count for player_id, player in self._players.items()
            },
        )

    def _decide_and_apply(self, state: GameState, rng: random.Random) -> GameState:
        acting_player = state.current_player
        if self._trace is not None:
            self._trace.set_context(turn=state.turn_number, kind="decision")
        request = DecisionRequest(
            view=self._build_view(state=state, player_id=acting_player),
            legal_actions=legal_actions(state=state),
        )
        decision = self._players[acting_player].decide_action(request)
        for action in decision.actions:
            rejection = validate_action(state=state, action=action)
            if rejection is not None:
                # an earlier action in this batch (e.g. an attack that depleted or captured) made
                # this one illegal; skip it and keep going rather than abort the whole move
                if self._verbose:
                    print(f"[skipped] {acting_player}: {rejection}")
                continue
            result = apply_action(state=state, action=action, rng=rng)
            if self._journal is not None:
                self._journal.write_action(player_id=acting_player, action=action)
                self._journal.write_events(events=result.events)
            self._record_events(state=state, events=result.events)
            state = result.state
            if state.phase is Phase.GAME_OVER:
                break  # an action mid-batch (a winning placement or conquest) can end the game
        self._record_thought(player_id=acting_player, thought=decision.thought)
        if decision.chat_message is not None:
            self._deliver_message(message=decision.chat_message)
        return state

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

    def _run_negotiation_round(self, state: GameState) -> None:
        living_others = [
            player_id
            for player_id, player in state.players.items()
            if player.eliminated_by is None and player_id != state.current_player
        ]
        speaking_order = [state.current_player] + living_others
        if self._trace is not None:
            self._trace.set_context(turn=state.turn_number, kind="negotiation")
        for _ in range(self._max_negotiation_rounds):
            if not self._run_negotiation_pass(state=state, speaking_order=speaking_order):
                return

    def _run_negotiation_pass(self, state: GameState, speaking_order: list[PlayerId]) -> bool:
        anyone_spoke = False
        for player_id in speaking_order:
            view = self._build_view(state=state, player_id=player_id)
            negotiation_turn = self._players[player_id].compose_messages(view=view)
            self._record_thought(player_id=player_id, thought=negotiation_turn.thought)
            for message in negotiation_turn.messages:
                # a dropped private message was heard by nobody, so it must not extend the round
                anyone_spoke |= self._deliver_message(message=message)
        return anyone_spoke

    def _record_thought(self, player_id: PlayerId, thought: str | None) -> None:
        if thought is None:
            return
        self._turn_log.append(f"{player_id} (thinks): {thought}")
        if self._journal is not None:
            self._journal.write_thought(player_id=player_id, text=thought)
        if self._verbose:
            print(f"({player_id} thinks) {thought}")

    def _deliver_message(self, message: ChatMessage) -> bool:
        if message.visibility is MessageVisibility.PRIVATE and not self._allow_private_messages:
            return False
        channel = f"privately to {message.recipient}" if message.recipient else "to everyone"
        self._turn_log.append(f"{message.sender} ({channel}): {message.text}")
        self._transcript.add(message=message)
        self._messages_by_turn.setdefault(message.turn_number, []).append(message)
        if self._journal is not None:
            self._journal.write_message(message=message)
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

    def _run_noting(self, state: GameState, finished_turn: int) -> None:
        if finished_turn < 1:
            return  # turn 0 is blind setup placement: no negotiation and nothing to remember
        for player_id, player in self._players.items():
            if state.players[player_id].eliminated_by is not None:
                continue
            if self._trace is not None:
                self._trace.set_context(turn=finished_turn, kind="noting")
            view = self._noting_view(state=state, player_id=player_id, finished_turn=finished_turn)
            notes = player.update_notes(view)
            if notes is None:
                continue
            self._notes[player_id] = notes
            if self._journal is not None:
                self._journal.write_note(player_id=player_id, turn_number=finished_turn, text=notes)
            if self._verbose:
                print(f"[notes] {player_id} (turn {finished_turn}): {notes}")

    def _build_view(self, state: GameState, player_id: PlayerId) -> PlayerView:
        return build_player_view(
            state=state,
            player_id=player_id,
            visible_chat=self._visible_turn_messages(player_id=player_id, turn_number=state.turn_number),
            recent_events=tuple(self._current_turn_events[player_id]),
            private_messages_allowed=self._allow_private_messages,
            notes=self._notes[player_id],
        )

    def _noting_view(self, state: GameState, player_id: PlayerId, finished_turn: int) -> PlayerView:
        return build_player_view(
            state=state,
            player_id=player_id,
            visible_chat=self._visible_turn_messages(player_id=player_id, turn_number=finished_turn),
            recent_events=tuple(self._last_turn_events[player_id]),
            private_messages_allowed=self._allow_private_messages,
            notes=self._notes[player_id],
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
