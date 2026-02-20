from __future__ import annotations

import random
from typing import Any

from agents.base import BaseAgent
from game.board import BOARD
from game.state import GameState, Phase


class RandomAgent(BaseAgent):
    """Picks a legal move uniformly at random. Always passes in chat."""

    def get_move(self, state: GameState, phase: Phase) -> dict[str, Any]:
        """Return a random legal move for the current phase.

        Args:
            state: Current game state.
            phase: The phase to generate a move for.

        Returns:
            A move dict appropriate for the given phase.
        """
        if phase == Phase.REINFORCE:
            return self._reinforce(state=state)
        if phase == Phase.ATTACK:
            return self._attack(state=state)
        if phase == Phase.FORTIFY:
            return self._fortify(state=state)
        return {"action": "skip"}

    def get_messages(self, state: GameState, inbox: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return no messages — random agent is always silent.

        Args:
            state: Current game state.
            inbox: Incoming messages from other players.

        Returns:
            Empty list.
        """
        return []

    def _reinforce(self, state: GameState) -> dict[str, Any]:
        owned = state.territories_of(player=self.color)
        territory = random.choice(owned)
        # Place all remaining troops at once
        return {"territory": territory, "count": state.troops_to_place}

    def _attack(self, state: GameState) -> dict[str, Any]:
        # 30% chance to end attacks each decision
        if random.random() < 0.3:
            return {"action": "end_attacks"}

        valid_attacks: list[tuple[str, str]] = []
        for from_territory, territory_state in state.territories.items():
            if territory_state.owner != self.color or territory_state.troops < 2:
                continue
            for adjacent_territory in BOARD.territories[from_territory].adjacent:
                enemy_owned = state.territories.get(adjacent_territory)
                if enemy_owned is not None and enemy_owned.owner != self.color:
                    valid_attacks.append((from_territory, adjacent_territory))

        if not valid_attacks:
            return {"action": "end_attacks"}

        from_territory, to_territory = random.choice(valid_attacks)
        max_attackers = min(3, state.territories[from_territory].troops - 1)
        return {"from": from_territory, "to": to_territory, "attackers": max_attackers}

    def _fortify(self, state: GameState) -> dict[str, Any]:
        # 50% chance to skip fortify
        if random.random() < 0.5:
            return {"action": "skip"}

        candidates: list[tuple[str, str]] = []
        for from_territory, territory_state in state.territories.items():
            if territory_state.owner != self.color or territory_state.troops < 2:
                continue
            for adjacent_territory in BOARD.territories[from_territory].adjacent:
                friendly_owned = state.territories.get(adjacent_territory)
                if friendly_owned is not None and friendly_owned.owner == self.color:
                    candidates.append((from_territory, adjacent_territory))

        if not candidates:
            return {"action": "skip"}

        from_territory, to_territory = random.choice(candidates)
        count = random.randint(1, state.territories[from_territory].troops - 1)
        return {"from": from_territory, "to": to_territory, "count": count}
