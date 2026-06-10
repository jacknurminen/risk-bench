from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass

from game.actions import Attack, EndAttacks, Fortify, PlaceTroops, RedeemCards, Skip, _Action
from game.board import BOARD, Territory
from game.cards import card_set_bonus, check_mission, territory_match_bonus
from game.combat import run_full_attack
from game.state import GameState, Phase, TerritoryState
from game.validator import InvalidMove, validate_card_redeem


@dataclass
class ActionResult:
    """What applying an action produced, for the orchestrator to narrate and act on.

    Fields default to the "nothing happened" value so each action only sets what
    is relevant to it (e.g. only ``attack`` populates combat and capture fields).
    ``ends_phase`` lets the driver loop generically: it keeps asking the current
    player for actions until one reports the phase is finished.
    """

    ends_phase: bool = False
    bonus: int = 0
    attacker_losses: int = 0
    defender_losses: int = 0
    captured: bool = False
    defender: str | None = None
    eliminated: str | None = None
    game_over: bool = False


class Game:
    """The rules of Risk: holds the state and applies the actions agents may take.

    The set of actions is the definition of the game. ``actions_for`` exposes which
    actions are legal in a given phase (the tools to offer), and ``apply`` validates
    and executes one, returning its result. Actions act on ``state.current_player``.
    """

    _ACTIONS_BY_PHASE: dict[Phase, tuple[type[_Action], ...]] = {
        Phase.REINFORCE: (PlaceTroops, RedeemCards),
        Phase.ATTACK: (Attack, EndAttacks),
        Phase.FORTIFY: (Fortify, Skip),
    }

    def __init__(self, state: GameState) -> None:
        self.state = state
        self._handlers: dict[type[_Action], Callable[..., ActionResult]] = {
            PlaceTroops: self._place_troops,
            RedeemCards: self._redeem_cards,
            Attack: self._attack,
            EndAttacks: self._end_attacks,
            Fortify: self._fortify,
            Skip: self._skip,
        }

    def actions_for(self, phase: Phase) -> tuple[type[_Action], ...]:
        """Return the action types that are legal to take in the given phase.

        Args:
            phase: The phase to query.

        Returns:
            The action models a player may choose from (empty if none apply).
        """
        return self._ACTIONS_BY_PHASE.get(phase, ())

    def apply(self, action: _Action) -> ActionResult:
        """Validate and execute an action on behalf of the current player.

        Args:
            action: The action to apply.

        Returns:
            The result of the action.

        Raises:
            InvalidMove: If the action is not legal in the current state.
        """
        return self._handlers[type(action)](action)

    def draw_card(self, player: str) -> str | None:
        """Deal one card to the player from the deck, recycling the discard if needed.

        Args:
            player: The color receiving the card.

        Returns:
            The drawn card, or None if no cards are available anywhere.
        """
        state = self.state
        if not state.deck:
            state.deck = state.discard
            state.discard = []
            random.shuffle(state.deck)
        if not state.deck:
            return None

        card = state.deck.pop()
        state.hands[player].append(card)
        return card

    # --- Reinforce phase ---

    def _place_troops(self, action: PlaceTroops) -> ActionResult:
        state = self.state
        player = state.current_player
        if state.territories[action.territory].owner != player:
            raise InvalidMove(f"reinforce: {action.territory!r} not owned by {player}")
        if action.count > state.troops_to_place:
            raise InvalidMove(f"reinforce: only {state.troops_to_place} troops to place, wanted {action.count}")

        state.territories[action.territory].troops += action.count
        state.troops_to_place -= action.count
        return ActionResult(ends_phase=state.troops_to_place == 0)

    def _redeem_cards(self, action: RedeemCards) -> ActionResult:
        state = self.state
        player = state.current_player
        validate_card_redeem(state=state, cards=action.cards)

        owned = state.territories_of(player=player)
        bonus = card_set_bonus(set_count=state.card_set_count) + territory_match_bonus(
            cards=action.cards, owned_territories=owned
        )
        state.card_set_count += 1
        state.troops_to_place += bonus
        for card in action.cards:
            state.hands[player].remove(card)
            state.discard.append(card)
        return ActionResult(bonus=bonus)

    # --- Attack phase ---

    def _attack(self, action: Attack) -> ActionResult:
        state = self.state
        player = state.current_player
        self._validate_attack(action=action, player=player)

        source = state.territories[action.source]
        target = state.territories[action.target]
        defender = target.owner

        remaining_attackers, remaining_defenders, _ = run_full_attack(
            attacker_troops=source.troops - 1,  # one troop must stay behind
            defender_troops=target.troops,
            attackers=action.armies,
        )
        attacker_losses = (source.troops - 1) - remaining_attackers
        defender_losses = target.troops - remaining_defenders
        source.troops = remaining_attackers + 1

        captured = remaining_defenders == 0
        eliminated: str | None = None
        if captured:
            moved_in = min(remaining_attackers if remaining_attackers > 0 else 1, source.troops - 1)
            source.troops -= moved_in
            state.territories[action.target] = TerritoryState(owner=player, troops=moved_in)
            state.captured_this_turn = True
            if not state.territories_of(player=defender):
                eliminated = self._eliminate(killer=player, victim=defender)
        else:
            target.troops = remaining_defenders

        return ActionResult(
            attacker_losses=attacker_losses,
            defender_losses=defender_losses,
            captured=captured,
            defender=defender,
            eliminated=eliminated,
            game_over=self._check_win(player=player) if captured else False,
        )

    def _end_attacks(self, action: EndAttacks) -> ActionResult:
        return ActionResult(ends_phase=True)

    # --- Fortify phase ---

    def _fortify(self, action: Fortify) -> ActionResult:
        state = self.state
        player = state.current_player
        if state.territories[action.source].owner != player:
            raise InvalidMove(f"fortify: {action.source!r} not owned by {player}")
        if state.territories[action.target].owner != player:
            raise InvalidMove(f"fortify: {action.target!r} not owned by {player}")
        if action.count >= state.territories[action.source].troops:
            raise InvalidMove(f"fortify: must keep at least 1 troop in {action.source!r}")
        if not self._connected_through_owned(start=action.source, end=action.target, player=player):
            raise InvalidMove(f"fortify: {action.source!r} not connected to {action.target!r} through owned territory")

        state.territories[action.source].troops -= action.count
        state.territories[action.target].troops += action.count
        return ActionResult(ends_phase=True)

    def _skip(self, action: Skip) -> ActionResult:
        return ActionResult(ends_phase=True)

    # --- Private helpers ---

    def _validate_attack(self, action: Attack, player: str) -> None:
        state = self.state
        if state.territories[action.source].owner != player:
            raise InvalidMove(f"attack: {action.source!r} not owned by {player}")
        if state.territories[action.target].owner == player:
            raise InvalidMove(f"attack: cannot attack own territory {action.target!r}")
        if not BOARD.are_adjacent(territory=action.source, other_territory=action.target):
            raise InvalidMove(f"attack: {action.source!r} not adjacent to {action.target!r}")
        if action.armies >= state.territories[action.source].troops:
            raise InvalidMove(f"attack: must keep 1 troop in {action.source!r}")

    def _eliminate(self, killer: str, victim: str) -> str:
        state = self.state
        state.hands[killer].extend(state.hands.get(victim, []))
        state.hands[victim] = []
        return victim

    def _check_win(self, player: str) -> bool:
        state = self.state
        mission = state.missions.get(player, "")
        if check_mission(player=player, mission=mission, state=state):
            state.winner = player
            state.win_reason = f"completed mission: {mission}"
            state.phase = Phase.GAME_OVER
            return True

        alive = state.alive_players()
        if len(alive) == 1 and alive[0] == player:
            state.winner = player
            state.win_reason = "last player standing"
            state.phase = Phase.GAME_OVER
            return True
        return False

    def _connected_through_owned(self, start: Territory, end: Territory, player: str) -> bool:
        state = self.state
        visited: set[Territory] = {start}
        queue: list[Territory] = [start]
        while queue:
            current = queue.pop(0)
            for neighbor in BOARD.territories[current].adjacent:
                if neighbor in visited or state.territories[neighbor].owner != player:
                    continue
                if neighbor == end:
                    return True
                visited.add(neighbor)
                queue.append(neighbor)
        return False
