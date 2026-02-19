"""Game loop and phase management."""

from __future__ import annotations

import random
import uuid
from typing import Any

from rich.console import Console

from game.board import BOARD
from game.cards import (
    assign_missions,
    build_deck,
    card_set_bonus,
    check_mission,
    territory_match_bonus,
)
from game.chat import ChatLog, run_reaction_window
from game.combat import run_full_attack
from game.state import GameState, Phase, TerritoryState
from game.validator import InvalidMove, validate_card_redeem, validate_move

console = Console()

MAX_TURNS = 500  # hard cap to prevent infinite games
MAX_RETRIES = 3  # retries before falling back to random move


class GameEngine:
    def __init__(
        self,
        agents: dict[str, Any],  # color -> BaseAgent
        verbose: bool = True,
    ) -> None:
        self.agents = agents
        self.verbose = verbose
        self.state: GameState = None  # type: ignore[assignment]
        self._deck: list[str] = []
        self._discard: list[str] = []

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------
    def new_game(self, players: list[str] | None = None) -> GameState:
        """Initialize a new game with the given player colors."""
        if players is None:
            players = list(self.agents.keys())

        game_id = str(uuid.uuid4())
        self._deck = build_deck()
        self._discard = []

        state = GameState(
            game_id=game_id,
            turn=0,
            phase=Phase.SETUP,
            current_player=players[0],
            players=players,
        )

        # Initialize empty hands
        for p in players:
            state.hands[p] = []

        # Assign missions
        state.missions = assign_missions(players)

        self.state = state
        self._setup_board()
        return state

    def _setup_board(self) -> None:
        """Distribute territories randomly and place initial troops."""
        state = self.state
        territories = BOARD.all_territory_names()
        random.shuffle(territories)

        n_players = len(state.players)
        # Initial troops per player based on player count
        initial_troops = {2: 40, 3: 35, 4: 30, 5: 25, 6: 20}.get(n_players, 20)

        # Distribute territories round-robin
        for i, terr in enumerate(territories):
            owner = state.players[i % n_players]
            state.territories[terr] = TerritoryState(owner=owner, troops=1)

        # Track remaining troops to place
        placed = {p: sum(1 for ts in state.territories.values() if ts.owner == p) for p in state.players}
        remaining = {p: initial_troops - placed[p] for p in state.players}

        # Place remaining troops randomly (auto-distribute for setup simplicity)
        for player in state.players:
            owned = state.territories_of(player)
            for _ in range(remaining[player]):
                t = random.choice(owned)
                state.territories[t].troops += 1

        state.phase = Phase.REINFORCE
        state.turn = 1

    # ------------------------------------------------------------------
    # Main game loop
    # ------------------------------------------------------------------
    def run(self) -> GameState:
        """Run the game to completion. Returns final state."""
        state = self.state

        for _ in range(MAX_TURNS):
            if state.phase == Phase.GAME_OVER:
                break

            player = state.current_player
            agent = self.agents.get(player)
            if agent is None:
                self._log(f"No agent for {player}, skipping turn")
                self._advance_player()
                continue

            # Start of turn: calculate reinforcements
            self._start_turn(player)

            # REINFORCE phase
            self._run_reinforce_phase(player, agent)
            if state.phase == Phase.GAME_OVER:
                break

            # ATTACK phase
            self._run_attack_phase(player, agent)
            if state.phase == Phase.GAME_OVER:
                break

            # Award card if captured territory
            if state.captured_this_turn:
                self._draw_card(player)

            # FORTIFY phase
            self._run_fortify_phase(player, agent)
            if state.phase == Phase.GAME_OVER:
                break

            # Post-fortify reaction window
            run_reaction_window(state, self.agents, "turn_end")

            # Advance to next player
            self._advance_player()

        if state.phase != Phase.GAME_OVER:
            # Determine winner by territory count
            alive = state.alive_players()
            if alive:
                winner = max(alive, key=lambda p: len(state.territories_of(p)))
                state.winner = winner
                state.win_reason = "most territories at turn limit"
            state.phase = Phase.GAME_OVER

        return state

    # ------------------------------------------------------------------
    # Reinforce phase
    # ------------------------------------------------------------------
    def _start_turn(self, player: str) -> None:
        state = self.state
        owned = state.territories_of(player)
        owner_map = {n: ts.owner for n, ts in state.territories.items()}

        base = max(3, len(owned) // 3)
        cont_ctrl = BOARD.continent_controller(owner_map)
        cont_bonus = sum(BOARD.continents[c].bonus for c, ctrl in cont_ctrl.items() if ctrl == player)
        state.troops_to_place = base + cont_bonus
        state.captured_this_turn = False
        self._log(f"[Turn {state.turn}] {player} reinforces with {state.troops_to_place} troops")

        # Start-of-turn reaction window
        run_reaction_window(state, self.agents, "turn_start")

    def _run_reinforce_phase(self, player: str, agent: Any) -> None:
        state = self.state
        state.phase = Phase.REINFORCE

        # Mandatory card redeem if hand >= 5
        hand = state.hands.get(player, [])
        if len(hand) >= 5:
            self._auto_redeem_cards(player)

        while state.troops_to_place > 0:
            move = self._get_move_with_fallback(player, agent, Phase.REINFORCE)
            if move.get("action") == "redeem_cards":
                self._apply_card_redeem(player, move.get("cards", []))
            else:
                self._apply_reinforce(player, move)

    def _apply_reinforce(self, player: str, move: dict[str, Any]) -> None:
        state = self.state
        territory = move["territory"]
        count = move["count"]
        state.territories[territory].troops += count
        state.troops_to_place -= count
        self._log(f"  {player} places {count} troops in {territory}")

    def _apply_card_redeem(self, player: str, cards: list[str]) -> None:
        state = self.state
        try:
            validate_card_redeem(state, cards)
        except InvalidMove as e:
            self._log(f"  card redeem failed: {e}")
            return

        owned = state.territories_of(player)
        bonus = card_set_bonus(state.card_set_count) + territory_match_bonus(cards, owned)
        state.card_set_count += 1
        state.troops_to_place += bonus

        for card in cards:
            state.hands[player].remove(card)
            self._discard.append(card)

        self._log(f"  {player} redeems cards for {bonus} troops")

    def _auto_redeem_cards(self, player: str) -> None:
        """Force card redemption when hand >= 5 — pick first valid set."""
        from game.cards import is_valid_set

        hand = self.state.hands.get(player, [])

        for i in range(len(hand)):
            for j in range(i + 1, len(hand)):
                for k in range(j + 1, len(hand)):
                    combo = [hand[i], hand[j], hand[k]]
                    if is_valid_set(combo):
                        self._apply_card_redeem(player, combo)
                        return

    # ------------------------------------------------------------------
    # Attack phase
    # ------------------------------------------------------------------
    def _run_attack_phase(self, player: str, agent: Any) -> None:
        state = self.state
        state.phase = Phase.ATTACK

        for _ in range(200):  # max attacks per turn
            move = self._get_move_with_fallback(player, agent, Phase.ATTACK)

            if move.get("action") == "end_attacks":
                self._log(f"  {player} ends attacks")
                break

            from_t = move["from"]
            to_t = move["to"]
            attackers = move["attackers"]

            from_troops = state.territories[from_t].troops
            to_troops = state.territories[to_t].troops
            defender = state.territories[to_t].owner

            # Run combat
            remaining_atk, remaining_def, rounds = run_full_attack(
                attacker_troops=from_troops - 1,  # must keep 1
                defender_troops=to_troops,
                attackers=attackers,
            )

            atk_losses = (from_troops - 1) - remaining_atk
            def_losses = to_troops - remaining_def

            state.territories[from_t].troops = remaining_atk + 1  # restore the 1 kept back

            if remaining_def == 0:
                # Attacker captures territory
                move_in = max(1, remaining_atk)
                if state.territories[from_t].troops - move_in < 1:
                    move_in = state.territories[from_t].troops - 1
                state.territories[from_t].troops -= move_in
                state.territories[to_t] = TerritoryState(owner=player, troops=move_in)
                state.captured_this_turn = True
                self._log(f"  {player} captures {to_t} from {defender}! ({move_in} troops moved in)")

                # Check if defender is eliminated
                if not state.territories_of(defender):
                    self._eliminate_player(player, defender)

                # Reaction window after capture
                run_reaction_window(state, self.agents, "attack_result")

                # Check win condition
                if self._check_win(player):
                    return
            else:
                state.territories[to_t].troops = remaining_def
                self._log(f"  {player} attacks {to_t}: atk lost {atk_losses}, def lost {def_losses}")
                # Reaction window after significant attack
                run_reaction_window(state, self.agents, "attack_result")

            # Check if player can still attack
            if not self._can_attack(player):
                self._log(f"  {player} has no more valid attacks")
                break

    def _can_attack(self, player: str) -> bool:
        state = self.state
        for from_t, ts in state.territories.items():
            if ts.owner != player or ts.troops < 2:
                continue
            for adj in BOARD.territories[from_t].adjacent:
                if adj in state.territories and state.territories[adj].owner != player:
                    return True
        return False

    # ------------------------------------------------------------------
    # Fortify phase
    # ------------------------------------------------------------------
    def _run_fortify_phase(self, player: str, agent: Any) -> None:
        state = self.state
        state.phase = Phase.FORTIFY

        move = self._get_move_with_fallback(player, agent, Phase.FORTIFY)

        if move.get("action") == "skip":
            return

        from_t = move["from"]
        to_t = move["to"]
        count = move["count"]

        state.territories[from_t].troops -= count
        state.territories[to_t].troops += count
        self._log(f"  {player} fortifies {to_t} from {from_t} with {count} troops")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _draw_card(self, player: str) -> None:
        if not self._deck:
            self._deck = self._discard[:]
            self._discard = []
            random.shuffle(self._deck)
        if self._deck:
            card = self._deck.pop()
            self.state.hands[player].append(card)
            self._log(f"  {player} draws a card")

    def _eliminate_player(self, killer: str, victim: str) -> None:
        state = self.state
        # Transfer victim's cards to killer
        victim_cards = state.hands.get(victim, [])
        state.hands[killer].extend(victim_cards)
        state.hands[victim] = []
        ChatLog(state).add(killer, f"I eliminated {victim}!", event="elimination")
        self._log(f"  {killer} eliminated {victim}! Transfers {len(victim_cards)} cards")
        run_reaction_window(state, self.agents, "elimination")

    def _check_win(self, player: str) -> bool:
        state = self.state
        # Mission win
        mission = state.missions.get(player, "")
        if check_mission(player, mission, state):
            state.winner = player
            state.win_reason = f"completed mission: {mission}"
            state.phase = Phase.GAME_OVER
            self._log(f"*** {player} wins! Mission: {mission} ***")
            return True
        # Last player standing
        alive = state.alive_players()
        if len(alive) == 1 and alive[0] == player:
            state.winner = player
            state.win_reason = "last player standing"
            state.phase = Phase.GAME_OVER
            self._log(f"*** {player} wins! Last standing ***")
            return True
        return False

    def _advance_player(self) -> None:
        state = self.state
        alive = state.alive_players()
        if not alive:
            state.phase = Phase.GAME_OVER
            return
        current_idx = state.players.index(state.current_player)
        for i in range(1, len(state.players) + 1):
            next_player = state.players[(current_idx + i) % len(state.players)]
            if next_player in alive:
                state.current_player = next_player
                state.turn += 1
                break

    def _get_valid_random_move(self, player: str, phase: Phase) -> dict[str, Any]:
        """Generate a random valid move for the given phase."""
        state = self.state

        if phase == Phase.REINFORCE:
            owned = state.territories_of(player)
            territory = random.choice(owned)
            return {"territory": territory, "count": state.troops_to_place}

        if phase == Phase.ATTACK:
            # Find valid attacks
            valid_attacks = []
            for from_t, ts in state.territories.items():
                if ts.owner != player or ts.troops < 2:
                    continue
                for adj in BOARD.territories[from_t].adjacent:
                    if adj in state.territories and state.territories[adj].owner != player:
                        valid_attacks.append((from_t, adj))
            if not valid_attacks:
                return {"action": "end_attacks"}
            from_t, to_t = random.choice(valid_attacks)
            max_atk = min(3, state.territories[from_t].troops - 1)
            return {"from": from_t, "to": to_t, "attackers": max_atk}

        if phase == Phase.FORTIFY:
            # Find valid fortifies
            for from_t, ts in state.territories.items():
                if ts.owner != player or ts.troops < 2:
                    continue
                for adj in BOARD.territories[from_t].adjacent:
                    if adj in state.territories and state.territories[adj].owner == player:
                        return {"from": from_t, "to": adj, "count": ts.troops - 1}
            return {"action": "skip"}

        return {"action": "skip"}

    def _get_move_with_fallback(self, player: str, agent: Any, phase: Phase) -> dict[str, Any]:
        """Try to get a valid move from the agent, falling back to random on failure."""
        state = self.state

        for attempt in range(MAX_RETRIES):
            try:
                move = agent.get_move(state, phase)
                validate_move(state, move)
                return move
            except InvalidMove as e:
                self._log(f"  [{player}] invalid move (attempt {attempt + 1}): {e}")
            except Exception as e:
                self._log(f"  [{player}] agent error (attempt {attempt + 1}): {e}")

        # Fallback
        self._log(f"  [{player}] falling back to random move")
        return self._get_valid_random_move(player, phase)

    def _log(self, msg: str) -> None:
        if self.verbose:
            console.print(msg)
