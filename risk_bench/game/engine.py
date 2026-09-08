import copy
import random
from dataclasses import dataclass
from typing import Final

from risk_bench.game.actions import (
    Action,
    Attack,
    EndAttackPhase,
    Fortify,
    PlaceArmies,
    SkipFortify,
    TradeCards,
)
from risk_bench.game.cards import create_shuffled_deck, territories_awarding_extra_armies, trade_in_bonus
from risk_bench.game.combat import max_attacker_dice, max_defender_dice, resolve_battle
from risk_bench.game.events import (
    ArmiesPlaced,
    BattleResolved,
    CardDrawn,
    CardsTraded,
    Fortified,
    GameEvent,
    MissionCompleted,
    PlayerEliminated,
    TerritoryConquered,
    TurnStarted,
)
from risk_bench.game.missions import deal_missions, effective_mission, is_mission_complete
from risk_bench.game.rules import reinforcement_count, validate_action
from risk_bench.game.state import GameState, PlayerState
from risk_bench.game.types import Phase, PlayerId, Territory


class IllegalActionError(Exception):
    """Raised when an action that violates the rules reaches the engine."""


@dataclass(frozen=True)
class StepResult:
    """The state after applying an action, plus the events it produced."""

    state: GameState
    events: tuple[GameEvent, ...]


_STARTING_ARMIES_BY_PLAYER_COUNT: Final[dict[int, int]] = {3: 35, 4: 30, 5: 25, 6: 20}


def setup_game(player_ids: list[PlayerId], rng: random.Random) -> GameState:
    """Create the starting state: deal territories, missions, and starting armies.

    Territories are dealt randomly with one army each; each player then places
    their remaining starting armies during the SETUP_PLACEMENT phase, one player
    at a time in turn order.

    Args:
        player_ids: The players in turn order; must be 3-6 unique ids.
        rng: Source of randomness for all deals and shuffles.

    Returns:
        The initial game state in the SETUP_PLACEMENT phase.

    Raises:
        ValueError: If the player count is outside 3-6 or ids are not unique.
    """
    if len(player_ids) not in _STARTING_ARMIES_BY_PLAYER_COUNT:
        raise ValueError(f"Risk requires 3-6 players, got {len(player_ids)}.")
    if len(set(player_ids)) != len(player_ids):
        raise ValueError(f"Player ids must be unique, got {player_ids}.")

    territories = list(Territory)
    rng.shuffle(territories)
    owner = {territory: player_ids[index % len(player_ids)] for index, territory in enumerate(territories)}

    missions = deal_missions(player_ids=player_ids, rng=rng)
    players = {
        player_id: PlayerState(player_id=player_id, mission=missions[player_id], cards=[], eliminated_by=None)
        for player_id in player_ids
    }

    state = GameState(
        armies={territory: 1 for territory in Territory},
        owner=owner,
        players=players,
        deck=create_shuffled_deck(rng=rng),
        phase=Phase.SETUP_PLACEMENT,
        current_player=player_ids[0],
        armies_to_place=0,
        conquered_territory_this_turn=False,
        turn_number=0,
        winner=None,
    )
    state.armies_to_place = _remaining_setup_armies(state=state, player_id=player_ids[0])
    return state


