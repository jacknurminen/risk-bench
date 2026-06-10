from __future__ import annotations

import random
import uuid

from game.actions import RedeemCards
from game.board import BOARD
from game.cards import assign_missions, build_deck, is_valid_set
from game.chat import ChatLog, run_reaction_window
from game.game import ActionResult, Game
from game.player import Player
from game.state import GameState, Phase, TerritoryState
from game.validator import InvalidMove
from rich.console import Console

console = Console()

MAX_TURNS = 500  # hard cap to prevent infinite games
MAX_DECISION_STEPS = 5  # attempts at one decision before the game proceeds without the player
MAX_ATTACKS_PER_TURN = 200  # cap on attacks in a single turn


class GameEngine:
    """Drives a full game of Risk.

    The engine owns the turn lifecycle (setup, per-turn reinforcements, card draws,
    turn order, the turn cap). Within each phase it runs one agent loop: it offers
    the legal actions to the current player and applies whatever the player chooses
    via the ``Game``. An illegal or unparseable move is not special-cased — the
    error is fed back to the player as the next observation and it tries again,
    until it succeeds or exhausts its step budget and the game proceeds without it.
    """

    def __init__(self, players: dict[str, Player], verbose: bool) -> None:
        self._players = players
        self._verbose = verbose
        self.state: GameState = None  # type: ignore[assignment]  # set by new_game
        self._game: Game = None  # type: ignore[assignment]  # set by new_game

    def new_game(self, players: list[str]) -> GameState:
        """Initialize a new game with the given player colors.

        Args:
            players: Ordered list of player color strings defining turn order.

        Returns:
            The freshly initialized GameState, ready for the first reinforce phase.
        """
        state = GameState(
            game_id=str(uuid.uuid4()),
            turn=0,
            phase=Phase.SETUP,
            current_player=players[0],
            players=players,
        )
        for player in players:
            state.hands[player] = []
        state.missions = assign_missions(players=players)
        state.deck = build_deck()

        self.state = state
        self._game = Game(state=state)
        self._setup_board()
        return state

    def run(self) -> GameState:
        """Run the game to completion and return the final state.

        Returns:
            GameState with phase=GAME_OVER and winner set.
        """
        state = self.state

        for _ in range(MAX_TURNS):
            if state.phase == Phase.GAME_OVER:
                break
            player = state.current_player
            if player not in self._players:
                self._log(f"No player registered for {player}, skipping turn")
                self._advance_player()
                continue

            self._start_turn(player=player)
            self._run_reinforce(player=player)
            if state.phase == Phase.GAME_OVER:
                break
            self._run_attack(player=player)
            if state.phase == Phase.GAME_OVER:
                break
            if state.captured_this_turn and self._game.draw_card(player=player) is not None:
                self._log(f"  {player} draws a card")
            self._run_fortify(player=player)
            if state.phase == Phase.GAME_OVER:
                break

            run_reaction_window(state=state, players=self._players, event="turn_end", inbox_messages=[])
            self._advance_player()

        if state.phase != Phase.GAME_OVER:
            self._end_at_turn_limit()
        return state

    # --- Phase drivers ---

    def _run_reinforce(self, player: str) -> None:
        self.state.phase = Phase.REINFORCE
        self._auto_redeem_if_required(player=player)
        while self.state.troops_to_place > 0:
            result = self._resolve_action(player=player, phase=Phase.REINFORCE)
            if result is None:
                self._auto_place_remaining(player=player)
                return
            if result.bonus:
                self._log(f"  {player} redeems cards for {result.bonus} troops")

    def _run_attack(self, player: str) -> None:
        self.state.phase = Phase.ATTACK
        for _ in range(MAX_ATTACKS_PER_TURN):
            result = self._resolve_action(player=player, phase=Phase.ATTACK)
            if result is None or result.ends_phase or result.game_over:
                return
            self._narrate_attack(player=player, result=result)

    def _run_fortify(self, player: str) -> None:
        self.state.phase = Phase.FORTIFY
        self._resolve_action(player=player, phase=Phase.FORTIFY)

    def _resolve_action(self, player: str, phase: Phase) -> ActionResult | None:
        chosen_player = self._players[player]
        actions = self._game.actions_for(phase=phase)
        error: str | None = None

        for _ in range(MAX_DECISION_STEPS):
            action = chosen_player.act(state=self.state, actions=actions, previous_error=error)
            if action is None:
                error = "You must respond by calling exactly one of the available tools."
                continue
            try:
                result = self._game.apply(action=action)
            except InvalidMove as invalid:
                error = str(invalid)
                self._log(f"  [{player}] illegal move: {invalid}")
                continue
            return result

        self._log(f"  [{player}] no legal move after {MAX_DECISION_STEPS} attempts; proceeding without it")
        return None

    # --- Attack narration and chat ---

    def _narrate_attack(self, player: str, result: ActionResult) -> None:
        state = self.state
        if not result.captured:
            self._log(f"  {player} attacks: lost {result.attacker_losses}, defender lost {result.defender_losses}")
            run_reaction_window(state=state, players=self._players, event="attack_result", inbox_messages=[])
            return

        self._log(f"  {player} captures a territory from {result.defender}")
        if result.eliminated is not None:
            ChatLog(state=state).add(from_color=player, text=f"I eliminated {result.eliminated}!", event="elimination")
            self._log(f"  {player} eliminated {result.eliminated}!")
            run_reaction_window(state=state, players=self._players, event="elimination", inbox_messages=[])
            return
        run_reaction_window(state=state, players=self._players, event="attack_result", inbox_messages=[])

    # --- Turn lifecycle ---

    def _start_turn(self, player: str) -> None:
        state = self.state
        owned = state.territories_of(player=player)
        owner_map = {name: territory_state.owner for name, territory_state in state.territories.items()}

        base_troops = max(3, len(owned) // 3)
        continent_control = BOARD.continent_controller(owner_map=owner_map)
        continent_bonus = sum(
            BOARD.continents[continent].bonus
            for continent, controller in continent_control.items()
            if controller == player
        )
        state.troops_to_place = base_troops + continent_bonus
        state.captured_this_turn = False
        self._log(f"[Turn {state.turn}] {player} reinforces with {state.troops_to_place} troops")

        run_reaction_window(state=state, players=self._players, event="turn_start", inbox_messages=[])

    def _advance_player(self) -> None:
        state = self.state
        alive = state.alive_players()
        if not alive:
            state.phase = Phase.GAME_OVER
            return
        current_index = state.players.index(state.current_player)
        for offset in range(1, len(state.players) + 1):
            next_player = state.players[(current_index + offset) % len(state.players)]
            if next_player in alive:
                state.current_player = next_player
                state.turn += 1
                break

    def _end_at_turn_limit(self) -> None:
        state = self.state
        alive = state.alive_players()
        if alive:
            winner = max(alive, key=lambda player: len(state.territories_of(player=player)))
            state.winner = winner
            state.win_reason = "most territories at turn limit"
        state.phase = Phase.GAME_OVER

    # --- Reinforce helpers ---

    def _auto_redeem_if_required(self, player: str) -> None:
        state = self.state
        while len(state.hands.get(player, [])) >= 5:
            combo = self._find_valid_set(hand=state.hands[player])
            if combo is None:
                return
            self._game.apply(action=RedeemCards(cards=combo))
            self._log(f"  {player} auto-redeems a card set (hand was full)")

    def _auto_place_remaining(self, player: str) -> None:
        state = self.state
        owned = state.territories_of(player=player)
        target = max(owned, key=lambda territory: state.territories[territory].troops)
        self._log(f"  {player} forfeited placement; {state.troops_to_place} troops auto-placed on {target}")
        state.territories[target].troops += state.troops_to_place
        state.troops_to_place = 0

    def _find_valid_set(self, hand: list[str]) -> list[str] | None:
        for first in range(len(hand)):
            for second in range(first + 1, len(hand)):
                for third in range(second + 1, len(hand)):
                    combo = [hand[first], hand[second], hand[third]]
                    if is_valid_set(cards=combo):
                        return combo
        return None

    # --- Setup ---

    def _setup_board(self) -> None:
        state = self.state
        territories = BOARD.all_territory_names()
        random.shuffle(territories)

        player_count = len(state.players)
        initial_troops = {2: 40, 3: 35, 4: 30, 5: 25, 6: 20}.get(player_count, 20)

        for index, territory in enumerate(territories):
            owner = state.players[index % player_count]
            state.territories[territory] = TerritoryState(owner=owner, troops=1)

        placed = {
            player: sum(1 for territory_state in state.territories.values() if territory_state.owner == player)
            for player in state.players
        }
        remaining = {player: initial_troops - placed[player] for player in state.players}

        for player in state.players:
            owned = state.territories_of(player=player)
            for _ in range(remaining[player]):
                territory = random.choice(owned)
                state.territories[territory].troops += 1

        state.phase = Phase.REINFORCE
        state.turn = 1

    def _log(self, message: str) -> None:
        if self._verbose:
            console.print(message)
