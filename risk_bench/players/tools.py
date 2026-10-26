from dataclasses import replace
from typing import Any

from risk_bench.agents.base import Tool, ToolCall, list_argument
from risk_bench.chat.diplomacy import ChatMessage, MessageVisibility
from risk_bench.game.actions import (
    Action,
    Attack,
    EndAttackPhase,
    Fortify,
    PlaceArmies,
    SkipFortify,
    TradeCards,
)
from risk_bench.game.cards import TerritoryCard, trade_in_bonus
from risk_bench.game.types import PlayerId, Territory
from risk_bench.players.base import DecisionRequest, PlayerView


class InvalidToolCallError(Exception):
    """Raised when a tool call cannot be parsed into a legal action.

    The message is written for the model: it names the problem and the legal
    bounds so the model can correct itself on retry.
    """


def tools_for_decision(request: DecisionRequest) -> list[Tool]:
    """Build the tool schemas for a decision point: one tool per legal action type, plus messaging.

    Territory parameters become JSON-schema enums restricted to the legal
    choices; numeric parameters (army counts, dice) carry minimum/maximum
    bounds instead of being enumerated. Pair constraints that JSON schema
    cannot express (which targets each territory may attack, per-pair dice
    limits) are spelled out in the tool descriptions and enforced on parsing.

    The messaging tools are offered alongside the action tools so the model
    can announce, threaten, or explain in the same response as its move.

    Args:
        request: The decision point to expose.

    Returns:
        The tools the model may call; always non-empty.
    """
    tools = []
    placements = _actions_of_type(request=request, action_type=PlaceArmies)
    trades = _actions_of_type(request=request, action_type=TradeCards)
    attacks = _actions_of_type(request=request, action_type=Attack)
    fortifications = _actions_of_type(request=request, action_type=Fortify)

    if placements:
        tools.append(_place_armies_tool(placements=placements))
    if trades:
        tools.append(_trade_cards_tool(trades=trades))
    if attacks:
        tools.append(_attack_tool(attacks=attacks))
    if fortifications:
        tools.append(_fortify_tool(fortifications=fortifications))
    if any(isinstance(action, EndAttackPhase) for action in request.legal_actions):
        tools.append(Tool(name="end_attack_phase", description="Stop attacking and move on.", parameters=_NO_ARGS))
    if any(isinstance(action, SkipFortify) for action in request.legal_actions):
        tools.append(Tool(name="skip_fortify", description="End your turn without fortifying.", parameters=_NO_ARGS))
    return [_with_private_memo(tool) for tool in tools + _message_tools(view=request.view)]


def is_message_tool_call(call: ToolCall) -> bool:
    """Tell whether a tool call is messaging rather than a game action.

    Args:
        call: The tool call to classify.

    Returns:
        True for the messaging tools (including 'say_nothing'), False otherwise.
    """
    return call.name in _MESSAGE_TOOL_NAMES


def parse_tool_call(call: ToolCall, request: DecisionRequest) -> list[Action]:
    """Convert a model tool call back into game actions, checking full legality.

    Every constraint is verified against ``request.legal_actions``. ``place_armies``
    and ``attack`` produce a sequence of actions from a single call — a whole
    reinforcement split or a multi-front assault costs one model response. Each
    parsed action is legal against the current state, but later actions in an
    attack batch may be invalidated by earlier ones (a depleted source, an
    already-captured target); the runner skips any that no longer apply.

    Args:
        call: The tool call returned by the model.
        request: The decision point the call answers.

    Returns:
        The corresponding legal actions, in application order; never empty.

    Raises:
        InvalidToolCallError: If the tool name is unknown or arguments are
            missing, malformed, or outside the legal bounds.
    """
    match call.name:
        case "place_armies":
            return list(_parse_place_armies(call=call, request=request))
        case "trade_cards":
            return [_parse_trade_cards(call=call, request=request)]
        case "attack":
            return list(_parse_attack_launches(call=call, request=request))
        case "fortify":
            return [_parse_fortify(call=call, request=request)]
        case "end_attack_phase":
            return [_parse_parameterless(request=request, action=EndAttackPhase(), tool_name=call.name)]
        case "skip_fortify":
            return [_parse_parameterless(request=request, action=SkipFortify(), tool_name=call.name)]
        case unknown_name:
            available = ", ".join(tool.name for tool in tools_for_decision(request=request))
            raise InvalidToolCallError(f"Unknown tool '{unknown_name}'. Available tools: {available}.")


def tools_for_negotiation(view: PlayerView) -> list[Tool]:
    """Build the messaging tool schemas: send public, send private, say nothing.

    The private-message tool is only offered when the game allows private
    messages (``view.private_messages_allowed``).

    Args:
        view: The player's view, providing the valid private-message recipients.

    Returns:
        The negotiation tools.
    """
    tools = _message_tools(view=view)
    tools.append(Tool(name="say_nothing", description="Send no message this round.", parameters=_NO_ARGS))
    return [_with_private_memo(tool) for tool in tools]


