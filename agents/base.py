from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from game.state import GameState, Phase


class BaseAgent(ABC):
    """Abstract base class for all Risk agents."""

    def __init__(self, color: str, name: str) -> None:
        self.color = color
        self.name = name

    @abstractmethod
    def get_move(self, state: GameState, phase: Phase) -> dict[str, Any]:
        """
        Return a move dict for the current phase.
        - REINFORCE: {"territory": str, "count": int}  or {"action": "redeem_cards", "cards": [...]}
        - ATTACK:    {"from": str, "to": str, "attackers": int}  or {"action": "end_attacks"}
        - FORTIFY:   {"from": str, "to": str, "count": int}  or {"action": "skip"}
        """
        ...

    def get_messages(self, state: GameState, inbox: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Optionally return messages to post to the group chat.
        Return a list of {"text": str} dicts, or an empty list to pass.
        Default implementation: always pass.
        """
        return []

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(color={self.color!r}, name={self.name!r})"
