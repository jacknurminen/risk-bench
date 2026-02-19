"""Chat log and reaction window logic."""

from __future__ import annotations

import random

from game.state import ChatEntry, GameState

# Maximum messages per reaction window before the game continues
REACTION_WINDOW_CAP = 12


class ChatLog:
    """Wrapper around GameState.chat_log with helper methods."""

    def __init__(self, state: GameState) -> None:
        self.state = state

    def add(self, from_color: str, text: str, event: str = "chat") -> None:
        self.state.chat_log.append(ChatEntry(turn=self.state.turn, event=event, from_color=from_color, text=text))

    def recent(self, n: int = 40) -> list[ChatEntry]:
        return self.state.chat_log[-n:]


def run_reaction_window(
    state: GameState,
    agents: dict[str, object],
    event: str,
    inbox_messages: list[ChatEntry] | None = None,
) -> None:
    """
    Open a reaction window: each alive agent (in random order) may say something or pass.
    Window closes after all agents pass consecutively, or REACTION_WINDOW_CAP messages.
    """
    alive = state.alive_players()
    if len(alive) <= 1:
        return

    order = list(alive)
    random.shuffle(order)

    consecutive_passes = 0
    total_messages = 0
    chat = ChatLog(state)

    while consecutive_passes < len(order) and total_messages < REACTION_WINDOW_CAP:
        for color in order:
            if consecutive_passes >= len(order) or total_messages >= REACTION_WINDOW_CAP:
                break
            agent = agents.get(color)
            if agent is None:
                consecutive_passes += 1
                continue

            inbox = inbox_messages or []
            try:
                messages = agent.get_messages(state, inbox)  # type: ignore[union-attr]
            except Exception:
                messages = []

            if messages:
                for msg in messages[:2]:  # at most 2 messages per window turn
                    text = str(msg.get("text", "")).strip()[:500]
                    if text:
                        chat.add(color, text, event=event)
                        total_messages += 1
                consecutive_passes = 0
            else:
                consecutive_passes += 1
