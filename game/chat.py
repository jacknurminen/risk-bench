from __future__ import annotations

import random

from game.state import ChatEntry, GameState

# Maximum messages per reaction window before the game continues
REACTION_WINDOW_CAP = 12


class ChatLog:
    """Wrapper around GameState.chat_log with helper methods."""

    def __init__(self, state: GameState) -> None:
        self.state = state

    def add(self, from_color: str, text: str, event: str) -> None:
        """Append a new chat entry to the game log.

        Args:
            from_color: Color of the player sending the message.
            text: Message content.
            event: Event type label (e.g. 'chat', 'elimination', 'turn_start').
        """
        self.state.chat_log.append(ChatEntry(turn=self.state.turn, event=event, from_color=from_color, text=text))

    def recent(self, count: int) -> list[ChatEntry]:
        """Return the most recent chat entries.

        Args:
            count: Maximum number of entries to return.

        Returns:
            List of the last `count` ChatEntry objects.
        """
        return self.state.chat_log[-count:]


def run_reaction_window(
    state: GameState,
    agents: dict[str, object],
    event: str,
    inbox_messages: list[ChatEntry],
) -> None:
    """Open a reaction window where each alive agent may say something or pass.

    The window closes after all agents pass consecutively or REACTION_WINDOW_CAP
    messages have been posted.

    Args:
        state: Current game state.
        agents: Mapping of player color to agent instance.
        event: Event label attached to any messages posted in this window.
        inbox_messages: Additional context messages delivered to agents.
    """
    alive = state.alive_players()
    if len(alive) <= 1:
        return

    order = list(alive)
    random.shuffle(order)

    consecutive_passes = 0
    total_messages = 0
    chat = ChatLog(state=state)

    while consecutive_passes < len(order) and total_messages < REACTION_WINDOW_CAP:
        for color in order:
            if consecutive_passes >= len(order) or total_messages >= REACTION_WINDOW_CAP:
                break
            agent = agents.get(color)
            if agent is None:
                consecutive_passes += 1
                continue

            try:
                messages = agent.get_messages(state, inbox_messages)  # type: ignore[union-attr]
            except Exception:
                messages = []

            if messages:
                for message in messages[:2]:  # at most 2 messages per window turn
                    text = str(message.get("text", "")).strip()[:500]
                    if text:
                        chat.add(from_color=color, text=text, event=event)
                        total_messages += 1
                consecutive_passes = 0
            else:
                consecutive_passes += 1
