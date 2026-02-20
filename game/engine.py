from __future__ import annotations

import random
import uuid
from typing import Any

from rich.console import Console

from game.board import BOARD, Territory
from game.cards import (
    assign_missions,
    build_deck,
    card_set_bonus,
    check_mission,
    is_valid_set,
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
    def __init__(self, agents: dict[str, Any], verbose: bool) -> None:
        self.agents = agents
        self.verbose = verbose
        self.state: GameState = None  # type: ignore[assignment]
        self._deck: list[str] = []
        self._discard: list[str] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def new_game(self, players: list[str]) -> GameState:
        """Initialize a new game with the given player colors.

        Args:
            players: Ordered list of player color strings defining turn order.

        Returns:
            The freshly initialized GameState, ready for the first reinforce phase.
        """
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

        for player in players:
            state.hands[player] = []

        state.missions = assign_missions(players=players)

        self.state = state
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
            agent = self.agents.get(player)
            if agent is None:
                self._log(f"No agent for {player}, skipping turn")
                self._advance_player()
                continue

            self._start_turn(player=player)

            self._run_reinforce_phase(player=player, agent=agent)
            if state.phase == Phase.GAME_OVER:
                break

            self._run_attack_phase(player=player, agent=agent)
            if state.phase == Phase.GAME_OVER:
                break

            if state.captured_this_turn:
                self._draw_card(player=player)

            self._run_fortify_phase(player=player, agent=agent)
            if state.phase == Phase.GAME_OVER:
                break

            run_reaction_window(state=state, agents=self.agents, event="turn_end", inbox_messages=[])
            self._advance_player()

        if state.phase != Phase.GAME_OVER:
            # Determine winner by territory count at turn limit
            alive = state.alive_players()
            if alive:
                winner = max(alive, key=lambda player: len(state.territories_of(player=player)))
                state.winner = winner
                state.win_reason = "most territories at turn limit"
            state.phase = Phase.GAME_OVER

        return state

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------
    def _setup_board(self) -> None:
        state = self.state
        territories = BOARD.all_territory_names()
        random.shuffle(territories)

        player_count = len(state.players)
        # Initial troops per player based on player count
        initial_troops = {2: 40, 3: 35, 4: 30, 5: 25, 6: 20}.get(player_count, 20)

        # Distribute territories round-robin
        for index, territory in enumerate(territories):
            owner = state.players[index % player_count]
            state.territories[territory] = TerritoryState(owner=owner, troops=1)

        # Track remaining troops to place after initial territory distribution
        placed = {
            player: sum(1 for territory_state in state.territories.values() if territory_state.owner == player)
            for player in state.players
        }
        remaining = {player: initial_troops - placed[player] for player in state.players}

        # Place remaining troops randomly (auto-distribute for setup simplicity)
        for player in state.players:
            owned = state.territories_of(player=player)
            for _ in range(remaining[player]):
                territory = random.choice(owned)
                state.territories[territory].troops += 1

        state.phase = Phase.REINFORCE
        state.turn = 1

    # ------------------------------------------------------------------
    # Reinforce phase
    # ------------------------------------------------------------------
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

        run_reaction_window(state=state, agents=self.agents, event="turn_start", inbox_messages=[])

    def _run_reinforce_phase(self, player: str, agent: Any) -> None:
        state = self.state
        state.phase = Phase.REINFORCE

        # Mandatory card redeem if hand >= 5
        hand = state.hands.get(player, [])
        if len(hand) >= 5:
            self._auto_redeem_cards(player=player)

        while state.troops_to_place > 0:
            move = self._get_move_with_fallback(player=player, agent=agent, phase=Phase.REINFORCE)
            if move.get("action") == "redeem_cards":
                self._apply_card_redeem(player=player, cards=move.get("cards", []))
            else:
                self._apply_reinforce(player=player, move=move)

    def _apply_reinforce(self, player: str, move: dict[str, Any]) -> None:
        state = self.state
        territory = Territory(move["territory"])
        count = move["count"]
        state.territories[territory].troops += count
        state.troops_to_place -= count
        self._log(f"  {player} places {count} troops in {territory}")

    def _apply_card_redeem(self, player: str, cards: list[str]) -> None:
        state = self.state
        try:
            validate_card_redeem(state=state, cards=cards)
        except InvalidMove as error:
            self._log(f"  card redeem failed: {error}")
            return

        owned = state.territories_of(player=player)
        bonus = card_set_bonus(set_count=state.card_set_count) + territory_match_bonus(
            cards=cards, owned_territories=owned
        )
        state.card_set_count += 1
        state.troops_to_place += bonus

        for card in cards:
            state.hands[player].remove(card)
            self._discard.append(card)

        self._log(f"  {player} redeems cards for {bonus} troops")

    def _auto_redeem_cards(self, player: str) -> None:
        hand = self.state.hands.get(player, [])

        for i in range(len(hand)):
            for j in range(i + 1, len(hand)):
                for k in range(j + 1, len(hand)):
                    combo = [hand[i], hand[j], hand[k]]
                    if is_valid_set(cards=combo):
                        self._apply_card_redeem(player=player, cards=combo)
                        return

    # ------------------------------------------------------------------
    # Attack phase
    # ------------------------------------------------------------------
    def _run_attack_phase(self, player: str, agent: Any) -> None:
        state = self.state
        state.phase = Phase.ATTACK

        for _ in range(200):  # max attacks per turn
            move = self._get_move_with_fallback(player=player, agent=agent, phase=Phase.ATTACK)

            if move.get("action") == "end_attacks":
                self._log(f"  {player} ends attacks")
                break

            from_territory = Territory(move["from"])
            to_territory = Territory(move["to"])
            attackers = move["attackers"]

            from_troops = state.territories[from_territory].troops
            to_troops = state.territories[to_territory].troops
            defender = state.territories[to_territory].owner

            remaining_attackers, remaining_defenders, rounds = run_full_attack(
                attacker_troops=from_troops - 1,  # must keep 1
                defender_troops=to_troops,
                attackers=attackers,
            )

            attacker_losses = (from_troops - 1) - remaining_attackers
            defender_losses = to_troops - remaining_defenders

            state.territories[from_territory].troops = remaining_attackers + 1  # restore the 1 kept back

            if remaining_defenders == 0:
                # Attacker captures territory
                move_in = max(1, remaining_attackers)
                if state.territories[from_territory].troops - move_in < 1:
                    move_in = state.territories[from_territory].troops - 1
                state.territories[from_territory].troops -= move_in
                state.territories[to_territory] = TerritoryState(owner=player, troops=move_in)
                state.captured_this_turn = True
                self._log(f"  {player} captures {to_territory} from {defender}! ({move_in} troops moved in)")

                if not state.territories_of(player=defender):
                    self._eliminate_player(killer=player, victim=defender)

                run_reaction_window(state=state, agents=self.agents, event="attack_result", inbox_messages=[])

                if self._check_win(player=player):
                    return
            else:
                state.territories[to_territory].troops = remaining_defenders
                self._log(f"  {player} attacks {to_territory}: atk lost {attacker_losses}, def lost {defender_losses}")
                run_reaction_window(state=state, agents=self.agents, event="attack_result", inbox_messages=[])

            if not self._can_attack(player=player):
                self._log(f"  {player} has no more valid attacks")
                break

    def _can_attack(self, player: str) -> bool:
        state = self.state
        for from_territory, territory_state in state.territories.items():
            if territory_state.owner != player or territory_state.troops < 2:
                continue
            for adjacent_territory in BOARD.territories[from_territory].adjacent:
                if adjacent_territory in state.territories and state.territories[adjacent_territory].owner != player:
                    return True
        return False

    # ------------------------------------------------------------------
    # Fortify phase
    # ------------------------------------------------------------------
    def _run_fortify_phase(self, player: str, agent: Any) -> None:
        state = self.state
        state.phase = Phase.FORTIFY

        move = self._get_move_with_fallback(player=player, agent=agent, phase=Phase.FORTIFY)

        if move.get("action") == "skip":
            return

        from_territory = Territory(move["from"])
        to_territory = Territory(move["to"])
        count = move["count"]

        state.territories[from_territory].troops -= count
        state.territories[to_territory].troops += count
        self._log(f"  {player} fortifies {to_territory} from {from_territory} with {count} troops")

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
        victim_cards = state.hands.get(victim, [])
        state.hands[killer].extend(victim_cards)
        state.hands[victim] = []
        ChatLog(state=state).add(from_color=killer, text=f"I eliminated {victim}!", event="elimination")
        self._log(f"  {killer} eliminated {victim}! Transfers {len(victim_cards)} cards")
        run_reaction_window(state=state, agents=self.agents, event="elimination", inbox_messages=[])

    def _check_win(self, player: str) -> bool:
        state = self.state
        mission = state.missions.get(player, "")
        if check_mission(player=player, mission=mission, state=state):
            state.winner = player
            state.win_reason = f"completed mission: {mission}"
            state.phase = Phase.GAME_OVER
            self._log(f"*** {player} wins! Mission: {mission} ***")
            return True
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
        current_index = state.players.index(state.current_player)
        for offset in range(1, len(state.players) + 1):
            next_player = state.players[(current_index + offset) % len(state.players)]
            if next_player in alive:
                state.current_player = next_player
                state.turn += 1
                break

    def _get_valid_random_move(self, player: str, phase: Phase) -> dict[str, Any]:
        state = self.state

        if phase == Phase.REINFORCE:
            owned = state.territories_of(player=player)
            territory = random.choice(owned)
            return {"territory": territory, "count": state.troops_to_place}

        if phase == Phase.ATTACK:
            valid_attacks = []
            for from_territory, territory_state in state.territories.items():
                if territory_state.owner != player or territory_state.troops < 2:
                    continue
                for adjacent_territory in BOARD.territories[from_territory].adjacent:
                    enemy_territory = state.territories.get(adjacent_territory)
                    if enemy_territory is not None and enemy_territory.owner != player:
                        valid_attacks.append((from_territory, adjacent_territory))
            if not valid_attacks:
                return {"action": "end_attacks"}
            from_territory, to_territory = random.choice(valid_attacks)
            max_attackers = min(3, state.territories[from_territory].troops - 1)
            return {"from": from_territory, "to": to_territory, "attackers": max_attackers}

        if phase == Phase.FORTIFY:
            for from_territory, territory_state in state.territories.items():
                if territory_state.owner != player or territory_state.troops < 2:
                    continue
                for adjacent_territory in BOARD.territories[from_territory].adjacent:
                    friendly_territory = state.territories.get(adjacent_territory)
                    if friendly_territory is not None and friendly_territory.owner == player:
                        return {"from": from_territory, "to": adjacent_territory, "count": territory_state.troops - 1}
            return {"action": "skip"}

        return {"action": "skip"}

    def _get_move_with_fallback(self, player: str, agent: Any, phase: Phase) -> dict[str, Any]:
        state = self.state

        for attempt in range(MAX_RETRIES):
            try:
                move = agent.get_move(state=state, phase=phase)
                validate_move(state=state, move=move)
                return move
            except InvalidMove as error:
                self._log(f"  [{player}] invalid move (attempt {attempt + 1}): {error}")
            except Exception as error:
                self._log(f"  [{player}] agent error (attempt {attempt + 1}): {error}")

        self._log(f"  [{player}] falling back to random move")
        return self._get_valid_random_move(player=player, phase=phase)

    def _log(self, message: str) -> None:
        if self.verbose:
            console.print(message)
