import random

from risk_bench.agents.base import Agent, Message, Response
from risk_bench.chat.diplomacy import ChatMessage
from risk_bench.game.actions import Action
from risk_bench.game.types import PlayerId
from risk_bench.players.base import DecisionRequest, PlayerView
from risk_bench.players.rendering import render_decision_prompt, render_negotiation_prompt, render_system_prompt
from risk_bench.players.tools import (
    InvalidToolCallError,
    parse_negotiation_tool_call,
    parse_tool_call,
    tools_for_decision,
    tools_for_negotiation,
)


class LLMPlayer:
    """A player driven by an LLM through tool calling.

    Each decision is a fresh, stateless exchange: the view is re-rendered from
    scratch (with recent events and visible chat) rather than growing one long
    conversation, so cost is bounded and long games cannot overflow context.

    Invalid tool calls are answered with the validation error and retried up to
    ``max_invalid_retries`` times, then a random legal action is substituted so
    a misbehaving model can never deadlock a game. Fallbacks are counted in
    ``invalid_action_count`` — itself a benchmark signal.
    """

    def __init__(self, player_id: PlayerId, agent: Agent, max_invalid_retries: int, rng: random.Random) -> None:
        self.player_id = player_id
        self.invalid_action_count = 0
        self._agent = agent
        self._max_invalid_retries = max_invalid_retries
        self._rng = rng

    def decide_action(self, request: DecisionRequest) -> Action:
        """Ask the model to choose an action via tool calling.

        Args:
            request: The player's view and the legal actions.

        Returns:
            The chosen action, or a random legal action after exhausted retries.
        """
        conversation = [
            Message(role="system", content=render_system_prompt(view=request.view)),
            Message(role="user", content=render_decision_prompt(request=request)),
        ]
        tools = tools_for_decision(request=request)

        for _ in range(self._max_invalid_retries + 1):
            response = self._agent.respond(messages=conversation, tools=tools)
            if not response.tool_calls:
                conversation.append(Message(role="assistant", content=response.text or ""))
                conversation.append(
                    Message(role="user", content="You must act by calling exactly one of the available tools.")
                )
                continue
            try:
                return parse_tool_call(call=response.tool_calls[0], request=request)
            except InvalidToolCallError as error:
                conversation += _correction_messages(response=response, error=error)

        self.invalid_action_count += 1
        return self._rng.choice(request.legal_actions)

    def compose_messages(self, view: PlayerView) -> list[ChatMessage]:
        """Ask the model whether to send diplomacy messages.

        Args:
            view: The player's current view of the game.

        Returns:
            Messages to send, empty for silence (also the fallback on invalid calls).
        """
        conversation = [
            Message(role="system", content=render_system_prompt(view=view)),
            Message(role="user", content=render_negotiation_prompt(view=view)),
        ]
        response = self._agent.respond(messages=conversation, tools=tools_for_negotiation(view=view))
        try:
            parsed = [parse_negotiation_tool_call(call=call, view=view) for call in response.tool_calls]
        except InvalidToolCallError:
            return []
        return [message for message in parsed if message is not None]


def _correction_messages(response: Response, error: InvalidToolCallError) -> list[Message]:
    failed_call, ignored_calls = response.tool_calls[0], response.tool_calls[1:]
    corrections = [
        Message(role="assistant", content=response.text, tool_calls=response.tool_calls),
        Message(role="tool", content=f"Invalid action: {error} Try again.", tool_call_id=failed_call.id),
    ]
    corrections += [
        Message(role="tool", content="Ignored: only one action may be taken at a time.", tool_call_id=call.id)
        for call in ignored_calls
    ]
    return corrections
