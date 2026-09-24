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
    """A failed provider call pauses the pending operation without substituting gameplay."""


class InvalidResponseError(ProviderUnavailableError):
    """The model has not supplied a usable response; retry the pending call on resume."""


class LLMPlayer:
    """A player driven by an LLM through tool calling.

    Each decision is a fresh, stateless exchange: the view is re-rendered from
    scratch (with recent events and visible chat) rather than growing one long
    conversation, so cost is bounded and long games cannot overflow context.

    Invalid tool calls receive a bounded correction exchange. Exhaustion pauses
    the game with the exchange preserved; any provider error pauses immediately.
    Resume retries the same operation without substituting an action or silence.
    """

    def __init__(
        self,
        player_id: PlayerId,
        agent: Agent,
        max_invalid_retries: int,
    ) -> None:
        self.player_id = player_id
        self.invalid_action_count = 0
        self._agent = agent
        self._max_invalid_retries = max_invalid_retries
        self._consecutive_provider_failures = 0
        self._retry_conversation = None
        self._retry_index = 0
        self.additional_instructions = ""

    def decide_action(self, request: DecisionRequest) -> Decision:
        """Ask the model to choose an action via tool calling, optionally with table talk.

        Alongside the action tool call the model may include messaging tool
        calls; at most one valid message is accepted per decision, and only
        from the response whose action was legal — chatter on failed attempts
        is never delivered. Plain text accompanying the successful response is
        captured as the decision's private thought.

        Args:
            request: The player's view and the legal actions.

        Returns:
            The model's validated decision; otherwise raise a resumable pause.
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
            response = self._respond(conversation=conversation, tools=tools)
            action_calls = [call for call in response.tool_calls if not is_message_tool_call(call=call)]
            if not action_calls:
                conversation += _action_demand_messages(response=response)
                continue
            if len(action_calls) != 1:
                conversation += _correction_messages(
                    response=response,
                    failed_call=action_calls[0],
                    error=InvalidToolCallError("Choose exactly one action tool; use its batch arguments."),
                )
                continue
            try:
                actions = parse_tool_call(call=action_calls[0], request=request)
            except InvalidToolCallError as error:
                conversation += _correction_messages(response=response, failed_call=action_calls[0], error=error)
                continue
            try:
                chat_message = _validated_message(response=response, view=request.view)
            except InvalidToolCallError as error:
                failed = next(call for call in response.tool_calls if is_message_tool_call(call=call))
                conversation += _correction_messages(response=response, failed_call=failed, error=error)
                continue
            self._retry_conversation = None
            self._retry_index = 0
            return Decision(
                actions=tuple(actions),
                chat_message=chat_message,
                thought=_thought_text(response=response),
            )

        # A later resume gets another bounded retry batch with the entire correction exchange.
        # Never apply a move that the controller did not choose.
        self._retry_conversation = conversation
        self._retry_index = 0
        self.invalid_action_count += 1
        raise InvalidResponseError(f"{self.player_id} returned no valid action. Game paused; resume to retry.")

    def compose_messages(self, view: PlayerView) -> NegotiationTurn:
        """Ask the model whether to send diplomacy messages.

        Plain text in the response is captured as the turn's private thought.

        Args:
            view: The player's current view of the game.

        Returns:
            The negotiation turn; silence requires an explicit pass tool call.
        """
        conversation = self._retry_conversation or [
            Message(role="system", content=render_system_prompt(view=view)),
            Message(role="user", content=render_negotiation_prompt(view=view)),
        ]
        if self._retry_conversation:
            # Context edits and a newly restored view apply even to a pending retry.
            conversation = [
                replace(conversation[0], content=render_system_prompt(view=view)),
                replace(conversation[1], content=render_negotiation_prompt(view=view)),
                *conversation[2:],
            ]
        self._retry_conversation = conversation
        for index in range(self._retry_index, self._max_invalid_retries + 1):
            self._retry_index = index
            response = self._respond(conversation=conversation, tools=tools_for_negotiation(view=view))
            if not response.tool_calls:
                # An empty response is not a pass. Ask again and preserve the correction
                # across a pause so Resume does not replay the same ineffective request.
                conversation += _negotiation_demand_messages(response)
                continue
            try:
                parsed = []
                for call in response.tool_calls:
                    parsed.append(parse_negotiation_tool_call(call=call, view=view))
            except InvalidToolCallError as error:
                conversation += _correction_messages(response=response, failed_call=call, error=error)
                continue
            self._retry_conversation = None
            self._retry_index = 0
            return NegotiationTurn(
                messages=tuple(message for message in parsed if message is not None),
                thought=_thought_text(response=response),
            )

        self._retry_index = 0
        raise InvalidResponseError(
            f"{self.player_id} did not send a valid message or explicitly pass after "
            f"{self._max_invalid_retries + 1} attempts. Game paused; resume to retry with the correction history."
        )

    def update_notes(self, view: PlayerView) -> str:
        """Ask the model to rewrite its private notes for the turn that just ended.

        A dedicated, tool-free call: the model's plain-text reply becomes its new
        notepad. Failure or an empty reply pauses the pending noting operation.

        Args:
            view: The noting player's view of the just-ended turn and its current notes.

        Returns:
            The rewritten notes; a failed response raises a resumable pause.
        """
        conversation = [
            Message(role="system", content=render_system_prompt(view=view)),
            Message(role="user", content=render_noting_prompt(view=view)),
        ]
        response = self._respond(conversation=conversation, tools=[])
        notes = (response.text or "").strip()
        if not notes or response.tool_calls:
            raise InvalidResponseError(f"{self.player_id} returned no usable private notes. Game paused.")
        return notes

    def _respond(self, conversation: list[Message], tools: list[Tool]) -> Response:
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
            raise ProviderUnavailableError(
                f"{self.player_id}'s provider failed: {error}. Game paused; resume to retry the pending call."
            ) from error
        self._consecutive_provider_failures = 0
        if response.text is None and not response.tool_calls:
            # Keep the exact failed request visible in the API trace.
            print(
                f"[empty-response] {self.player_id}: the provider returned no text and no tool calls", file=sys.stderr
            )
        return response


def _thought_text(response: Response) -> str | None:
    text = (response.text or "").strip()
    return text or None


def _validated_message(response: Response, view: PlayerView) -> ChatMessage | None:
    calls = [call for call in response.tool_calls if is_message_tool_call(call=call)]
    if len(calls) > 1:
        raise InvalidToolCallError("Send at most one message alongside an action.")
    return parse_negotiation_tool_call(call=calls[0], view=view) if calls else None


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


def _negotiation_demand_messages(response: Response) -> list[Message]:
    messages = [Message(role="assistant", content=response.text)] if response.text else []
    messages.append(
        Message(
            role="user",
            content="You have not answered this negotiation opportunity. Call send_public_message, "
            "send_private_message if offered, or say_nothing. Silence requires an explicit "
            "say_nothing tool call; do not choose a board action here.",
        )
    )
    return messages


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
