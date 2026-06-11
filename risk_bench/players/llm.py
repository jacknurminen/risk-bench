import random
import sys

from risk_bench.agents.base import Agent, Message, Response, Tool, ToolCall
from risk_bench.chat.diplomacy import ChatMessage
from risk_bench.game.types import PlayerId
from risk_bench.players.base import Decision, DecisionRequest, NegotiationTurn, PlayerView
from risk_bench.players.rendering import render_decision_prompt, render_negotiation_prompt, render_system_prompt
from risk_bench.players.tools import (
    InvalidToolCallError,
    is_message_tool_call,
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
    ``invalid_action_count`` — itself a benchmark signal. Provider API failures
    (outages, content-policy flags) consume a retry the same way: a player's
    provider must never be able to kill a running game.
    """

    def __init__(self, player_id: PlayerId, agent: Agent, max_invalid_retries: int, rng: random.Random) -> None:
        self.player_id = player_id
        self.invalid_action_count = 0
        self._agent = agent
        self._max_invalid_retries = max_invalid_retries
        self._rng = rng

    def decide_action(self, request: DecisionRequest) -> Decision:
        """Ask the model to choose an action via tool calling, optionally with table talk.

        Alongside the action tool call the model may include messaging tool
        calls; the first valid message is kept (one per decision), and only
        from the response whose action was legal — chatter on failed attempts
        is never delivered. Plain text accompanying the successful response is
        captured as the decision's private thought.

        Args:
            request: The player's view and the legal actions.

        Returns:
            The decision, or a silent random legal action after exhausted retries.
        """
        conversation = [
            Message(role="system", content=render_system_prompt(view=request.view)),
            Message(role="user", content=render_decision_prompt(request=request)),
        ]
        tools = tools_for_decision(request=request)

        for _ in range(self._max_invalid_retries + 1):
            response = self._respond_or_none(conversation=conversation, tools=tools)
            if response is None:
                continue
            action_calls = [call for call in response.tool_calls if not is_message_tool_call(call=call)]
            if not action_calls:
                conversation += _action_demand_messages(response=response)
                continue
            try:
                actions = parse_tool_call(call=action_calls[0], request=request)
            except InvalidToolCallError as error:
                conversation += _correction_messages(response=response, failed_call=action_calls[0], error=error)
                continue
            return Decision(
                actions=tuple(actions),
                chat_message=_first_valid_message(response=response, view=request.view),
                thought=_thought_text(response=response),
            )

        self.invalid_action_count += 1
        return Decision(actions=(self._rng.choice(request.legal_actions),), chat_message=None, thought=None)

    def compose_messages(self, view: PlayerView) -> NegotiationTurn:
        """Ask the model whether to send diplomacy messages.

        Plain text in the response is captured as the turn's private thought.

        Args:
            view: The player's current view of the game.

        Returns:
            The negotiation turn; empty messages mean silence (also the
            fallback on invalid calls).
        """
        conversation = [
            Message(role="system", content=render_system_prompt(view=view)),
            Message(role="user", content=render_negotiation_prompt(view=view)),
        ]
        response = self._respond_or_none(conversation=conversation, tools=tools_for_negotiation(view=view))
        if response is None:
            return NegotiationTurn(messages=(), thought=None)
        thought = _thought_text(response=response)
        try:
            parsed = [parse_negotiation_tool_call(call=call, view=view) for call in response.tool_calls]
        except InvalidToolCallError:
            return NegotiationTurn(messages=(), thought=thought)
        return NegotiationTurn(messages=tuple(message for message in parsed if message is not None), thought=thought)

    def _respond_or_none(self, conversation: list[Message], tools: list[Tool]) -> Response | None:
        try:
            return self._agent.respond(messages=conversation, tools=tools)
        except Exception as error:  # noqa: BLE001 - a player's provider must never kill a running game
            print(f"[agent-error] {self.player_id}: {error}", file=sys.stderr)
            return None


def _thought_text(response: Response) -> str | None:
    text = (response.text or "").strip()
    return text or None


def _first_valid_message(response: Response, view: PlayerView) -> ChatMessage | None:
    for call in response.tool_calls:
        if not is_message_tool_call(call=call):
            continue
        try:
            message = parse_negotiation_tool_call(call=call, view=view)
        except InvalidToolCallError:
            continue
        if message is not None:
            return message
    return None


def _action_demand_messages(response: Response) -> list[Message]:
    if not response.tool_calls:
        return [
            Message(role="assistant", content=response.text or ""),
            Message(role="user", content="You must act by calling exactly one of the available tools."),
        ]
    demands = [Message(role="assistant", content=response.text, tool_calls=response.tool_calls)]
    demands += [
        Message(
            role="tool",
            content="Not delivered: you must include a game action tool call in the same response.",
            tool_call_id=call.id,
        )
        for call in response.tool_calls
    ]
    return demands


def _correction_messages(response: Response, failed_call: ToolCall, error: InvalidToolCallError) -> list[Message]:
    corrections = [Message(role="assistant", content=response.text, tool_calls=response.tool_calls)]
    for call in response.tool_calls:
        if call.id == failed_call.id:
            content = f"Invalid action: {error} Try again."
        elif is_message_tool_call(call=call):
            content = "Not delivered: messages only count alongside a legal action; resend it with your retry."
        else:
            content = "Ignored: only one action may be taken at a time."
        corrections.append(Message(role="tool", content=content, tool_call_id=call.id))
    return corrections
