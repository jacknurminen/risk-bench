import random
from dataclasses import dataclass

from risk_bench.chat.diplomacy import ChatMessage, ChatTranscript, MessageVisibility
from risk_bench.game.engine import apply_action, setup_game
from risk_bench.game.events import ArmiesPlaced, GameEvent, TurnLimitReached
from risk_bench.game.missions import Mission
from risk_bench.game.rules import legal_actions
from risk_bench.game.state import GameState
from risk_bench.game.types import Phase, PlayerId
from risk_bench.players.base import DecisionRequest, Player, PlayerView
from risk_bench.players.view import build_player_view


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

    Owns the game flow: each turn starts with a negotiation round (the active
    player may send messages, then every other living player gets one reaction
    opportunity), followed by the decide/apply loop through the turn's phases.
    A game that reaches ``max_turns`` without a completed mission is recorded
    as a draw. Setup placements happen before any negotiation, and their events
    are delivered only to the placing player, keeping starting placements blind.
    When ``allow_private_messages`` is off (the table-game default), private
    messages are silently dropped at the transcript boundary.
    """

    def __init__(
        self,
        players: dict[PlayerId, Player],
        seed: int,
        max_turns: int,
        allow_private_messages: bool,
        verbose: bool,
    ) -> None:
        self._players = players
        self._seed = seed
        self._max_turns = max_turns
        self._allow_private_messages = allow_private_messages
        self._verbose = verbose
        # per-game progress, (re)created by run()
        self._transcript = ChatTranscript()
        self._all_events: list[GameEvent] = []
        self._pending_events: dict[PlayerId, list[GameEvent]] = {}

    def run(self) -> GameRecord:
        """Play the game to completion.

        Returns:
            The full record of the finished game.
        """
        rng = random.Random(self._seed)
        self._transcript = ChatTranscript()
        self._all_events = []
        self._pending_events = {player_id: [] for player_id in self._players}

        state = setup_game(player_ids=list(self._players), rng=rng)
        missions = {player_id: player.mission for player_id, player in state.players.items()}

        last_negotiated_turn = 0
        while state.phase is not Phase.GAME_OVER:
            if state.turn_number > self._max_turns:
                self._record_events(state=state, events=(TurnLimitReached(turn_number=state.turn_number),))
                break
            if state.phase is not Phase.SETUP_PLACEMENT and state.turn_number > last_negotiated_turn:
                last_negotiated_turn = state.turn_number
                self._run_negotiation_round(state=state)
            state = self._decide_and_apply(state=state, rng=rng)

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
        request = DecisionRequest(
            view=self._build_view(state=state, player_id=acting_player),
            legal_actions=legal_actions(state=state),
        )
        action = self._players[acting_player].decide_action(request)
        result = apply_action(state=state, action=action, rng=rng)
        self._record_events(state=state, events=result.events)
        return result.state

    def _record_events(self, state: GameState, events: tuple[GameEvent, ...]) -> None:
        self._all_events.extend(events)
        for event in events:
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
        for player_id in [state.current_player] + living_others:
            view = self._build_view(state=state, player_id=player_id)
            for message in self._players[player_id].compose_messages(view=view):
                if message.visibility is MessageVisibility.PRIVATE and not self._allow_private_messages:
                    continue
                self._transcript.add(message=message)
                if self._verbose:
                    print(message)

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
