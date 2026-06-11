from risk_bench.chat.diplomacy import ChatMessage
from risk_bench.game.events import GameEvent
from risk_bench.game.rules import reinforcement_count
from risk_bench.game.state import GameState
from risk_bench.game.types import Phase, PlayerId, Territory
from risk_bench.players.base import PlayerView, PublicPlayerInfo


def build_player_view(
    state: GameState,
    player_id: PlayerId,
    visible_chat: tuple[ChatMessage, ...],
    recent_events: tuple[GameEvent, ...],
    private_messages_allowed: bool,
) -> PlayerView:
    """Project the full game state down to what one player may legally know.

    This is the only place information hiding is enforced: other players' cards
    and missions are reduced to counts, everything else on the board is public.
    During SETUP_PLACEMENT other players' army counts are additionally masked to
    the dealt baseline of 1, so starting placements are decided blind.

    Args:
        state: The full game state.
        player_id: The player whose view to build.
        visible_chat: Chat messages already filtered to this player's visibility.
        recent_events: Events since this player's previous decision.
        private_messages_allowed: Whether this game permits private messages.

    Returns:
        The player's view of the game.
    """
    visible_armies = _visible_armies(state=state, viewer=player_id)
    own_player = state.players[player_id]
    return PlayerView(
        player_id=player_id,
        phase=state.phase,
        turn_number=state.turn_number,
        current_player=state.current_player,
        private_messages_allowed=private_messages_allowed,
        armies=visible_armies,
        owner=dict(state.owner),
        players=_public_player_infos(state=state, visible_armies=visible_armies),
        own_cards=tuple(own_player.cards),
        own_mission=own_player.mission,
        armies_to_place=state.armies_to_place if state.current_player == player_id else 0,
        visible_chat=visible_chat,
        recent_events=recent_events,
    )


def _visible_armies(state: GameState, viewer: PlayerId) -> dict[Territory, int]:
    if state.phase is not Phase.SETUP_PLACEMENT:
        return dict(state.armies)
    return {territory: armies if state.owner[territory] == viewer else 1 for territory, armies in state.armies.items()}


def _public_player_infos(state: GameState, visible_armies: dict[Territory, int]) -> tuple[PublicPlayerInfo, ...]:
    return tuple(
        PublicPlayerInfo(
            player_id=player_id,
            territory_count=len(state.territories_owned_by(player_id=player_id)),
            army_count=sum(visible_armies[territory] for territory in state.territories_owned_by(player_id=player_id)),
            card_count=len(player.cards),
            income=reinforcement_count(state=state, player_id=player_id),
            is_eliminated=player.eliminated_by is not None,
        )
        for player_id, player in state.players.items()
    )
