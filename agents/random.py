"""Random-move agent — baseline and fallback."""

from __future__ import annotations

import random
from typing import Any

from agents.base import BaseAgent
from game.board import BOARD
from game.state import GameState, Phase


class RandomAgent(BaseAgent):
    """Picks a legal move uniformly at random. Always passes in chat."""

    def get_move(self, state: GameState, phase: Phase) -> dict[str, Any]:
        if phase == Phase.REINFORCE:
            return self._reinforce(state)
        if phase == Phase.ATTACK:
            return self._attack(state)
        if phase == Phase.FORTIFY:
            return self._fortify(state)
        return {"action": "skip"}

    def _reinforce(self, state: GameState) -> dict[str, Any]:
        owned = state.territories_of(self.color)
        territory = random.choice(owned)
        # Place all remaining troops at once
        return {"territory": territory, "count": state.troops_to_place}

    def _attack(self, state: GameState) -> dict[str, Any]:
        # 30% chance to end attacks each decision
        if random.random() < 0.3:
            return {"action": "end_attacks"}

        valid_attacks: list[tuple[str, str]] = []
        for from_t, ts in state.territories.items():
            if ts.owner != self.color or ts.troops < 2:
                continue
            for adj in BOARD.territories[from_t].adjacent:
                if adj in state.territories and state.territories[adj].owner != self.color:
                    valid_attacks.append((from_t, adj))

        if not valid_attacks:
            return {"action": "end_attacks"}

        from_t, to_t = random.choice(valid_attacks)
        max_atk = min(3, state.territories[from_t].troops - 1)
        return {"from": from_t, "to": to_t, "attackers": max_atk}

    def _fortify(self, state: GameState) -> dict[str, Any]:
        # 50% chance to skip fortify
        if random.random() < 0.5:
            return {"action": "skip"}

        candidates: list[tuple[str, str]] = []
        for from_t, ts in state.territories.items():
            if ts.owner != self.color or ts.troops < 2:
                continue
            for adj in BOARD.territories[from_t].adjacent:
                if adj in state.territories and state.territories[adj].owner == self.color:
                    candidates.append((from_t, adj))

        if not candidates:
            return {"action": "skip"}

        from_t, to_t = random.choice(candidates)
        count = random.randint(1, state.territories[from_t].troops - 1)
        return {"from": from_t, "to": to_t, "count": count}

    def get_messages(self, state: GameState, inbox: list[dict[str, Any]]) -> list[dict[str, Any]]:
        # Random agent is silent
        return []
