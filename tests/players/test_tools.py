import pytest

from risk_bench.agents.base import ToolCall
from risk_bench.chat.diplomacy import MessageVisibility
from risk_bench.game.actions import Attack, EndAttackPhase, Occupy, PlaceArmies, SkipFortify, TradeCards
from risk_bench.game.cards import CardDesign, TerritoryCard
from risk_bench.game.rules import legal_actions
from risk_bench.game.types import Phase, PlayerId, Territory
from risk_bench.players.base import DecisionRequest
from risk_bench.players.tools import (
    InvalidToolCallError,
    parse_negotiation_tool_call,
    parse_tool_call,
    tools_for_decision,
    tools_for_negotiation,
)
from risk_bench.players.view import build_player_view
from tests.game.builders import PLACEHOLDER_MISSION, make_state, split_board_between

ALICE: PlayerId = "alice"
BOB: PlayerId = "bob"


def request_for_phase(phase: Phase, hand: list[TerritoryCard], private_messages_allowed: bool) -> DecisionRequest:
    owner = split_board_between(
        first_player=ALICE,
        first_player_territories=frozenset({Territory.ALASKA, Territory.ALBERTA}),
        second_player=BOB,
    )
    armies = {territory: 1 for territory in Territory}
    armies[Territory.ALASKA] = 4
    state = make_state(
        owner=owner,
        armies=armies,
        missions={ALICE: PLACEHOLDER_MISSION, BOB: PLACEHOLDER_MISSION},
        eliminated_by={ALICE: None, BOB: None},
    )
    state.phase = phase
    state.armies_to_place = 5
    state.players[ALICE].cards = hand
    view = build_player_view(
        state=state,
        player_id=ALICE,
        visible_chat=(),
        recent_events=(),
        private_messages_allowed=private_messages_allowed,
    )
    return DecisionRequest(view=view, legal_actions=legal_actions(state=state))


def attack_request() -> DecisionRequest:
    return request_for_phase(phase=Phase.ATTACK, hand=[], private_messages_allowed=False)


def reinforce_request_with_tradeable_set() -> DecisionRequest:
    hand = [
        TerritoryCard(design=CardDesign.INFANTRY, territory=Territory.ALASKA),
        TerritoryCard(design=CardDesign.INFANTRY, territory=Territory.PERU),
        TerritoryCard(design=CardDesign.INFANTRY, territory=Territory.JAPAN),
    ]
    return request_for_phase(phase=Phase.REINFORCE, hand=hand, private_messages_allowed=False)


def call(name: str, **arguments) -> ToolCall:
    return ToolCall(id="call-1", name=name, arguments=arguments)


def test_attack_phase_exposes_attack_and_end_attack_tools():
    tools = tools_for_decision(request=attack_request())

    assert [tool.name for tool in tools] == ["attack", "end_attack_phase"]
    attack_tool = tools[0]
    assert attack_tool.parameters["properties"]["from_territory"]["enum"] == ["alaska"]
    assert set(attack_tool.parameters["properties"]["to_territory"]["enum"]) == {"kamchatka", "northwest_territory"}
    assert attack_tool.parameters["properties"]["attacker_dice"]["maximum"] == 3
    assert "alaska -> kamchatka (up to 3 dice)" in attack_tool.description


def test_reinforce_exposes_trade_and_placement_tools_with_bounds():
    tools = tools_for_decision(request=reinforce_request_with_tradeable_set())

    assert [tool.name for tool in tools] == ["place_armies", "trade_cards"]
    place_tool, trade_tool = tools
    assert place_tool.parameters["properties"]["count"]["maximum"] == 5
    assert set(place_tool.parameters["properties"]["territory"]["enum"]) == {"alaska", "alberta"}
    assert "infantry(alaska), infantry(peru), infantry(japan) -> 4 armies" in trade_tool.description


def test_parse_round_trips_a_valid_attack():
    action = parse_tool_call(
        call=call("attack", from_territory="alaska", to_territory="kamchatka", attacker_dice=2),
        request=attack_request(),
    )
    assert action == Attack(from_territory=Territory.ALASKA, to_territory=Territory.KAMCHATKA, attacker_dice=2)


def test_parse_round_trips_placement_trade_and_phase_tools():
    request = reinforce_request_with_tradeable_set()

    placement = parse_tool_call(call=call("place_armies", territory="alberta", count=5), request=request)
    assert placement == PlaceArmies(territory=Territory.ALBERTA, count=5)

    trade = parse_tool_call(call=call("trade_cards", set_number=1), request=request)
    assert isinstance(trade, TradeCards)
    assert trade in request.legal_actions

    assert parse_tool_call(call=call("end_attack_phase"), request=attack_request()) == EndAttackPhase()


