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
    requires_private_memo,
    tools_for_decision,
    tools_for_negotiation,
)


class ProviderUnavailableError(Exception):
    """A failed provider call pauses the pending operation without substituting gameplay."""


class InvalidResponseError(ProviderUnavailableError):
    """The model has not supplied a usable response; retry the pending call on resume."""


class ProviderRefusalError(ProviderUnavailableError):
    """An explicit provider refusal pauses immediately instead of retrying as malformed output."""


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
        is never delivered. Required private_memo arguments in the successful
        tool calls are captured as the decision's private thought.

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
            # refresh the system requirements and game context in their original roles.
            conversation = [
                replace(conversation[0], content=render_system_prompt(view=request.view)),
                replace(conversation[1], content=render_decision_prompt(request=request)),
                *conversation[2:],
            ]
        conversation = _without_empty_assistant_turns(conversation)
        tools = tools_for_decision(request=request)

        self._retry_conversation = conversation
        last_error = "No action tool call received."
        for index in range(self._retry_index, self._max_invalid_retries + 1):
            self._retry_index = index
            response = self._respond(conversation=conversation, tools=tools)
            action_calls = [call for call in response.tool_calls if not is_message_tool_call(call=call)]
            if not action_calls:
                last_error = (
                    _empty_response_error(response)
                    if _is_empty_response(response)
                    else "Include a game action tool call with its required private_memo argument."
                )
                conversation += _action_demand_messages(response=response)
                continue
            if len(action_calls) != 1:
                last_error = "Choose exactly one action tool; use its batch arguments."
                conversation += _correction_messages(
                    response=response,
                    failed_call=action_calls[0],
                    error=InvalidToolCallError("Choose exactly one action tool; use its batch arguments."),
                )
                continue
            try:
                thought = _response_memo(response=response)
                actions = parse_tool_call(call=action_calls[0], request=request)
            except InvalidToolCallError as error:
                last_error = str(error)
                conversation += _correction_messages(response=response, failed_call=action_calls[0], error=error)
                continue
            try:
                chat_message = _validated_message(response=response, view=request.view)
            except InvalidToolCallError as error:
                last_error = str(error)
                failed = next(call for call in response.tool_calls if is_message_tool_call(call=call))
                conversation += _correction_messages(response=response, failed_call=failed, error=error)
                continue
            self._retry_conversation = None
            self._retry_index = 0
            return Decision(
                actions=tuple(actions),
                chat_message=chat_message,
                thought=thought,
            )

        # A later resume gets another bounded retry batch with the entire correction exchange.
        # Never apply a move that the controller did not choose.
        self._retry_conversation = conversation
        self._retry_index = 0
        self.invalid_action_count += 1
        raise InvalidResponseError(
            f"{self.player_id} returned no valid action. Last validation error: {last_error} "
            "Game paused; resume to retry."
        )

    def compose_messages(self, view: PlayerView) -> NegotiationTurn:
        """Ask the model whether to send diplomacy messages.

        Required private_memo arguments are captured as the turn's private thought.

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
        conversation = _without_empty_assistant_turns(conversation)
        self._retry_conversation = conversation
        last_error = "No negotiation tool call received."
        for index in range(self._retry_index, self._max_invalid_retries + 1):
            self._retry_index = index
            response = self._respond(conversation=conversation, tools=tools_for_negotiation(view=view))
            if not response.tool_calls:
                last_error = (
                    _empty_response_error(response)
                    if _is_empty_response(response)
                    else "Call a message tool or say_nothing; public messages require private_memo."
                )
                # An empty response is not a pass. Ask again and preserve the correction
                # across a pause so Resume does not replay the same ineffective request.
                conversation += _negotiation_demand_messages(response)
                continue
            try:
                thought = _response_memo(response=response)
                parsed = []
                for call in response.tool_calls:
                    parsed.append(parse_negotiation_tool_call(call=call, view=view))
            except InvalidToolCallError as error:
                last_error = str(error)
                conversation += _correction_messages(
                    response=response, failed_call=response.tool_calls[0], error=error
                )
                continue
            self._retry_conversation = None
            self._retry_index = 0
            return NegotiationTurn(
                messages=tuple(message for message in parsed if message is not None),
                thought=thought,
            )

        self._retry_index = 0
        raise InvalidResponseError(
            f"{self.player_id} did not send a valid message or explicitly pass after "
            f"{self._max_invalid_retries + 1} attempts. Last validation error: {last_error} "
            "Game paused; resume to retry with the correction history."
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
        if response.stop_reason == "refusal":
            error = (
                f"{self.player_id}'s provider refused the request{_response_diagnostics(response)}. "
                "Game paused; automatic retries stopped. No action or message was executed. "
                "The API trace contains the request and provider response."
            )
            print(f"[provider-refusal] {error}", file=sys.stderr)
            raise ProviderRefusalError(error)
        if _is_empty_response(response):
            # Keep the exact failed request visible in the API trace.
            print(f"[empty-response] {self.player_id}: {_empty_response_error(response)}", file=sys.stderr)
        return response


def _is_empty_response(response: Response) -> bool:
    return not (response.text or "").strip() and not response.tool_calls


def _empty_response_error(response: Response) -> str:
    return f"Empty provider response: no usable text or tool calls{_response_diagnostics(response)}."


def _response_diagnostics(response: Response) -> str:
    details = []
    if response.stop_reason is not None:
        details.append(f"stop_reason={response.stop_reason}")
    if response.output_tokens is not None:
        details.append(f"output_tokens={response.output_tokens}")
    request_id = (response.provider_metadata or {}).get("request_id")
    if request_id:
        details.append(f"request_id={request_id}")
    return f" ({', '.join(details)})" if details else ""


def _without_empty_assistant_turns(conversation: list[Message]) -> list[Message]:
    # Also repair histories restored from checkpoints written before empty replies
    # were omitted. Keep every real reply, tool result and provider signature.
    return [
        message
        for message in conversation
        if message.role != "assistant" or message.tool_calls or (message.content or "").strip()
    ]


def _response_memo(response: Response) -> str | None:
    memos = []
    for call in response.tool_calls:
        if not requires_private_memo(call.name) and "private_memo" not in call.arguments:
            continue
        text = call.arguments.get("private_memo")
        if not isinstance(text, str) or not text.strip() or len(text) > 1200:
            raise InvalidToolCallError(
                f"{call.name} requires a private_memo string argument of 1–1200 characters: "
                "a short private memo describing your intent and reason. Put it INSIDE the tool arguments. "
                "Keep your chosen tool and its other required arguments. "
                "Plain response text does not satisfy this field. Nothing in this response was executed or sent."
            )
        if text.strip() not in memos:
            memos.append(text.strip())
    return "\n\n".join(memos) or None


def _validated_message(response: Response, view: PlayerView) -> ChatMessage | None:
    calls = [call for call in response.tool_calls if is_message_tool_call(call=call)]
    if len(calls) > 1:
        raise InvalidToolCallError("Send at most one message alongside an action.")
    return parse_negotiation_tool_call(call=calls[0], view=view) if calls else None


def _action_demand_messages(response: Response) -> list[Message]:
    if not response.tool_calls:
        messages = [] if _is_empty_response(response) else [Message(role="assistant", content=response.text)]
        messages.append(
            Message(
                role="user",
                content="No game action was received or executed. Continue by calling exactly one available "
                "game action tool with its required private_memo argument.",
            )
        )
        return messages
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
    messages = [] if _is_empty_response(response) else [Message(role="assistant", content=response.text)]
    messages.append(
        Message(
            role="user",
            content="You have not answered this negotiation opportunity. Call send_public_message, "
            "send_private_message if offered, or say_nothing. Silence requires an explicit "
            "say_nothing tool call, which needs no memo. Public messages require private_memo; "
            "do not choose a board action here.",
        )
    )
    return messages


def _correction_messages(response: Response, failed_call: ToolCall, error: InvalidToolCallError) -> list[Message]:
    corrections = [Message(role="assistant", content=response.text, tool_calls=response.tool_calls)]
    for call in response.tool_calls:
        if call.id == failed_call.id:
            content = f"Invalid action: {error} Try again."
        else:
            content = (
                "Not delivered or executed: the entire response was rejected. Resend your intended tool calls "
                "with valid arguments, including private_memo for public messages and game actions."
            )
        corrections.append(Message(role="tool", content=content, tool_call_id=call.id))
    return corrections
