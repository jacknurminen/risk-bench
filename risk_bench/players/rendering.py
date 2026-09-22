from typing import Final

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
from risk_bench.game.board import BOARD
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
    TurnLimitReached,
    TurnStarted,
)
from risk_bench.game.missions import mission_deck
from risk_bench.game.types import Continent, Phase, PlayerId, Territory
from risk_bench.players.base import DecisionRequest, PlayerView

_RULES_SUMMARY: Final[str] = """\
Rules summary:
- Risk, mission variant: every player has a SECRET mission; the first to complete theirs wins immediately.
- Turn order: reinforce (place armies, optionally trade card sets), attack (as often as you like), \
fortify (one move between adjacent owned territories, then your turn ends).
- Reinforcements: territories owned / 3 (minimum 3), plus continent bonuses: North America 5, \
South America 2, Europe 5, Africa 3, Asia 7, Oceania 2.
- Combat: the attacker rolls 1 to 3 dice (at most one fewer than the armies in the attacking territory), \
the defender automatically rolls up to 2. Highest dice are compared pairwise; the defender wins ties. \
You may launch several attacks in one move and repeat against a single target until it falls.
- Conquering: when you capture a territory your surviving attackers advance into it; you choose how many \
stay behind (all but one by default), but at least as many armies as dice you rolled in the capturing \
battle must advance. Conquering at least one territory during your turn earns one territory card at the \
end of it.
- Cards: trade sets of three (all the same design or one of each; wilds substitute for any design). \
Bonuses: 3 infantry = 4 armies, 3 cavalry = 6, 3 artillery = 8, one of each = 10, plus 2 extra armies \
on each pictured territory you own. Holding 5 or more cards forces a trade.
- Eliminating a player transfers their cards to you; if that leaves you holding 6 or more, you must at once \
trade sets down to 4 or fewer and place the armies before attacking again. If only one player remains, they \
win regardless of missions.
- Eliminate-player missions fall back to 'conquer 24 territories' if the target is you or someone else \
eliminates them."""


def render_system_prompt(view: PlayerView) -> str:
    """Render the per-player system prompt: identity, rules summary, and secret mission.

    Args:
        view: The player's view, providing identity, opponents, and mission.

    Returns:
        The system prompt text.
    """
    opponents = ", ".join(info.player_id for info in view.players if info.player_id != view.player_id)
    privacy_note = (
        "Private messages to single players are allowed."
        if view.private_messages_allowed
        else "All messages are public; there are no private channels."
    )
    return (
        f"You are '{view.player_id}', playing the classic board game Risk against: {opponents}. "
        f"This is a friendly research-benchmark match between players: all talk of attacks, battles, "
        f"and eliminating players refers to plastic armies on a game board, exactly as at a family game "
        f"night.\n\n"
        f"{_RULES_SUMMARY}\n\n"
        f"{_render_mission_deck(view=view)}\n\n"
        f"{render_board_topology()}\n"
        f"Each turn you are shown the live board (who owns what, and army counts) separately; "
        f"combine it with this fixed map to reason about borders.\n\n"
        "Timing: a turn is one player's reinforcement, attack and fortification sequence. "
        "A round is one pass through the fixed seating order, skipping eliminated players. "
        "Round 1 starts after setup; a new round begins when the order wraps back to its first living player. "
        "Turn numbers count individual turns throughout the game and do not reset each round. "
        "For commitments, state an explicit deadline such as 'until the end of round 4' or "
        "'until the start of Alice's next turn', using the actual player id.\n\n"
        f"Diplomacy: at the start of every turn there is a negotiation session where players may exchange "
        f"messages. The session runs in passes: as long as someone said something, everyone gets another "
        f"chance to reply (up to a limit), so proposals can be answered and agreed within the same turn. "
        f"You may also attach one short message to any action you take during your turn — table talk that "
        f"others see but can only answer in the next negotiation session. "
        f"{privacy_note} Promises are not enforced by the rules. In messages, refer to players by their "
        f"exact ids (e.g. '{opponents.split(', ')[0]}') and to territories and continents by their exact "
        f"ids (e.g. 'middle_east', 'north_america'), so everyone knows precisely what you mean.\n\n"
        f"YOUR SECRET MISSION: {view.own_mission.description}\n"
        f"Other players are not shown your mission. Your objective is to win by completing your secret "
        f"mission under the game rules.\n"
        f"Always act by calling exactly one action tool, optionally adding one message tool call.\n"
        # Anthropic models refuse a system prompt that asks for "private reasoning" or "private
        # thoughts"; the same sentence without that phrasing is accepted and means the same thing.
        f"Before your tool calls, briefly state your reasoning as plain text. It is not shown to the "
        f"other players; it is recorded only for post-game analysis."
    )


