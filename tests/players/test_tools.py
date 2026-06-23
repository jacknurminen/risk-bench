import pytest

from risk_bench.agents.base import ToolCall
from risk_bench.chat.diplomacy import MessageVisibility
from risk_bench.game.actions import Attack, EndAttackPhase, PlaceArmies, SkipFortify, TradeCards
from risk_bench.game.cards import CardDesign, TerritoryCard
from risk_bench.game.rules import legal_actions
from risk_bench.game.types import Phase, PlayerId, Territory
from risk_bench.players.base import DecisionRequest
from risk_bench.players.tools import (
    InvalidToolCallError,
    is_message_tool_call,
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

    assert [tool.name for tool in tools] == ["attack", "end_attack_phase", "send_public_message"]
    attack_tool = tools[0]
    launch_item = attack_tool.parameters["properties"]["launches"]["items"]
    assert launch_item["properties"]["from_territory"]["enum"] == ["alaska"]
    assert set(launch_item["properties"]["to_territory"]["enum"]) == {"kamchatka", "northwest_territory"}
    assert launch_item["properties"]["attacker_dice"]["maximum"] == 3
    # the repeat and garrison knobs are offered but not required
    assert {"max_attacks", "armies_to_leave_behind"} <= set(launch_item["properties"])
    assert set(launch_item["required"]) == {"from_territory", "to_territory", "attacker_dice"}
    assert "alaska -> kamchatka (up to 3 dice)" in attack_tool.description


def test_reinforce_exposes_trade_and_placement_tools_with_bounds():
    tools = tools_for_decision(request=reinforce_request_with_tradeable_set())

    assert [tool.name for tool in tools] == ["place_armies", "trade_cards", "send_public_message"]
    place_tool, trade_tool = tools[0], tools[1]
    placement_item = place_tool.parameters["properties"]["placements"]["items"]
    assert placement_item["properties"]["count"]["maximum"] == 5
    assert set(placement_item["properties"]["territory"]["enum"]) == {"alaska", "alberta"}
    assert "infantry(alaska), infantry(peru), infantry(japan) -> 4 armies" in trade_tool.description


def test_parse_round_trips_a_valid_attack():
    actions = parse_tool_call(
        call=call("attack", launches=[{"from_territory": "alaska", "to_territory": "kamchatka", "attacker_dice": 2}]),
        request=attack_request(),
    )
    assert actions == [
        Attack(
            from_territory=Territory.ALASKA,
            to_territory=Territory.KAMCHATKA,
            attacker_dice=2,
            max_attacks=1,
            armies_to_leave_behind=1,
        )
    ]


def test_parse_batches_launches_in_order_with_optional_repeat_and_garrison():
    actions = parse_tool_call(
        call=call(
            "attack",
            launches=[
                {"from_territory": "alaska", "to_territory": "kamchatka", "attacker_dice": 3, "max_attacks": 5},
                {
                    "from_territory": "alaska",
                    "to_territory": "northwest_territory",
                    "attacker_dice": 1,
                    "armies_to_leave_behind": 4,
                },
            ],
        ),
        request=attack_request(),
    )
    assert actions == [
        Attack(
            from_territory=Territory.ALASKA,
            to_territory=Territory.KAMCHATKA,
            attacker_dice=3,
            max_attacks=5,
            armies_to_leave_behind=1,
        ),
        Attack(
            from_territory=Territory.ALASKA,
            to_territory=Territory.NORTHWEST_TERRITORY,
            attacker_dice=1,
            max_attacks=1,
            armies_to_leave_behind=4,
        ),
    ]


def test_parse_round_trips_placement_trade_and_phase_tools():
    request = reinforce_request_with_tradeable_set()

    placements = parse_tool_call(
        call=call("place_armies", placements=[{"territory": "alberta", "count": 5}]), request=request
    )
    assert placements == [PlaceArmies(territory=Territory.ALBERTA, count=5)]

    [trade] = parse_tool_call(call=call("trade_cards", set_number=1), request=request)
    assert isinstance(trade, TradeCards)
    assert trade in request.legal_actions

    assert parse_tool_call(call=call("end_attack_phase"), request=attack_request()) == [EndAttackPhase()]


def test_placements_split_across_territories_become_a_batch_in_order():
    request = reinforce_request_with_tradeable_set()

    placements = parse_tool_call(
        call=call(
            "place_armies",
            placements=[{"territory": "alaska", "count": 3}, {"territory": "alberta", "count": 2}],
        ),
        request=request,
    )

    assert placements == [
        PlaceArmies(territory=Territory.ALASKA, count=3),
        PlaceArmies(territory=Territory.ALBERTA, count=2),
    ]


def test_placement_batches_are_validated_as_a_whole():
    request = reinforce_request_with_tradeable_set()
    rejections = {
        "empty array": call("place_armies", placements=[]),
        "not an array": call("place_armies", placements="alaska"),
        "malformed item": call("place_armies", placements=["alaska"]),
        "missing count": call("place_armies", placements=[{"territory": "alaska"}]),
        "unowned territory": call("place_armies", placements=[{"territory": "japan", "count": 1}]),
        "total over the limit": call(
            "place_armies",
            placements=[{"territory": "alaska", "count": 3}, {"territory": "alberta", "count": 3}],
        ),
    }
    for reason, bad_call in rejections.items():
        with pytest.raises(InvalidToolCallError):
            parse_tool_call(call=bad_call, request=request)


def test_parse_rejects_illegal_or_malformed_calls_with_model_readable_messages():
    request = attack_request()
    rejections = {
        "unknown tool": call("launch_nukes"),
        "launches not an array": call("attack", launches="alaska"),
        "empty launches": call("attack", launches=[]),
        "malformed launch": call("attack", launches=["alaska"]),
        "illegal pair": call(
            "attack", launches=[{"from_territory": "alaska", "to_territory": "japan", "attacker_dice": 1}]
        ),
        "own territory": call(
            "attack", launches=[{"from_territory": "alaska", "to_territory": "alberta", "attacker_dice": 1}]
        ),
        "missing argument": call("attack", launches=[{"from_territory": "alaska", "to_territory": "kamchatka"}]),
        "bad territory": call(
            "attack", launches=[{"from_territory": "narnia", "to_territory": "kamchatka", "attacker_dice": 1}]
        ),
        "bad count type": call(
            "attack", launches=[{"from_territory": "alaska", "to_territory": "kamchatka", "attacker_dice": "three"}]
        ),
        "wrong phase tool": call("skip_fortify"),
    }
    for reason, bad_call in rejections.items():
        with pytest.raises(InvalidToolCallError):
            parse_tool_call(call=bad_call, request=request)


def test_parse_rejects_counts_over_the_legal_maximum():
    request = reinforce_request_with_tradeable_set()
    with pytest.raises(InvalidToolCallError, match="5"):
        parse_tool_call(call=call("place_armies", placements=[{"territory": "alaska", "count": 6}]), request=request)
    with pytest.raises(InvalidToolCallError, match="set_number"):
        parse_tool_call(call=call("trade_cards", set_number=2), request=request)


def test_parse_accepts_whole_number_floats():
    actions = parse_tool_call(
        call=call(
            "attack", launches=[{"from_territory": "alaska", "to_territory": "kamchatka", "attacker_dice": 2.0}]
        ),
        request=attack_request(),
    )
    assert actions == [
        Attack(
            from_territory=Territory.ALASKA,
            to_territory=Territory.KAMCHATKA,
            attacker_dice=2,
            max_attacks=1,
            armies_to_leave_behind=1,
        )
    ]


def test_fortify_tools_round_trip():
    request = request_for_phase(phase=Phase.FORTIFY, hand=[], private_messages_allowed=False)

    tools = tools_for_decision(request=request)
    assert [tool.name for tool in tools] == ["fortify", "skip_fortify", "send_public_message"]

    [action] = parse_tool_call(
        call=call("fortify", from_territory="alaska", to_territory="alberta", count=3), request=request
    )
    assert action in request.legal_actions
    assert parse_tool_call(call=call("skip_fortify"), request=request) == [SkipFortify()]


def test_decision_message_tools_respect_the_private_message_flag():
    with_private = request_for_phase(phase=Phase.ATTACK, hand=[], private_messages_allowed=True)

    tool_names = [tool.name for tool in tools_for_decision(request=with_private)]

    assert tool_names == ["attack", "end_attack_phase", "send_public_message", "send_private_message"]


def test_message_tool_calls_are_distinguished_from_action_calls():
    assert is_message_tool_call(call=call("send_public_message", text="Hi"))
    assert is_message_tool_call(call=call("send_private_message", recipient=BOB, text="Psst"))
    assert is_message_tool_call(call=call("say_nothing"))
    assert not is_message_tool_call(call=call("attack"))


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