def parse_negotiation_tool_call(call: ToolCall, view: PlayerView) -> ChatMessage | None:
    """Convert a messaging tool call into a chat message, or None for silence.

    Args:
        call: The tool call returned by the model.
        view: The player's view, providing sender identity and turn number.

    Returns:
        The composed message, or None if the model chose to say nothing.

    Raises:
        InvalidToolCallError: If the tool name is unknown, the recipient is
            invalid, or private messages are not allowed in this game.
    """
    match call.name:
        case "say_nothing":
            return None
        case "send_public_message":
            return ChatMessage(
                turn_number=view.turn_number,
                sender=view.player_id,
                visibility=MessageVisibility.PUBLIC,
                recipient=None,
                text=str(_required_argument(call=call, key="text")),
            )
        case "send_private_message":
            return _parse_private_message(call=call, view=view)
        case unknown_name:
            raise InvalidToolCallError(
                f"Unknown tool '{unknown_name}'. Use send_public_message, send_private_message, or say_nothing."
            )


_NO_ARGS: dict[str, Any] = {"type": "object", "properties": {}, "required": []}
_MESSAGE_TOOL_NAMES: frozenset[str] = frozenset({"send_public_message", "send_private_message", "say_nothing"})


def requires_private_memo(tool_name: str) -> bool:
    return tool_name not in {"say_nothing", "send_private_message"}


def _with_private_memo(tool: Tool) -> Tool:
    """Put the private decision summary in the advertised top-level signature."""
    if not requires_private_memo(tool.name):
        return tool
    return replace(tool, parameters={
        **tool.parameters,
        "properties": {
            "private_memo": {
                "type": "string", "minLength": 1, "maxLength": 1200,
                "description": "Required short decision summary: the intended game outcome and relevant board facts, "
                "in one or two brief sentences (1–1200 characters). "
                "Never delivered to other players.",
            },
            **tool.parameters["properties"],
        },
        "required": ["private_memo", *tool.parameters["required"]],
    })


def _message_tools(view: PlayerView) -> list[Tool]:
    tools = [
        Tool(
            name="send_public_message",
            description="Say something all players will see.",
            parameters=_object_schema(properties={"text": {"type": "string", "description": "Your message."}}),
        )
    ]
    recipients = _valid_recipients(view=view)
    if view.private_messages_allowed and recipients:
        tools.append(
            Tool(
                name="send_private_message",
                description="Whisper to one player; nobody else will see it.",
                parameters=_object_schema(
                    properties={
                        "recipient": {"type": "string", "enum": recipients, "description": "Who to whisper to."},
                        "text": {"type": "string", "description": "Your message."},
                    }
                ),
            )
        )
    return tools