def _render_mission_deck(view: PlayerView) -> str:
    # The deck is printed in the rules of the physical game: a player at a table knows which
    # missions exist and can reason about what an opponent's play implies. Withholding it would
    # make deduction about hidden goals — and any lie about one's own — unanchored.
    cards = mission_deck(player_ids=[info.player_id for info in view.players])
    listing = "\n".join(f"- {card.description}" for card in cards)
    return (
        f"The mission deck is public; only the deal is secret. It holds these {len(cards)} cards, "
        f"of which {len(view.players)} were dealt face down, one to each player:\n{listing}\n"
        f"You hold one of them and know which; the other {len(cards) - 1} are the pool every "
        f"opponent's card was dealt from. What a player attacks, defends and asks for is evidence "
        f"about which one they hold."
    )


def render_decision_prompt(request: DecisionRequest) -> str:
    """Render a decision point: notes, this turn's events and chat, board, standings, and the task.

    Only the current turn's events and chat are shown; everything older the player
    carries in its own notes. The board is serialized grouped by continent with
    owners and army counts, so the model never has to recall the map from memory.

    Args:
        request: The decision point to render.

    Returns:
        The user-turn prompt text for the decision.
    """
    view = request.view
    sections = [
        _render_timing(view),
        f"{view.phase} phase. It is your move.",
        _render_notes(view=view),
        _render_events(view=view),
        _render_chat(view=view),
        _render_additional_context(view=view),
        _render_players(view=view),
        _render_board(view=view),
        _render_hand(view=view),
        f"Reminder of your secret mission: {view.own_mission.description}",
        _phase_instruction(request=request),
        "You may optionally include ONE message tool call alongside your action tool call to address the "
        "other players; most actions need no message.",
    ]
    return "\n\n".join(section for section in sections if section)


def render_negotiation_prompt(view: PlayerView) -> str:
    """Render a negotiation opportunity: events, standings, board, and the visible chat.

    Args:
        view: The player's current view of the game.

    Returns:
        The user-turn prompt text inviting diplomacy messages.
    """
    speaker_note = (
        "It is your turn; you may open the negotiation session."
        if view.current_player == view.player_id
        else f"It is {view.current_player}'s turn; you may react."
    )
    sections = [
        _render_timing(view),
        f"Negotiation session. {speaker_note}",
        _render_notes(view=view),
        _render_events(view=view),
        _render_chat(view=view),
        _render_additional_context(view=view),
        _render_players(view=view),
        _render_board(view=view),
        f"Reminder of your secret mission: {view.own_mission.description}",
        "Send a message to the other players, or say nothing. If anyone speaks, everyone gets "
        "another chance to reply; staying silent helps end the session. Keep it short.",
    ]
    return "\n\n".join(section for section in sections if section)


def render_noting_prompt(view: PlayerView) -> str:
    """Render the end-of-turn noting prompt: the turn just played, then a rewrite instruction.

    A dedicated, neutral call: the model rereads the turn that just ended and its
    own previous notes, then rewrites a fresh, bounded notepad. These notes are
    the only memory it keeps of earlier turns, so what it records, consolidates,
    or drops is entirely its own choice.

    Args:
        view: The noting player's view, carrying the just-ended turn's events and
            chat plus the player's current notes.

    Returns:
        The user-turn prompt text inviting a notes rewrite.
    """
    sections = [
        f"Round {view.previous_round_number} · Turn {view.turn_number - 1} just ended. "
        "Update the private notes you keep for your "
        "future self before the next turn proceeds.",
        "Current board: " + _render_timing(view),
        _render_events(view=view),
        _render_chat(view=view),
        _render_additional_context(view=view),
        _render_players(view=view),
        _render_board(view=view),
        _render_notes_for_update(view=view),
        _NOTING_INSTRUCTION,
    ]
    return "\n\n".join(section for section in sections if section)


def _render_timing(view: PlayerView) -> str:
    position = (
        "Setup placement, before round 1."
        if view.turn_number == 0
        else f"Round {view.round_number} · Turn {view.turn_number} · {view.current_player}'s turn."
    )
    order = " → ".join(player.player_id for player in view.players if not player.is_eliminated)
    return f"{position}\nTurn order (living players): {order}."


# soft target the noting prompt suggests, so a long game's notes stay bounded; not enforced
_NOTES_WORD_BUDGET: Final[int] = 200

_NOTING_INSTRUCTION: Final[str] = (
    "These notes are your only memory of earlier turns: on your next turn the live board and the "
    "fixed map are always shown again, so you never need to record who owns what or how many armies "
    "sit where — only what the board cannot tell you later (intentions, promises, threats, alliances, "
    "who did what to whom, and your own plans). Rewrite your notes from scratch: keep what still "
    "matters, drop what has gone stale, and fold in anything you learned this turn. Keep them concise "
    f"(aim for under about {_NOTES_WORD_BUDGET} words). Reply with the notes themselves as plain text "
    "and nothing else."
)