def test_parse_rejects_illegal_or_malformed_calls_with_model_readable_messages():
    request = attack_request()
    rejections = {
        "unknown tool": call("launch_nukes"),
        "illegal pair": call("attack", from_territory="alaska", to_territory="japan", attacker_dice=1),
        "own territory": call("attack", from_territory="alaska", to_territory="alberta", attacker_dice=1),
        "missing argument": call("attack", from_territory="alaska", to_territory="kamchatka"),
        "bad territory": call("attack", from_territory="narnia", to_territory="kamchatka", attacker_dice=1),
        "bad count type": call("attack", from_territory="alaska", to_territory="kamchatka", attacker_dice="three"),
        "wrong phase tool": call("skip_fortify"),
    }
    for reason, bad_call in rejections.items():
        with pytest.raises(InvalidToolCallError):
            parse_tool_call(call=bad_call, request=request)


def test_parse_rejects_counts_over_the_legal_maximum():
    request = reinforce_request_with_tradeable_set()
    with pytest.raises(InvalidToolCallError, match="5"):
        parse_tool_call(call=call("place_armies", territory="alaska", count=6), request=request)
    with pytest.raises(InvalidToolCallError, match="set_number"):
        parse_tool_call(call=call("trade_cards", set_number=2), request=request)


def test_parse_accepts_whole_number_floats():
    action = parse_tool_call(
        call=call("attack", from_territory="alaska", to_territory="kamchatka", attacker_dice=2.0),
        request=attack_request(),
    )
    assert action == Attack(from_territory=Territory.ALASKA, to_territory=Territory.KAMCHATKA, attacker_dice=2)


def test_occupy_tool_round_trip():
    request = attack_request()
    occupy_request = DecisionRequest(view=request.view, legal_actions=[Occupy(armies_to_move=3)])

    tools = tools_for_decision(request=occupy_request)
    assert [tool.name for tool in tools] == ["occupy"]

    action = parse_tool_call(call=call("occupy", armies_to_move=2), request=occupy_request)
    assert action == Occupy(armies_to_move=2)
    with pytest.raises(InvalidToolCallError):
        parse_tool_call(call=call("occupy", armies_to_move=4), request=occupy_request)


def test_fortify_tools_round_trip():
    request = request_for_phase(phase=Phase.FORTIFY, hand=[], private_messages_allowed=False)

    tools = tools_for_decision(request=request)
    assert [tool.name for tool in tools] == ["fortify", "skip_fortify"]

    action = parse_tool_call(
        call=call("fortify", from_territory="alaska", to_territory="alberta", count=3), request=request
    )
    assert action in request.legal_actions
    assert parse_tool_call(call=call("skip_fortify"), request=request) == SkipFortify()


def test_negotiation_tools_respect_the_private_message_flag():
    public_only = request_for_phase(phase=Phase.ATTACK, hand=[], private_messages_allowed=False)
    with_private = request_for_phase(phase=Phase.ATTACK, hand=[], private_messages_allowed=True)

    assert [tool.name for tool in tools_for_negotiation(view=public_only.view)] == [
        "send_public_message",
        "say_nothing",
    ]
    private_tools = tools_for_negotiation(view=with_private.view)
    assert [tool.name for tool in private_tools] == ["send_public_message", "send_private_message", "say_nothing"]
    assert private_tools[1].parameters["properties"]["recipient"]["enum"] == [BOB]


def test_parse_negotiation_calls():
    view = request_for_phase(phase=Phase.ATTACK, hand=[], private_messages_allowed=True).view

    assert parse_negotiation_tool_call(call=call("say_nothing"), view=view) is None

    public = parse_negotiation_tool_call(call=call("send_public_message", text="Truce?"), view=view)
    assert public is not None
    assert (public.sender, public.visibility, public.text) == (ALICE, MessageVisibility.PUBLIC, "Truce?")

    private = parse_negotiation_tool_call(call=call("send_private_message", recipient=BOB, text="Psst"), view=view)
    assert private is not None
    assert (private.recipient, private.visibility) == (BOB, MessageVisibility.PRIVATE)

    with pytest.raises(InvalidToolCallError):
        parse_negotiation_tool_call(call=call("send_private_message", recipient="nobody", text="?"), view=view)


def test_private_negotiation_calls_are_rejected_when_disallowed():
    view = request_for_phase(phase=Phase.ATTACK, hand=[], private_messages_allowed=False).view
    with pytest.raises(InvalidToolCallError, match="not allowed"):
        parse_negotiation_tool_call(call=call("send_private_message", recipient=BOB, text="Psst"), view=view)
