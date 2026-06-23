import random
from dataclasses import dataclass

from risk_bench.agents.tracing import TraceWriter
from risk_bench.chat.diplomacy import ChatMessage, ChatTranscript, MessageVisibility
from risk_bench.game.engine import apply_action, setup_game
from risk_bench.game.events import ArmiesPlaced, GameEvent, TurnLimitReached
from risk_bench.game.missions import Mission
from risk_bench.game.rules import legal_actions, validate_action
from risk_bench.game.state import GameState
from risk_bench.game.types import Phase, PlayerId
from risk_bench.players.base import DecisionRequest, Player, PlayerView
from risk_bench.players.rendering import narrate_event
from risk_bench.players.view import build_player_view
from risk_bench.runner.commentary import Commentator
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

    Each commentator, when any are given, is invoked after each player's completed
    turn (and once more when the game ends) with the omniscient state and a log of
    the turn; its commentary is journaled and printed but never shown to the
    players, and its failures never affect the game. Multiple commentators each
    apply their own rubric (e.g. skill and conduct) to the same turn.
    """

    def __init__(
        self,
        players: dict[PlayerId, Player],
        seed: int,
        max_turns: int,
        allow_private_messages: bool,
        max_negotiation_rounds: int,
        commentators: tuple[Commentator, ...],
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
        self._journal = journal
        self._trace = trace
        self._verbose = verbose
        # per-game progress, (re)created by run()/resume()
        self._transcript = ChatTranscript()
        self._all_events: list[GameEvent] = []
        self._pending_events: dict[PlayerId, list[GameEvent]] = {}
        self._turn_log: list[str] = []
        # recent commentary kept per rubric, so each commentator only sees its own continuity
        self._previous_commentary: dict[str, list[str]] = {}

    def run(self) -> GameRecord:
        """Play the game from setup to completion.

        Returns:
            The full record of the finished game.
        """
        rng = random.Random(self._seed)
        self._reset_progress(transcript=ChatTranscript())
        state = setup_game(player_ids=list(self._players), rng=rng)
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
        self._pending_events = {player_id: [] for player_id in self._players}
        self._turn_log = []
        self._previous_commentary = {commentator.rubric.name: [] for commentator in self._commentators}

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
            self._turn_log.append(narrate_event(event=event))
            if self._verbose:
                print(event)
            for player_id in self._players:
                if _is_hidden_setup_placement(state=state, event=event, observer=player_id):
                    continue
                self._pending_events[player_id].append(event)

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
        for commentator in self._commentators:
            self._commentate_with(commentator=commentator, state=state, turn_number=turn_number, turn_log=turn_log)

    def _commentate_with(
        self, commentator: Commentator, state: GameState, turn_number: int, turn_log: list[str]
    ) -> None:
        rubric_name = commentator.rubric.name
        if self._trace is not None:
            self._trace.set_context(turn=turn_number, kind=f"commentary:{rubric_name}")
        commentary = commentator.comment_on_turn(
            state=state,
            turn_number=turn_number,
            turn_log=turn_log,
            previous_commentary=self._previous_commentary.get(rubric_name, []),
        )
        if commentary is None:
            return
        kept = (self._previous_commentary.get(rubric_name, []) + [commentary.text])[-_PREVIOUS_COMMENTARY_KEPT:]
        self._previous_commentary[rubric_name] = kept
        if self._journal is not None:
            self._journal.write_commentary(
                turn_number=turn_number,
                rubric=rubric_name,
                text=commentary.text,
                tags=[{"player": tag.player, "behavior": tag.behavior, "note": tag.note} for tag in commentary.tags],
            )
        if self._verbose:
            print(f"[commentary:{rubric_name}] {commentary.text}")
            for tag in commentary.tags:
                print(f"[commentary:{rubric_name}]   {tag.behavior} — {tag.player}: {tag.note}")

    def _build_view(self, state: GameState, player_id: PlayerId) -> PlayerView:
        recent_events = tuple(self._pending_events[player_id])
        self._pending_events[player_id] = []
        return build_player_view(
            state=state,
            player_id=player_id,
            visible_chat=tuple(self._transcript.visible_to(player_id=player_id)),
            recent_events=recent_events,
            private_messages_allowed=self._allow_private_messages,
        )


def _is_hidden_setup_placement(state: GameState, event: GameEvent, observer: PlayerId) -> bool:
    if state.phase is not Phase.SETUP_PLACEMENT:
        return False
    return isinstance(event, ArmiesPlaced) and event.player_id != observer