# An experimental intervention is rendered as information the player has, in the same register as
# the rest of the game context: framing it as an instruction, or as the player's own recollection,
# would change what a comparison between two forks actually measures.
def _render_additional_context(view: PlayerView) -> str:
    if not view.additional_context:
        return ""
    return f"Additional information available to you:\n{view.additional_context}"


def _render_notes(view: PlayerView) -> str:
    if not view.notes:
        return ""
    return f"Your private notes (your memory of earlier turns; only you can see them):\n{view.notes}"


def _render_notes_for_update(view: PlayerView) -> str:
    if not view.notes:
        return "Your notes so far: (none yet — this is your first set.)"
    return f"Your notes so far:\n{view.notes}"


def _render_events(view: PlayerView) -> str:
    if not view.recent_events:
        return ""
    lines = "\n".join(f"- {narrate_event(event=event)}" for event in view.recent_events)
    return f"This turn so far:\n{lines}"


def _render_chat(view: PlayerView) -> str:
    if not view.visible_chat:
        return ""
    lines = "\n".join(f"- {_format_message(message=message)}" for message in view.visible_chat)
    return f"Messages this turn (most recent last):\n{lines}"


def _format_message(message: ChatMessage) -> str:
    channel = "to everyone" if message.visibility is MessageVisibility.PUBLIC else f"privately to {message.recipient}"
    return f"turn {message.turn_number}, {message.sender} {channel}: {message.text}"


def _render_players(view: PlayerView) -> str:
    lines = []
    for info in view.players:
        if info.is_eliminated:
            lines.append(f"- {info.player_id}: ELIMINATED")
        else:
            lines.append(
                f"- {info.player_id}: {info.territory_count} territories, {info.army_count} armies, "
                f"{info.card_count} cards, income {info.income}/turn"
            )
    return "Players:\n" + "\n".join(lines)


def render_board_text(owner: dict[Territory, PlayerId], armies: dict[Territory, int]) -> str:
    """Serialize the board grouped by continent with owners, army counts, and adjacency.

    Args:
        owner: The owning player of every territory.
        armies: The army count on every territory.

    Returns:
        The board as prompt text; used by the commentator, which sees one
        self-contained omniscient view per turn rather than a cached prefix.
    """
    lines = []
    for continent in Continent:
        continent_info = BOARD.continent_info(continent)
        lines.append(f"{continent} (+{continent_info.army_bonus} bonus for owning all of it):")
        for territory in sorted(continent_info.territories):
            neighbors = ", ".join(sorted(BOARD.neighbors_of(territory)))
            lines.append(f"  {territory}: {owner[territory]} with {armies[territory]} armies | borders: {neighbors}")
    return "Board:\n" + "\n".join(lines)


def render_board_topology() -> str:
    """Serialize the fixed map: continents, their bonuses, and each territory's borders.

    The topology never changes during a game, so it is sent once in the system
    prompt (and cached there) instead of being re-sent with every decision.

    Returns:
        The static map as prompt text.
    """
    lines = []
    for continent in Continent:
        continent_info = BOARD.continent_info(continent)
        lines.append(f"{continent} (+{continent_info.army_bonus} bonus for owning all of it):")
        for territory in sorted(continent_info.territories):
            neighbors = ", ".join(sorted(BOARD.neighbors_of(territory)))
            lines.append(f"  {territory} borders: {neighbors}")
    return "The map is fixed for the whole game. Territories by continent, with the bonus and borders:\n" + "\n".join(
        lines
    )


def render_board_state(owner: dict[Territory, PlayerId], armies: dict[Territory, int]) -> str:
    """Serialize the live board: who owns each territory and how many armies sit on it.

    Adjacency is omitted because it is fixed and already given by the map in the
    system prompt; only the changing ownership and army counts are sent each turn.

    Args:
        owner: The owning player of every territory.
        armies: The army count on every territory.

    Returns:
        The current board state as prompt text.
    """
    lines = []
    for continent in Continent:
        continent_info = BOARD.continent_info(continent)
        lines.append(f"{continent}:")
        for territory in sorted(continent_info.territories):
            lines.append(f"  {territory}: {owner[territory]} with {armies[territory]} armies")
    return "Current board (owner and armies per territory):\n" + "\n".join(lines)


def _render_board(view: PlayerView) -> str:
    return render_board_state(owner=view.owner, armies=view.armies)


def _render_hand(view: PlayerView) -> str:
    if not view.own_cards:
        return "Your cards: none."
    labels = ", ".join(
        "wild" if card.territory is None else f"{card.design}({card.territory})" for card in view.own_cards
    )
    return f"Your cards: {labels}"


