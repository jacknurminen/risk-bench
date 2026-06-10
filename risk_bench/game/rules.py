from collections import Counter

from risk_bench.game.actions import (
    Action,
    Attack,
    EndAttackPhase,
    Fortify,
    Occupy,
    PlaceArmies,
    SkipFortify,
    TradeCards,
)
from risk_bench.game.board import BOARD
from risk_bench.game.cards import find_tradeable_sets, is_tradeable_set
from risk_bench.game.combat import max_attacker_dice
from risk_bench.game.state import GameState
from risk_bench.game.types import Phase, PlayerId


def legal_actions(state: GameState) -> list[Action]:
    """Enumerate the actions the current player may take in the current phase.

    Numeric fields (army counts, dice) are emitted at their maximum legal value;
    callers treat them as upper bounds. The tool-schema layer converts these
    bounds into JSON-schema range constraints instead of enumerating integers.
    Ordering is deterministic (territories sorted by name).

    Args:
        state: The current game state.

    Returns:
        The legal actions, never empty while the game is running.
    """
    match state.phase:
        case Phase.SETUP_PLACEMENT:
            return _legal_placements(state=state)
        case Phase.REINFORCE:
            return _legal_reinforce_actions(state=state)
        case Phase.ATTACK:
            return _legal_attacks(state=state)
        case Phase.OCCUPY:
            return _legal_occupations(state=state)
        case Phase.FORTIFY:
            return _legal_fortifications(state=state)
        case Phase.GAME_OVER:
            return []


def validate_action(state: GameState, action: Action) -> str | None:
    """Check an action against the rules without applying it.

    Args:
        state: The current game state.
        action: The action to validate.

    Returns:
        None if the action is legal, otherwise a human-readable reason suitable
        for feeding back to an LLM as a correction message.
    """
    if state.phase is Phase.GAME_OVER:
        return "The game is over; no further actions are possible."
    match action:
        case PlaceArmies():
            return _validate_place_armies(state=state, action=action)
        case TradeCards():
            return _validate_trade_cards(state=state, action=action)
        case Attack():
            return _validate_attack(state=state, action=action)
        case Occupy():
            return _validate_occupy(state=state, action=action)
        case Fortify():
            return _validate_fortify(state=state, action=action)
        case EndAttackPhase():
            return _require_phase(state=state, expected=Phase.ATTACK, action_name="Ending the attack phase")
        case SkipFortify():
            return _require_phase(state=state, expected=Phase.FORTIFY, action_name="Skipping fortification")


