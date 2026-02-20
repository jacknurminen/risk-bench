from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass
class BattleResult:
    attacker_losses: int
    defender_losses: int
    attacker_dice: list[int]
    defender_dice: list[int]


def roll_dice(count: int) -> list[int]:
    """Roll dice and return results sorted descending.

    Args:
        count: Number of dice to roll.

    Returns:
        List of die results sorted from highest to lowest.
    """
    return sorted([random.randint(1, 6) for _ in range(count)], reverse=True)


def resolve_battle(attackers: int, defenders: int) -> BattleResult:
    """Resolve one round of Risk combat.

    Args:
        attackers: Number of attacking armies committed (1-3).
        defenders: Number of defending armies (1-2).

    Returns:
        BattleResult containing losses and dice rolled for each side.
    """
    attacker_dice_count = min(attackers, 3)
    defender_dice_count = min(defenders, 2)

    attacker_dice_rolls = roll_dice(count=attacker_dice_count)
    defender_dice_rolls = roll_dice(count=defender_dice_count)

    pairs = min(len(attacker_dice_rolls), len(defender_dice_rolls))
    attacker_losses = 0
    defender_losses = 0

    for i in range(pairs):
        if attacker_dice_rolls[i] > defender_dice_rolls[i]:
            defender_losses += 1
        else:
            # Defender wins ties
            attacker_losses += 1

    return BattleResult(
        attacker_losses=attacker_losses,
        defender_losses=defender_losses,
        attacker_dice=attacker_dice_rolls,
        defender_dice=defender_dice_rolls,
    )


def run_full_attack(
    attacker_troops: int,
    defender_troops: int,
    attackers: int,
) -> tuple[int, int, list[BattleResult]]:
    """Run combat until the territory is taken or the attacker can no longer attack.

    The attacker territory must keep at least 1 army; that bookkeeping is done
    in the engine — this function receives the available attacking troops directly.

    Args:
        attacker_troops: Troops available to attack with (territory count minus the 1 kept back).
        defender_troops: Troops in the defending territory.
        attackers: Max armies to commit per round (1-3).

    Returns:
        Tuple of (remaining_attacker_troops, remaining_defender_troops, rounds_played).
    """
    rounds: list[BattleResult] = []
    remaining_attackers = attacker_troops
    remaining_defenders = defender_troops

    while remaining_defenders > 0 and remaining_attackers > 0:
        committed_armies = min(attackers, remaining_attackers, 3)
        if committed_armies < 1:
            break
        result = resolve_battle(attackers=committed_armies, defenders=remaining_defenders)
        remaining_attackers -= result.attacker_losses
        remaining_defenders -= result.defender_losses
        rounds.append(result)
        if remaining_attackers == 0:
            break

    return remaining_attackers, remaining_defenders, rounds
