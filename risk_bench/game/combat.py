import random
from dataclasses import dataclass


@dataclass(frozen=True)
class BattleOutcome:
    """The result of one dice battle: the rolls and the armies each side lost."""

    attacker_dice: tuple[int, ...]
    defender_dice: tuple[int, ...]
    attacker_losses: int
    defender_losses: int


def resolve_battle(attacker_dice_count: int, defender_dice_count: int, rng: random.Random) -> BattleOutcome:
    """Roll the dice for one battle and determine losses.

    Dice are sorted descending and compared pairwise; the defender wins ties.
    Attacker dice are rolled before defender dice.

    Args:
        attacker_dice_count: Number of dice the attacker rolls (1-3).
        defender_dice_count: Number of dice the defender rolls (1-2).
        rng: Source of randomness for the dice rolls.

    Returns:
        The rolled dice (sorted descending) and resulting army losses for both sides.
    """
    attacker_dice = sorted((rng.randint(1, 6) for _ in range(attacker_dice_count)), reverse=True)
    defender_dice = sorted((rng.randint(1, 6) for _ in range(defender_dice_count)), reverse=True)
    attacker_losses = sum(1 for attacker, defender in zip(attacker_dice, defender_dice) if attacker <= defender)
    defender_losses = min(attacker_dice_count, defender_dice_count) - attacker_losses
    return BattleOutcome(
        attacker_dice=tuple(attacker_dice),
        defender_dice=tuple(defender_dice),
        attacker_losses=attacker_losses,
        defender_losses=defender_losses,
    )


def max_attacker_dice(armies_in_territory: int) -> int:
    """Return the most dice an attacker may roll from a territory with the given armies.

    Args:
        armies_in_territory: Armies in the attacking territory (one must stay behind).

    Returns:
        The maximum attacker dice count (0-3); 0 means the territory cannot attack.
    """
    return max(0, min(3, armies_in_territory - 1))


def max_defender_dice(armies_in_territory: int) -> int:
    """Return the dice a defender rolls for a territory with the given armies.

    The defender always rolls the maximum — defending with fewer dice is a
    near-degenerate choice, so it is not exposed as a decision.

    Args:
        armies_in_territory: Armies in the defending territory.

    Returns:
        The defender dice count (1-2).
    """
    return min(2, armies_in_territory)