def reinforcement_count(state: GameState, player_id: PlayerId) -> int:
    """Compute the armies a player receives at the start of their reinforce phase.

    Territories owned divided by three (minimum three), plus continent bonuses.
    Card trade-in bonuses are not included; they are granted by ``TradeCards``.

    Args:
        state: The current game state.
        player_id: The player receiving reinforcements.

    Returns:
        The number of armies to place.
    """
    territory_income = max(3, len(state.territories_owned_by(player_id=player_id)) // 3)
    continent_income = sum(
        BOARD.continent_info(continent).army_bonus for continent in state.continents_owned_by(player_id=player_id)
    )
    return territory_income + continent_income


def must_trade_cards(state: GameState, player_id: PlayerId) -> bool:
    """Check whether the player holds five or more cards and is forced to trade.

    Args:
        state: The current game state.
        player_id: The player to check.

    Returns:
        True if trading a set is mandatory before placing armies.
    """
    return len(state.players[player_id].cards) >= 5


def _legal_placements(state: GameState) -> list[Action]:
    owned = sorted(state.territories_owned_by(player_id=state.current_player))
    return [PlaceArmies(territory=territory, count=state.armies_to_place) for territory in owned]


def _legal_reinforce_actions(state: GameState) -> list[Action]:
    hand = tuple(state.players[state.current_player].cards)
    trade_actions: list[Action] = [TradeCards(cards=card_set) for card_set in find_tradeable_sets(cards=hand)]
    if must_trade_cards(state=state, player_id=state.current_player):
        return trade_actions
    return trade_actions + _legal_placements(state=state)


def _legal_attacks(state: GameState) -> list[Action]:
    attacks: list[Action] = []
    for from_territory in sorted(state.territories_owned_by(player_id=state.current_player)):
        dice_limit = max_attacker_dice(armies_in_territory=state.armies[from_territory])
        if dice_limit == 0:
            continue
        for to_territory in sorted(BOARD.neighbors_of(from_territory)):
            if state.owner[to_territory] != state.current_player:
                attacks.append(
                    Attack(from_territory=from_territory, to_territory=to_territory, attacker_dice=dice_limit)
                )
    return attacks + [EndAttackPhase()]


def _legal_occupations(state: GameState) -> list[Action]:
    assert state.pending_occupy is not None
    from_territory, _ = state.pending_occupy
    return [Occupy(armies_to_move=state.armies[from_territory] - 1)]


def _legal_fortifications(state: GameState) -> list[Action]:
    fortifications: list[Action] = []
    for from_territory in sorted(state.territories_owned_by(player_id=state.current_player)):
        if state.armies[from_territory] < 2:
            continue
        for to_territory in sorted(BOARD.neighbors_of(from_territory)):
            if state.owner[to_territory] == state.current_player:
                fortifications.append(
                    Fortify(
                        from_territory=from_territory,
                        to_territory=to_territory,
                        count=state.armies[from_territory] - 1,
                    )
                )
    return fortifications + [SkipFortify()]


def _require_phase(state: GameState, expected: Phase, action_name: str) -> str | None:
    if state.phase is expected:
        return None
    return f"{action_name} is only legal in the {expected} phase; the current phase is {state.phase}."


def _validate_place_armies(state: GameState, action: PlaceArmies) -> str | None:
    if state.phase not in (Phase.SETUP_PLACEMENT, Phase.REINFORCE):
        return f"Placing armies is only legal during setup or reinforce; the current phase is {state.phase}."
    if state.phase is Phase.REINFORCE and must_trade_cards(state=state, player_id=state.current_player):
        return "You hold five or more cards and must trade in a set before placing armies."
    if state.owner[action.territory] != state.current_player:
        return f"You do not own {action.territory}."
    if not 1 <= action.count <= state.armies_to_place:
        return f"You must place between 1 and {state.armies_to_place} armies, not {action.count}."
    return None


def _validate_trade_cards(state: GameState, action: TradeCards) -> str | None:
    phase_rejection = _require_phase(state=state, expected=Phase.REINFORCE, action_name="Trading cards")
    if phase_rejection is not None:
        return phase_rejection
    hand = state.players[state.current_player].cards
    if not Counter(action.cards) <= Counter(hand):
        return "You can only trade cards that are in your hand."
    if not is_tradeable_set(card_set=action.cards):
        return "The three designs must be all the same or all different (wilds substitute for any design)."
    return None


def _validate_attack(state: GameState, action: Attack) -> str | None:
    phase_rejection = _require_phase(state=state, expected=Phase.ATTACK, action_name="Attacking")
    if phase_rejection is not None:
        return phase_rejection
    if state.owner[action.from_territory] != state.current_player:
        return f"You do not own {action.from_territory}."
    if state.owner[action.to_territory] == state.current_player:
        return f"You cannot attack {action.to_territory}; you own it."
    if action.to_territory not in BOARD.neighbors_of(action.from_territory):
        return f"{action.to_territory} is not adjacent to {action.from_territory}."
    dice_limit = max_attacker_dice(armies_in_territory=state.armies[action.from_territory])
    if dice_limit == 0:
        return f"{action.from_territory} needs at least 2 armies to attack."
    if not 1 <= action.attacker_dice <= dice_limit:
        return f"You can roll between 1 and {dice_limit} dice from {action.from_territory}."
    return None


def _validate_occupy(state: GameState, action: Occupy) -> str | None:
    phase_rejection = _require_phase(state=state, expected=Phase.OCCUPY, action_name="Occupying")
    if phase_rejection is not None:
        return phase_rejection
    assert state.pending_occupy is not None
    from_territory, _ = state.pending_occupy
    movable_armies = state.armies[from_territory] - 1
    if not 1 <= action.armies_to_move <= movable_armies:
        return f"You must move between 1 and {movable_armies} armies into the conquered territory."
    return None


def _validate_fortify(state: GameState, action: Fortify) -> str | None:
    phase_rejection = _require_phase(state=state, expected=Phase.FORTIFY, action_name="Fortifying")
    if phase_rejection is not None:
        return phase_rejection
    if state.owner[action.from_territory] != state.current_player:
        return f"You do not own {action.from_territory}."
    if state.owner[action.to_territory] != state.current_player:
        return f"You do not own {action.to_territory}; you can only fortify between your own territories."
    if action.to_territory not in BOARD.neighbors_of(action.from_territory):
        return f"{action.to_territory} is not adjacent to {action.from_territory}."
    movable_armies = state.armies[action.from_territory] - 1
    if movable_armies < 1:
        return f"{action.from_territory} has no armies to spare; one must stay behind."
    if not 1 <= action.count <= movable_armies:
        return f"You can move between 1 and {movable_armies} armies from {action.from_territory}."
    return None