def apply_action(state: GameState, action: Action, rng: random.Random) -> StepResult:
    """Apply a validated action and advance phase, turn, and win bookkeeping.

    Handles all consequences: battle resolution (a single attack may repeat
    until the target falls), the conquest's army advance, player eliminations
    (including card transfer), card draws at turn end, and mission-completion
    checks after every state change — missions can complete mid-attack-phase on
    a conquest or elimination.

    Args:
        state: The current game state; it is not mutated.
        action: The action to apply.
        rng: Source of randomness for dice rolls.

    Returns:
        The next state and the events describing what happened.

    Raises:
        IllegalActionError: If the action fails ``rules.validate_action``.
    """
    rejection = validate_action(state=state, action=action)
    if rejection is not None:
        raise IllegalActionError(rejection)

    next_state = copy.deepcopy(state)
    acting_player = next_state.current_player
    events: list[GameEvent] = []
    turn_ended = False

    match action:
        case PlaceArmies():
            events += _apply_place_armies(state=next_state, action=action)
        case TradeCards():
            events += _apply_trade_cards(state=next_state, action=action)
        case Attack():
            events += _apply_attack(state=next_state, action=action, rng=rng)
        case Fortify():
            events += _apply_fortify(state=next_state, action=action)
            turn_ended = True
        case EndAttackPhase():
            next_state.phase = Phase.FORTIFY
        case SkipFortify():
            turn_ended = True

    events += _declare_winner_if_any(state=next_state, acting_player=acting_player)
    if next_state.winner is None and turn_ended:
        events += _end_turn(state=next_state)
    return StepResult(state=next_state, events=tuple(events))


def _remaining_setup_armies(state: GameState, player_id: PlayerId) -> int:
    starting_armies = _STARTING_ARMIES_BY_PLAYER_COUNT[len(state.players)]
    return starting_armies - len(state.territories_owned_by(player_id=player_id))


def _apply_place_armies(state: GameState, action: PlaceArmies) -> list[GameEvent]:
    state.armies[action.territory] += action.count
    state.armies_to_place -= action.count
    events: list[GameEvent] = [
        ArmiesPlaced(player_id=state.current_player, territory=action.territory, count=action.count)
    ]
    if state.armies_to_place > 0:
        return events
    if state.phase is Phase.SETUP_PLACEMENT:
        return events + _advance_setup(state=state)
    state.phase = Phase.ATTACK
    return events


def _advance_setup(state: GameState) -> list[GameEvent]:
    player_ids = list(state.players)
    next_index = player_ids.index(state.current_player) + 1
    if next_index < len(player_ids):
        state.current_player = player_ids[next_index]
        state.armies_to_place = _remaining_setup_armies(state=state, player_id=state.current_player)
        return []
    return _start_turn(state=state, player_id=player_ids[0])


def _start_turn(state: GameState, player_id: PlayerId) -> list[GameEvent]:
    state.current_player = player_id
    state.phase = Phase.REINFORCE
    state.turn_number += 1
    state.conquered_territory_this_turn = False
    state.armies_to_place = reinforcement_count(state=state, player_id=player_id)
    return [TurnStarted(player_id=player_id, turn_number=state.turn_number)]


def _apply_trade_cards(state: GameState, action: TradeCards) -> list[GameEvent]:
    hand = state.players[state.current_player].cards
    for card in action.cards:
        hand.remove(card)
    state.deck.discard(cards=action.cards)

    bonus_armies = trade_in_bonus(card_set=action.cards)
    state.armies_to_place += bonus_armies
    first_design, second_design, third_design = sorted(card.design for card in action.cards)
    events: list[GameEvent] = [
        CardsTraded(
            player_id=state.current_player,
            card_designs=(first_design, second_design, third_design),
            bonus_armies=bonus_armies,
        )
    ]

    owned = state.territories_owned_by(player_id=state.current_player)
    for territory in territories_awarding_extra_armies(card_set=action.cards, owned_territories=owned):
        state.armies[territory] += 2
        events.append(ArmiesPlaced(player_id=state.current_player, territory=territory, count=2))
    return events


