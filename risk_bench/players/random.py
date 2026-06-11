import random

from risk_bench.game.types import PlayerId
from risk_bench.players.base import Decision, DecisionRequest, NegotiationTurn, PlayerView


class RandomPlayer:
    """A baseline player: uniform random choice over legal actions, never chats.

    Serves as the Elo anchor and as the workhorse for engine tests — full
    random-vs-random games exercise every rule without any LLM calls.
    """

    def __init__(self, player_id: PlayerId, rng: random.Random) -> None:
        self.player_id = player_id
        self.invalid_action_count = 0
        self._rng = rng

    def decide_action(self, request: DecisionRequest) -> Decision:
        """Choose a uniformly random legal action, silently.

        Numeric fields keep the maximum values that ``legal_actions`` emits;
        sampling them lower would mostly produce slower, weaker play.

        Args:
            request: The player's view and the legal actions.

        Returns:
            A messageless decision with one of the legal actions.
        """
        return Decision(actions=(self._rng.choice(request.legal_actions),), chat_message=None, thought=None)

    def compose_messages(self, view: PlayerView) -> NegotiationTurn:
        """Stay silent; the random baseline never negotiates.

        Args:
            view: The player's current view of the game (unused).

        Returns:
            An empty negotiation turn.
        """
        return NegotiationTurn(messages=(), thought=None)
