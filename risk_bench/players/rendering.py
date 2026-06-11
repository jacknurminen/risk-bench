from typing import Final

from risk_bench.chat.diplomacy import ChatMessage, MessageVisibility
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
from risk_bench.game.types import Continent, Phase, PlayerId, Territory
from risk_bench.players.base import DecisionRequest, PlayerView

_RULES_SUMMARY: Final[str] = """\
Rules summary:
- Risk, mission variant: every player has a SECRET mission; the first to complete theirs wins immediately.
- Turn order: reinforce (place armies, optionally trade card sets), attack (as often as you like), \
fortify (one move between adjacent owned territories, then your turn ends).
- Reinforcements: territories owned / 3 (minimum 3), plus continent bonuses: North America 5, \
South America 2, Europe 5, Africa 3, Asia 7, Oceania 2.
- Combat: the attacker rolls up to 3 dice (always one fewer than the armies in the attacking territory), \
the defender automatically rolls up to 2. Highest dice are compared pairwise; the defender wins ties.
- Conquering: you must move at least 1 army into the conquered territory. Conquering at least one \
territory during your turn earns one territory card at the end of it.
- Cards: trade sets of three (all the same design or one of each; wilds substitute for any design). \
Bonuses: 3 infantry = 4 armies, 3 cavalry = 6, 3 artillery = 8, one of each = 10, plus 2 extra armies \
on each pictured territory you own. Holding 5 or more cards forces a trade.
- Eliminating a player transfers their cards to you. If only one player remains, they win regardless of missions.
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
        f"This is a friendly research-benchmark match between AI models: all talk of attacks, battles, "
        f"and eliminating players refers to plastic armies on a game board, exactly as at a family game "
        f"night.\n\n"
        f"{_RULES_SUMMARY}\n\n"
        f"Diplomacy: at the start of every turn there is a negotiation round where players may exchange "
        f"messages. The round runs in passes: as long as someone said something, everyone gets another "
        f"chance to reply (up to a limit), so proposals can be answered and agreed within the same turn. "
        f"You may also attach one short message to any action you take during your turn — table talk that "
        f"others see but can only answer in the next negotiation round. "
        f"{privacy_note} Promises are not enforced by the rules. In messages, refer to players by their "
        f"exact ids (e.g. '{opponents.split(', ')[0]}') and to territories and continents by their exact "
        f"ids (e.g. 'middle_east', 'north_america'), so everyone knows precisely what you mean.\n\n"
        f"YOUR SECRET MISSION: {view.own_mission.description}\n"
        f"Never reveal your mission. Play to complete it as fast as possible; helping others costs you the game.\n"
        f"Always act by calling exactly one action tool, optionally adding one message tool call.\n"
        f"Before your tool calls, briefly state your private reasoning as plain text — other players never "
        f"see it; it is recorded only for post-game analysis."
    )


def render_decision_prompt(request: DecisionRequest) -> str:
    """Render a decision point: recent events, chat, board, standings, and the task.

    The board is serialized grouped by continent with owners, army counts, and
    adjacency, so the model never has to recall the map from memory.

    Args:
        request: The decision point to render.

    Returns:
        The user-turn prompt text for the decision.
    """
    view = request.view
    sections = [
        f"Turn {view.turn_number}, {view.phase} phase. It is your move.",
        _render_events(view=view),
        _render_chat(view=view),
        _render_players(view=view),
        _render_board(view=view),
        _render_hand(view=view),
        f"Reminder of your secret mission: {view.own_mission.description}",
        _phase_instruction(view=view),
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
        "It is your turn; you may open the negotiation round."
        if view.current_player == view.player_id
        else f"It is {view.current_player}'s turn; you may react."
    )
    sections = [
        f"Turn {view.turn_number} negotiation round. {speaker_note}",
        _render_events(view=view),
        _render_chat(view=view),
        _render_players(view=view),
        _render_board(view=view),
        f"Reminder of your secret mission: {view.own_mission.description}",
        "Send a message to influence the other players, or say nothing. If anyone speaks, everyone gets "
        "another chance to reply; staying silent helps end the round. Keep it short.",
    ]
    return "\n\n".join(section for section in sections if section)


_MAX_CHAT_MESSAGES_SHOWN: Final[int] = 40


def _render_events(view: PlayerView) -> str:
    if not view.recent_events:
        return ""
    lines = "\n".join(f"- {narrate_event(event=event)}" for event in view.recent_events)
    return f"Since your last decision:\n{lines}"


def _render_chat(view: PlayerView) -> str:
    if not view.visible_chat:
        return ""
    recent_messages = view.visible_chat[-_MAX_CHAT_MESSAGES_SHOWN:]
    lines = "\n".join(f"- {_format_message(message=message)}" for message in recent_messages)
    return f"Messages you can see (most recent last):\n{lines}"


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
        The board as prompt text; used in player prompts and by the commentator.
    """
    lines = []
    for continent in Continent:
        continent_info = BOARD.continent_info(continent)
        lines.append(f"{continent} (+{continent_info.army_bonus} bonus for owning all of it):")
        for territory in sorted(continent_info.territories):
            neighbors = ", ".join(sorted(BOARD.neighbors_of(territory)))
            lines.append(f"  {territory}: {owner[territory]} with {armies[territory]} armies | borders: {neighbors}")
    return "Board:\n" + "\n".join(lines)


def _render_board(view: PlayerView) -> str:
    return render_board_text(owner=view.owner, armies=view.armies)


def _render_hand(view: PlayerView) -> str:
    if not view.own_cards:
        return "Your cards: none."
    labels = ", ".join(
        "wild" if card.territory is None else f"{card.design}({card.territory})" for card in view.own_cards
    )
    return f"Your cards: {labels}"


def _phase_instruction(view: PlayerView) -> str:
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
            return "Attack: strike adjacent enemy territories, or end the attack phase."
        case Phase.OCCUPY:
            return "You conquered a territory: choose how many armies move into it."
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