def _object_schema(properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": list(properties)}


def _territory_property(territories: list[Territory], description: str) -> dict[str, Any]:
    return {"type": "string", "enum": [str(territory) for territory in territories], "description": description}


def _count_property(maximum: int, description: str) -> dict[str, Any]:
    return {"type": "integer", "minimum": 1, "maximum": maximum, "description": description}


def _positive_int_property(description: str) -> dict[str, Any]:
    return {"type": "integer", "minimum": 1, "description": description}


def _actions_of_type[ActionT: Action](request: DecisionRequest, action_type: type[ActionT]) -> list[ActionT]:
    return [action for action in request.legal_actions if isinstance(action, action_type)]


def _card_label(card: TerritoryCard) -> str:
    return "wild" if card.territory is None else f"{card.design}({card.territory})"


def _place_armies_tool(placements: list[PlaceArmies]) -> Tool:
    maximum = placements[0].count
    return Tool(
        name="place_armies",
        description=(
            f"Place armies on your territories. You have {maximum} armies left to place; "
            f"you can split them across territories by listing several placements in this one call."
        ),
        parameters=_object_schema(
            properties={
                "placements": {
                    "type": "array",
                    "minItems": 1,
                    "description": f"Where to place armies; the counts may total at most {maximum}.",
                    "items": _object_schema(
                        properties={
                            "territory": _territory_property(
                                territories=[placement.territory for placement in placements],
                                description="One of your territories.",
                            ),
                            "count": _count_property(maximum=maximum, description="How many armies to place there."),
                        }
                    ),
                },
            }
        ),
    )


def _trade_cards_tool(trades: list[TradeCards]) -> Tool:
    set_descriptions = "; ".join(
        f"{number}: {', '.join(_card_label(card) for card in trade.cards)}"
        f" -> {trade_in_bonus(card_set=trade.cards)} armies"
        for number, trade in enumerate(trades, start=1)
    )
    return Tool(
        name="trade_cards",
        description=f"Trade in a set of three cards for bonus armies. Available sets: {set_descriptions}.",
        parameters=_object_schema(
            properties={
                "set_number": _count_property(maximum=len(trades), description="Which available set to trade."),
            }
        ),
    )


def _attack_tool(attacks: list[Attack]) -> Tool:
    pair_descriptions = "; ".join(
        f"{attack.from_territory} -> {attack.to_territory} (up to {attack.attacker_dice} dice)" for attack in attacks
    )
    launch_item = _object_schema(
        properties={
            "from_territory": _territory_property(
                territories=sorted({attack.from_territory for attack in attacks}),
                description="Your attacking territory.",
            ),
            "to_territory": _territory_property(
                territories=sorted({attack.to_territory for attack in attacks}),
                description="The enemy territory to attack.",
            ),
            "attacker_dice": _count_property(
                maximum=max(attack.attacker_dice for attack in attacks),
                description="How many dice to roll; more dice means more risk and more reward.",
            ),
        }
    )
    launch_item["properties"]["max_attacks"] = _positive_int_property(
        description="Optional: how many times to keep rolling against this target, stopping early when you "
        "capture it or the source can no longer attack. Defaults to 1 (a single roll)."
    )
    launch_item["properties"]["armies_to_leave_behind"] = _positive_int_property(
        description="Optional: if you capture the target, how many armies stay in the source; the rest advance. "
        "At least as many armies as dice rolled in the capturing battle must advance, so a larger value is "
        "reduced if needed. Defaults to 1 (advance all but one)."
    )
    return Tool(
        name="attack",
        description="Attack adjacent enemy territories. Provide one or more launches; they resolve in the order "
        "given, and any launch that is no longer legal by the time it runs (e.g. its source was depleted, or it "
        f"targets a territory you already captured) is skipped. Valid attacks: {pair_descriptions}.",
        parameters=_object_schema(
            properties={
                "launches": {
                    "type": "array",
                    "minItems": 1,
                    "description": "The attacks to attempt this move, resolved in order.",
                    "items": launch_item,
                },
            }
        ),
    )


def _fortify_tool(fortifications: list[Fortify]) -> Tool:
    pair_descriptions = "; ".join(
        f"{fortification.from_territory} -> {fortification.to_territory} (up to {fortification.count})"
        for fortification in fortifications
    )
    return Tool(
        name="fortify",
        description=f"Move armies between two adjacent territories you own; ends your turn. Valid moves: "
        f"{pair_descriptions}.",
        parameters=_object_schema(
            properties={
                "from_territory": _territory_property(
                    territories=sorted({fortification.from_territory for fortification in fortifications}),
                    description="Where to move armies from.",
                ),
                "to_territory": _territory_property(
                    territories=sorted({fortification.to_territory for fortification in fortifications}),
                    description="Where to move armies to.",
                ),
                "count": _count_property(
                    maximum=max(fortification.count for fortification in fortifications),
                    description="How many armies to move.",
                ),
            }
        ),
    )


def _parse_place_armies(call: ToolCall, request: DecisionRequest) -> list[PlaceArmies]:
    raw_placements = list_argument(_required_argument(call=call, key="placements"))
    if not raw_placements:
        raise InvalidToolCallError("'placements' must be a non-empty array of {territory, count} objects.")
    owned_territories = {
        placement.territory for placement in _actions_of_type(request=request, action_type=PlaceArmies)
    }
    armies_available = _actions_of_type(request=request, action_type=PlaceArmies)[0].count

    placements = []
    for raw_placement in raw_placements:
        if not isinstance(raw_placement, dict):
            raise InvalidToolCallError("Each placement must be an object with 'territory' and 'count'.")
        if "territory" not in raw_placement or "count" not in raw_placement:
            raise InvalidToolCallError("Each placement must be an object with 'territory' and 'count'.")
        territory = _parse_territory(value=raw_placement["territory"])
        count = _parse_positive_int(value=raw_placement["count"], name="count")
        if territory not in owned_territories:
            raise InvalidToolCallError(f"You cannot place armies on {territory}; it is not one of your territories.")
        placements.append(PlaceArmies(territory=territory, count=count))

    total = sum(placement.count for placement in placements)
    if total > armies_available:
        raise InvalidToolCallError(
            f"Your placements total {total} armies but you only have {armies_available} left to place."
        )
    return placements


def _parse_trade_cards(call: ToolCall, request: DecisionRequest) -> TradeCards:
    trades = _actions_of_type(request=request, action_type=TradeCards)
    set_number = _parse_positive_int(value=_required_argument(call=call, key="set_number"), name="set_number")
    if not 1 <= set_number <= len(trades):
        raise InvalidToolCallError(f"set_number must be between 1 and {len(trades)}, not {set_number}.")
    return trades[set_number - 1]


def _parse_attack_launches(call: ToolCall, request: DecisionRequest) -> list[Attack]:
    raw_launches = list_argument(_required_argument(call=call, key="launches"))
    if not raw_launches:
        raise InvalidToolCallError("'launches' must be a non-empty array of attack objects.")
    return [_parse_attack_launch(raw_launch=raw_launch, request=request) for raw_launch in raw_launches]


def _parse_attack_launch(raw_launch: Any, request: DecisionRequest) -> Attack:
    if not isinstance(raw_launch, dict):
        raise InvalidToolCallError(
            "Each launch must be an object with 'from_territory', 'to_territory', and 'attacker_dice'."
        )
    for required_key in ("from_territory", "to_territory", "attacker_dice"):
        if required_key not in raw_launch:
            raise InvalidToolCallError(f"Each launch must include '{required_key}'.")
    from_territory = _parse_territory(value=raw_launch["from_territory"])
    to_territory = _parse_territory(value=raw_launch["to_territory"])
    dice = _parse_positive_int(value=raw_launch["attacker_dice"], name="attacker_dice")
    template = next(
        (
            attack
            for attack in _actions_of_type(request=request, action_type=Attack)
            if attack.from_territory == from_territory and attack.to_territory == to_territory
        ),
        None,
    )
    if template is None:
        raise InvalidToolCallError(
            f"Attacking {to_territory} from {from_territory} is not possible; see the attack tool's valid attacks."
        )
    if dice > template.attacker_dice:
        raise InvalidToolCallError(
            f"You can roll at most {template.attacker_dice} dice from {from_territory}, not {dice}."
        )
    return Attack(
        from_territory=from_territory,
        to_territory=to_territory,
        attacker_dice=dice,
        max_attacks=_optional_positive_int(raw_launch=raw_launch, key="max_attacks"),
        armies_to_leave_behind=_optional_positive_int(raw_launch=raw_launch, key="armies_to_leave_behind"),
    )


def _optional_positive_int(raw_launch: dict[str, Any], key: str) -> int:
    if key not in raw_launch:
        return 1
    return _parse_positive_int(value=raw_launch[key], name=key)


def _parse_fortify(call: ToolCall, request: DecisionRequest) -> Fortify:
    from_territory = _parse_territory(value=_required_argument(call=call, key="from_territory"))
    to_territory = _parse_territory(value=_required_argument(call=call, key="to_territory"))
    count = _parse_positive_int(value=_required_argument(call=call, key="count"), name="count")
    template = next(
        (
            fortification
            for fortification in _actions_of_type(request=request, action_type=Fortify)
            if fortification.from_territory == from_territory and fortification.to_territory == to_territory
        ),
        None,
    )
    if template is None:
        raise InvalidToolCallError(
            f"Fortifying from {from_territory} to {to_territory} is not possible; see the fortify tool's valid moves."
        )
    if count > template.count:
        raise InvalidToolCallError(f"You can move at most {template.count} armies from {from_territory}, not {count}.")
    return Fortify(from_territory=from_territory, to_territory=to_territory, count=count)


def _parse_parameterless(request: DecisionRequest, action: Action, tool_name: str) -> Action:
    if action not in request.legal_actions:
        raise InvalidToolCallError(f"'{tool_name}' is not available right now.")
    return action


def _parse_private_message(call: ToolCall, view: PlayerView) -> ChatMessage:
    if not view.private_messages_allowed:
        raise InvalidToolCallError("Private messages are not allowed in this game; speak publicly instead.")
    recipient = str(_required_argument(call=call, key="recipient"))
    if recipient not in _valid_recipients(view=view):
        raise InvalidToolCallError(f"'{recipient}' is not a living opponent you can whisper to.")
    return ChatMessage(
        turn_number=view.turn_number,
        sender=view.player_id,
        visibility=MessageVisibility.PRIVATE,
        recipient=recipient,
        text=str(_required_argument(call=call, key="text")),
    )


def _valid_recipients(view: PlayerView) -> list[PlayerId]:
    return [info.player_id for info in view.players if not info.is_eliminated and info.player_id != view.player_id]


def _required_argument(call: ToolCall, key: str) -> Any:
    if key not in call.arguments:
        raise InvalidToolCallError(f"Tool '{call.name}' requires the argument '{key}'.")
    return call.arguments[key]


def _parse_territory(value: Any) -> Territory:
    try:
        return Territory(str(value).strip().lower())
    except ValueError:
        raise InvalidToolCallError(f"'{value}' is not a territory name.") from None


def _parse_positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float) or int(value) != value or int(value) < 1:
        raise InvalidToolCallError(f"'{name}' must be a positive whole number, got {value!r}.")
    return int(value)
