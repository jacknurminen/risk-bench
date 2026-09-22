import random
import sys
from dataclasses import replace

from risk_bench.agents.base import Agent, Message, Response, Tool, ToolCall
from risk_bench.chat.diplomacy import ChatMessage
from risk_bench.game.types import PlayerId
from risk_bench.players.base import Decision, DecisionRequest, NegotiationTurn, PlayerView
from risk_bench.players.rendering import (
    render_decision_prompt,
    render_negotiation_prompt,
    render_noting_prompt,
    render_system_prompt,
)
from risk_bench.players.tools import (
    InvalidToolCallError,
    is_message_tool_call,
    parse_negotiation_tool_call,
    parse_tool_call,
    tools_for_decision,
    tools_for_negotiation,
)


class ProviderUnavailableError(Exception):
    """Raised when a player's provider fails persistently (quota, outage, auth).

    Distinct from transient failures, which only consume a retry: this aborts
    the game so it pauses cleanly instead of silently degrading into random
    play. The journal keeps the game resumable once the provider recovers.
    """


class LLMPlayer:
    """A player driven by an LLM through tool calling.

    Each decision is a fresh, stateless exchange: the view is re-rendered from
    scratch (with recent events and visible chat) rather than growing one long
    conversation, so cost is bounded and long games cannot overflow context.

    Invalid tool calls are answered with the validation error and retried up to
    ``max_invalid_retries`` times, then a random legal action is substituted so
    a misbehaving model can never deadlock a game. Fallbacks are counted in
    ``invalid_action_count`` — itself a benchmark signal. A provider API
    failure (outage, rate limit, content-policy flag) consumes a retry the
    same way, but ``max_consecutive_provider_failures`` failures in a row —
    the signature of an exhausted quota rather than a flaky call — raise
    ``ProviderUnavailableError`` to pause the game instead of letting it
    degrade into random play. Any successful call resets the streak.
    """

    def __init__(
        self,
        player_id: PlayerId,
        agent: Agent,
        max_invalid_retries: int,
        max_consecutive_provider_failures: int,
        rng: random.Random,
    ) -> None:
        self.player_id = player_id
        self.invalid_action_count = 0
        self._agent = agent
        self._max_invalid_retries = max_invalid_retries
        self._max_consecutive_provider_failures = max_consecutive_provider_failures
        self._consecutive_provider_failures = 0
        self._rng = rng
        self._retry_conversation = None
        self._retry_index = 0
        self.additional_instructions = ""

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
        conversation = self._retry_conversation or [
            Message(role="system", content=render_system_prompt(view=request.view)),
            Message(role="user", content=render_decision_prompt(request=request)),
        ]
        if self._retry_conversation:
            # An information edit applies to the next call, including a paused retry.
            # Keep the correction exchange, provider signatures and retry budget intact;
            # only refresh the game-context message, in its original user-role position.
            conversation = [
                conversation[0],
                replace(conversation[1], content=render_decision_prompt(request=request)),
                *conversation[2:],
            ]
        tools = tools_for_decision(request=request)

        self._retry_conversation = conversation
        for index in range(self._retry_index, self._max_invalid_retries + 1):
            self._retry_index = index
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
            self._retry_conversation = None
            self._retry_index = 0
            return Decision(
                actions=tuple(actions),
                chat_message=_first_valid_message(response=response, view=request.view),
                thought=_thought_text(response=response),
            )

        self._retry_conversation = None
        self._retry_index = 0
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

    def update_notes(self, view: PlayerView) -> str | None:
        """Ask the model to rewrite its private notes for the turn that just ended.

        A dedicated, tool-free call: the model's plain-text reply becomes its new
        notepad. On a provider failure (or an empty reply) the existing notes are
        kept by returning None, so noting never blocks the game.

        Args:
            view: The noting player's view of the just-ended turn and its current notes.

        Returns:
            The rewritten notes, or None to keep the existing notes.
        """
        conversation = [
            Message(role="system", content=render_system_prompt(view=view)),
            Message(role="user", content=render_noting_prompt(view=view)),
        ]
        response = self._respond_or_none(conversation=conversation, tools=[])
        if response is None:
            return None
        notes = (response.text or "").strip()
        return notes or None

    def _respond_or_none(self, conversation: list[Message], tools: list[Tool]) -> Response | None:
        if self.additional_instructions:
            conversation = [
                replace(
                    conversation[0],
                    content=(conversation[0].content or "")
                    + "\n\nAdditional instructions:\n"
                    + self.additional_instructions,
                ),
                *conversation[1:],
            ]
        try:
            response = self._agent.respond(messages=conversation, tools=tools)
        except Exception as error:  # noqa: BLE001 - transient provider failures must not kill the game
            self._consecutive_provider_failures += 1
            print(f"[agent-error] {self.player_id}: {error}", file=sys.stderr)
            if self._consecutive_provider_failures >= self._max_consecutive_provider_failures:
                raise ProviderUnavailableError(
                    f"{self.player_id}'s provider failed {self._consecutive_provider_failures} times in a row "
                    f"(last error: {error}). Pausing the game; resume it once the provider recovers."
                ) from error
            return None
        self._consecutive_provider_failures = 0
        if response.text is None and not response.tool_calls:
            # A provider can answer with nothing at all — a content refusal, or an output budget
            # spent entirely on hidden reasoning. Silently that costs every retry and ends in a
            # random fallback, so name it: the trace holds the exact request that produced it.
            print(
                f"[empty-response] {self.player_id}: the provider returned no text and no tool calls", file=sys.stderr
            )
        return response


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