def _phase_instruction(request: DecisionRequest) -> str:
    view = request.view
    match view.phase:
        case Phase.SETUP_PLACEMENT:
            return (
                f"Setup: place your starting armies ({view.armies_to_place} left); you can split them across "
                f"territories in one call. Other players' placements are hidden until the game begins, and "
                f"yours are hidden from them."
            )
        case Phase.REINFORCE:
            return (
                f"Reinforce: you have {view.armies_to_place} armies to place, splittable across territories "
                f"in one call. Trade cards first if you wish."
            )
        case Phase.ATTACK:
            if any(isinstance(action, TradeCards) for action in request.legal_actions):
                return (
                    f"Attack phase, but you now hold {len(view.own_cards)} cards after taking an eliminated "
                    f"player's hand: you must trade in a set before you can attack again."
                )
            if any(isinstance(action, PlaceArmies) for action in request.legal_actions):
                return (
                    f"Attack phase: place the {view.armies_to_place} armies from your forced card trade on your "
                    f"territories (splittable across territories in one call), then you can continue attacking."
                )
            return (
                "Attack: launch one or more attacks on adjacent enemy territories (you can repeat against a "
                "target until it falls), or end the attack phase."
            )
        case Phase.FORTIFY:
            return "Fortify: optionally move armies between two adjacent territories you own; this ends your turn."
        case Phase.GAME_OVER:
            return "The game is over."


def narrate_event(event: GameEvent) -> str:
    """Render one game event as a human-readable sentence.

    Args:
        event: The event to narrate.

    Returns:
        A single-sentence description, used in prompts and the replay viewer.
    """
    match event:
        case TurnStarted(player_id=player_id, turn_number=turn_number):
            return f"Turn {turn_number} started for {player_id}."
        case ArmiesPlaced(player_id=player_id, territory=territory, count=count):
            return f"{player_id} placed {count} armies on {territory}."
        case CardsTraded(player_id=player_id, card_designs=designs, bonus_armies=bonus):
            return f"{player_id} traded a card set ({', '.join(designs)}) for {bonus} armies."
        case BattleResolved(attacker=attacker, defender=defender, outcome=outcome) as battle:
            return (
                f"{attacker} attacked {battle.to_territory} from {battle.from_territory}: "
                f"rolled {list(outcome.attacker_dice)} vs {defender}'s {list(outcome.defender_dice)} — "
                f"attacker lost {outcome.attacker_losses}, defender lost {outcome.defender_losses}."
            )
        case TerritoryConquered(player_id=player_id, territory=territory, from_territory=from_territory):
            return f"{player_id} conquered {territory} from {from_territory}."
        case Fortified(player_id=player_id, from_territory=from_territory, to_territory=to_territory, count=count):
            return f"{player_id} fortified, moving {count} armies from {from_territory} to {to_territory}."
        case CardDrawn(player_id=player_id):
            return f"{player_id} earned a territory card."
        case PlayerEliminated(eliminated_player=eliminated_player, eliminated_by=eliminated_by):
            return f"{eliminated_player} was ELIMINATED by {eliminated_by}, who takes their cards."
        case MissionCompleted(player_id=player_id, mission_description=description):
            return f"{player_id} WON the game by completing their mission: {description}"
        case TurnLimitReached(turn_number=turn_number):
            return f"The game hit the turn limit at turn {turn_number} and ends in a draw."


def describe_action(player_id: PlayerId, action: Action) -> str:
    """Render one applied action as a short present-tense sentence.

    Used by the replay viewer and the evaluator's trace index, so a citation and
    a replay step describe the same move identically.

    Args:
        player_id: The player who took the action.
        action: The action to describe.

    Returns:
        A single-sentence description of the action.
    """
    match action:
        case PlaceArmies(territory=territory, count=count):
            return f"{player_id} places {count} armies on {territory}"
        case TradeCards(cards=cards):
            labels = ", ".join(
                "wild" if card.territory is None else f"{card.design}({card.territory})" for card in cards
            )
            return f"{player_id} trades in {labels}"
        case Attack(
            from_territory=from_territory, to_territory=to_territory, attacker_dice=dice, max_attacks=max_attacks
        ):
            repeat = f" up to {max_attacks} times" if max_attacks > 1 else ""
            return f"{player_id} attacks {to_territory} from {from_territory} with {dice} dice{repeat}"
        case Fortify(from_territory=from_territory, to_territory=to_territory, count=count):
            return f"{player_id} fortifies {count} armies from {from_territory} to {to_territory}"
        case EndAttackPhase():
            return f"{player_id} ends the attack phase"
        case SkipFortify():
            return f"{player_id} ends the turn without fortifying"