def _apply_attack(state: GameState, action: Attack, rng: random.Random) -> list[GameEvent]:
    attacker = state.current_player
    defender = state.owner[action.to_territory]
    events: list[GameEvent] = []
    for _ in range(action.max_attacks):
        dice_count = min(
            action.attacker_dice, max_attacker_dice(armies_in_territory=state.armies[action.from_territory])
        )
        if dice_count == 0:
            break  # the attacking territory is down to its last army and can no longer roll
        outcome = resolve_battle(
            attacker_dice_count=dice_count,
            defender_dice_count=max_defender_dice(armies_in_territory=state.armies[action.to_territory]),
            rng=rng,
        )
        state.armies[action.from_territory] -= outcome.attacker_losses
        state.armies[action.to_territory] -= outcome.defender_losses
        events.append(
            BattleResolved(
                attacker=attacker,
                defender=defender,
                from_territory=action.from_territory,
                to_territory=action.to_territory,
                outcome=outcome,
            )
        )
        if state.armies[action.to_territory] <= 0:
            events += _occupy_conquered_territory(
                state=state, action=action, attacker=attacker, defender=defender, capturing_dice_count=dice_count
            )
            break
    return events


def _occupy_conquered_territory(
    state: GameState, action: Attack, attacker: PlayerId, defender: PlayerId, capturing_dice_count: int
) -> list[GameEvent]:
    # at least as many armies as dice rolled in the capturing battle must advance; the attacker loses
    # nothing on a capturing roll, so the source always still holds that many plus the one that stays
    from_armies = state.armies[action.from_territory]
    requested_advance = from_armies - action.armies_to_leave_behind
    armies_to_move = min(max(requested_advance, capturing_dice_count, 1), from_armies - 1)
    state.armies[action.from_territory] -= armies_to_move
    state.armies[action.to_territory] += armies_to_move
    state.owner[action.to_territory] = attacker
    state.conquered_territory_this_turn = True
    events: list[GameEvent] = [
        TerritoryConquered(player_id=attacker, territory=action.to_territory, from_territory=action.from_territory)
    ]
    if not state.territories_owned_by(player_id=defender):
        events += _eliminate_player(state=state, eliminated_player=defender, eliminated_by=attacker)
    return events


def _eliminate_player(state: GameState, eliminated_player: PlayerId, eliminated_by: PlayerId) -> list[GameEvent]:
    eliminated_state = state.players[eliminated_player]
    eliminated_state.eliminated_by = eliminated_by
    state.players[eliminated_by].cards.extend(eliminated_state.cards)
    eliminated_state.cards.clear()
    return [PlayerEliminated(eliminated_player=eliminated_player, eliminated_by=eliminated_by)]


def _apply_fortify(state: GameState, action: Fortify) -> list[GameEvent]:
    state.armies[action.from_territory] -= action.count
    state.armies[action.to_territory] += action.count
    return [
        Fortified(
            player_id=state.current_player,
            from_territory=action.from_territory,
            to_territory=action.to_territory,
            count=action.count,
        )
    ]


def _end_turn(state: GameState) -> list[GameEvent]:
    events: list[GameEvent] = []
    if state.conquered_territory_this_turn:
        state.players[state.current_player].cards.append(state.deck.draw())
        events.append(CardDrawn(player_id=state.current_player))
    events += _start_turn(state=state, player_id=_next_living_player(state=state))
    return events


def _next_living_player(state: GameState) -> PlayerId:
    player_ids = list(state.players)
    current_index = player_ids.index(state.current_player)
    for offset in range(1, len(player_ids) + 1):
        candidate = player_ids[(current_index + offset) % len(player_ids)]
        if state.players[candidate].eliminated_by is None:
            return candidate
    raise RuntimeError("No living players remain.")


def _declare_winner_if_any(state: GameState, acting_player: PlayerId) -> list[GameEvent]:
    living_players = [player_id for player_id, player in state.players.items() if player.eliminated_by is None]
    mission = state.players[acting_player].mission
    if len(living_players) == 1:
        description = "All opponents have been eliminated."
    elif is_mission_complete(mission=mission, player_id=acting_player, state=state):
        description = effective_mission(mission=mission, player_id=acting_player, state=state).description
    else:
        return []
    state.winner = acting_player
    state.phase = Phase.GAME_OVER
    return [MissionCompleted(player_id=acting_player, mission_description=description)]
