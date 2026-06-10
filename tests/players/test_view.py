from risk_bench.game.types import Phase, PlayerId, Territory
from risk_bench.players.view import build_player_view
from tests.game.builders import PLACEHOLDER_MISSION, make_state, split_board_between

ALICE: PlayerId = "alice"
BOB: PlayerId = "bob"


def two_player_state():
    owner = split_board_between(
        first_player=ALICE, first_player_territories=frozenset({Territory.ALASKA}), second_player=BOB
    )
    armies = {territory: 1 for territory in Territory}
    armies[Territory.ALASKA] = 5
    armies[Territory.JAPAN] = 7
    return make_state(
        owner=owner,
        armies=armies,
        missions={ALICE: PLACEHOLDER_MISSION, BOB: PLACEHOLDER_MISSION},
        eliminated_by={ALICE: None, BOB: None},
    )


def test_view_hides_other_players_cards_and_missions_but_shows_counts():
    state = two_player_state()
    state.players[BOB].cards = [state.deck.draw(), state.deck.draw()]

    view = build_player_view(
        state=state, player_id=ALICE, visible_chat=(), recent_events=(), private_messages_allowed=True
    )

    assert view.own_mission == PLACEHOLDER_MISSION
    assert view.own_cards == ()
    bob_info = next(info for info in view.players if info.player_id == BOB)
    assert bob_info.card_count == 2
    assert bob_info.territory_count == 41
    assert not hasattr(view, "players_cards")


def test_view_shows_the_public_board():
    state = two_player_state()

    view = build_player_view(
        state=state, player_id=ALICE, visible_chat=(), recent_events=(), private_messages_allowed=True
    )

    assert view.armies[Territory.JAPAN] == 7
    assert view.owner[Territory.JAPAN] == BOB
    assert view.armies[Territory.ALASKA] == 5


def test_setup_placement_masks_other_players_armies_to_the_dealt_baseline():
    state = two_player_state()
    state.phase = Phase.SETUP_PLACEMENT

    alice_view = build_player_view(
        state=state, player_id=ALICE, visible_chat=(), recent_events=(), private_messages_allowed=True
    )
    bob_view = build_player_view(
        state=state, player_id=BOB, visible_chat=(), recent_events=(), private_messages_allowed=True
    )

    assert alice_view.armies[Territory.ALASKA] == 5  # own placements stay visible
    assert alice_view.armies[Territory.JAPAN] == 1  # bob's placements are hidden
    assert bob_view.armies[Territory.JAPAN] == 7
    assert bob_view.armies[Territory.ALASKA] == 1
    bob_info_for_alice = next(info for info in alice_view.players if info.player_id == BOB)
    assert bob_info_for_alice.army_count == 41  # one per territory, placements hidden


def test_armies_to_place_is_only_shown_to_the_current_player():
    state = two_player_state()
    state.armies_to_place = 7

    assert (
        build_player_view(
            state=state, player_id=ALICE, visible_chat=(), recent_events=(), private_messages_allowed=True
        ).armies_to_place
        == 7
    )
    assert (
        build_player_view(
            state=state, player_id=BOB, visible_chat=(), recent_events=(), private_messages_allowed=True
        ).armies_to_place
        == 0
    )
