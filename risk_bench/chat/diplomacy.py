from dataclasses import dataclass
from enum import StrEnum

from risk_bench.game.types import PlayerId


class MessageVisibility(StrEnum):
    PUBLIC = "public"
    PRIVATE = "private"


@dataclass(frozen=True)
class ChatMessage:
    """One diplomacy message; ``recipient`` is set only for private messages."""

    turn_number: int
    sender: PlayerId
    visibility: MessageVisibility
    recipient: PlayerId | None
    text: str


class ChatTranscript:
    """The full chat history of one game, with per-player visibility filtering."""

    def __init__(self) -> None:
        self._messages: list[ChatMessage] = []

    def add(self, message: ChatMessage) -> None:
        """Append a message to the transcript.

        Args:
            message: The message to record.
        """
        self._messages.append(message)

    def visible_to(self, player_id: PlayerId) -> list[ChatMessage]:
        """Return the messages the given player is allowed to see, oldest first.

        Public messages plus private messages the player sent or received.

        Args:
            player_id: The player whose view to build.

        Returns:
            The visible messages in chronological order.
        """
        return [
            message
            for message in self._messages
            if message.visibility is MessageVisibility.PUBLIC or player_id in (message.sender, message.recipient)
        ]

    def full_transcript(self) -> list[ChatMessage]:
        """Return every message including private ones, for game records and analysis."""
        return list(self._messages)
