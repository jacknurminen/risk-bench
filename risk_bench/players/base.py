from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from risk_bench.chat.diplomacy import ChatMessage
from risk_bench.game.actions import Action
from risk_bench.game.cards import TerritoryCard
from risk_bench.game.events import GameEvent
from risk_bench.game.missions import Mission
from risk_bench.game.types import Phase, PlayerId, Territory


@dataclass(frozen=True)
class PublicPlayerInfo:
    """What everyone may know about a player: counts, not contents."""

    player_id: PlayerId
    territory_count: int
    army_count: int
    card_count: int
    is_eliminated: bool


@dataclass(frozen=True)
class PlayerView:
    """Everything one player may legally know about the game.

    The board (ownership and army counts) is fully public; cards and missions
    are private to their holder. During the SETUP_PLACEMENT phase other players'
    army counts are masked to the dealt baseline of 1, so starting placements
    are decided blind. Built exclusively by ``players.view`` so information
    hiding is enforced in one auditable place.
    """

    # identity and game flow
    player_id: PlayerId
    phase: Phase
    turn_number: int
    current_player: PlayerId
    private_messages_allowed: bool
    # public board state
    armies: dict[Territory, int]
    owner: dict[Territory, PlayerId]
    players: tuple[PublicPlayerInfo, ...]
    # private information
    own_cards: tuple[TerritoryCard, ...]
    own_mission: Mission
    armies_to_place: int
    # history
    visible_chat: tuple[ChatMessage, ...]
    recent_events: tuple[GameEvent, ...]


@dataclass(frozen=True)
class DecisionRequest:
    """One decision point: the player's view plus the actions they may take."""

    view: PlayerView
    legal_actions: list[Action]


@runtime_checkable
class Player(Protocol):
    """A game-playing strategy: decides actions and composes diplomacy messages.

    Implementations never see ``GameState`` directly — only ``PlayerView``, so
    they cannot act on hidden information. ``invalid_action_count`` tracks how
    often the implementation failed to produce a legal action and a fallback
    was substituted — a benchmark signal in itself.
    """

    invalid_action_count: int

    def decide_action(self, request: DecisionRequest) -> Action:
        """Choose one of the legal actions at this decision point.

        Args:
            request: The player's view and the legal actions.

        Returns:
            The chosen action; must be among (or within the bounds of) the legal ones.
        """
        ...

    def compose_messages(self, view: PlayerView) -> list[ChatMessage]:
        """Compose diplomacy messages for the current negotiation opportunity.

        Args:
            view: The player's current view of the game.

        Returns:
            Messages to send, empty for silence.
        """
        ...
