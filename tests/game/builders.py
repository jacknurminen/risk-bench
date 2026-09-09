import random

from risk_bench.game.cards import create_shuffled_deck
from risk_bench.game.missions import Mission, MissionKind
from risk_bench.game.ruleset import CURRENT_RULESET
from risk_bench.game.state import GameState, PlayerState
from risk_bench.game.types import Phase, PlayerId, Territory

PLACEHOLDER_MISSION = Mission(
    kind=MissionKind.CONQUER_24_TERRITORIES,
    description="Conquer 24 territories of your choice.",
    target_continents=frozenset(),
    target_player=None,
)


def make_state(
    owner: dict[Territory, PlayerId],
    armies: dict[Territory, int],
    missions: dict[PlayerId, Mission],
    eliminated_by: dict[PlayerId, PlayerId | None],
) -> GameState:
    players = {
        player_id: PlayerState(
            player_id=player_id,
            mission=mission,
            cards=[],
            eliminated_by=eliminated_by[player_id],
        )
        for player_id, mission in missions.items()
    }
    return GameState(
        ruleset=CURRENT_RULESET,
        armies=armies,
        owner=owner,
        players=players,
        deck=create_shuffled_deck(rng=random.Random(0)),
        phase=Phase.REINFORCE,
        current_player=next(iter(players)),
        armies_to_place=0,
        conquered_territory_this_turn=False,
        turn_number=1,
        winner=None,
    )


def split_board_between(
    first_player: PlayerId, first_player_territories: frozenset[Territory], second_player: PlayerId
) -> dict[Territory, PlayerId]:
    return {
        territory: first_player if territory in first_player_territories else second_player for territory in Territory
    }
