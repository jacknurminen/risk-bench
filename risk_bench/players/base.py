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
    """What everyone may know about a player: counts, not contents.

    ``income`` is the player's current per-turn reinforcement count
    (territories and continent bonuses, not card trades) — derivable from
    the public board, so exposing it leaks nothing.
    """

    player_id: PlayerId
    territory_count: int
    army_count: int
    card_count: int
    income: int
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


@dataclass(frozen=True)
class Decision:
    """A player's answer to one decision point, optionally announced with table talk.

    ``actions`` is a sequence applied in order so a whole reinforcement split
    can be decided at once; every action type other than army placement is
    strictly one per decision, since attacks need fresh dice results between
    choices. At most one message per decision keeps a turn's many decisions
    (every attack roll is one) from flooding the chat transcript.

    ``thought`` is the player's private rationale: recorded in the journal for
    analysis, never shown to other players.
    """

    actions: tuple[Action, ...]
    chat_message: ChatMessage | None
    thought: str | None


@dataclass(frozen=True)
class NegotiationTurn:
    """A player's contribution to one negotiation pass: messages plus private rationale.

    Like ``Decision.thought``, the thought is observational only — journaled
    for analysis, invisible to other players.
    """

    messages: tuple[ChatMessage, ...]
    thought: str | None


@runtime_checkable
class Player(Protocol):
    """A game-playing strategy: decides actions and composes diplomacy messages.

    Implementations never see ``GameState`` directly — only ``PlayerView``, so
    they cannot act on hidden information. ``invalid_action_count`` tracks how
    often the implementation failed to produce a legal action and a fallback
    was substituted — a benchmark signal in itself.
    """

    invalid_action_count: int

    def decide_action(self, request: DecisionRequest) -> Decision:
        """Choose legal actions for this decision point, optionally with a message.

        Args:
            request: The player's view and the legal actions.

        Returns:
            The decision; its actions must be among (or within the bounds of) the
            legal ones when applied in order — more than one only for army
            placements — and its message (if any) is broadcast as they are taken.
        """
        ...

    def compose_messages(self, view: PlayerView) -> NegotiationTurn:
        """Compose diplomacy messages for the current negotiation opportunity.

        Args:
            view: The player's current view of the game.

        Returns:
            The negotiation turn; empty messages mean silence.
        """
        ...
