"""Dice rolling and battle resolution for Risk."""

from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass
class BattleResult:
    attacker_losses: int
    defender_losses: int
    attacker_dice: list[int]
    defender_dice: list[int]


def roll_dice(n: int) -> list[int]:
    """Roll n dice and return sorted descending."""
    return sorted([random.randint(1, 6) for _ in range(n)], reverse=True)


def resolve_battle(attackers: int, defenders: int) -> BattleResult:
    """
    Resolve one round of Risk combat.
    attackers: number of attacking armies committed (1-3)
    defenders: number of defending armies (1-2)
    Returns losses for each side.
    """
    atk_dice_count = min(attackers, 3)
    def_dice_count = min(defenders, 2)

    atk_dice = roll_dice(atk_dice_count)
    def_dice = roll_dice(def_dice_count)

    pairs = min(len(atk_dice), len(def_dice))
    atk_losses = 0
    def_losses = 0

    for i in range(pairs):
        if atk_dice[i] > def_dice[i]:
            def_losses += 1
        else:
            # defender wins ties
            atk_losses += 1

    return BattleResult(
        attacker_losses=atk_losses,
        defender_losses=def_losses,
        attacker_dice=atk_dice,
        defender_dice=def_dice,
    )


def run_full_attack(attacker_troops: int, defender_troops: int, attackers: int) -> tuple[int, int, list[BattleResult]]:
    """
    Run combat until territory is taken or attacker can no longer attack.
    attackers: max armies to commit per round (1-3).
    Returns (remaining_attacker_troops, remaining_defender_troops, rounds).
    The attacker territory must keep at least 1 army, so attacking territory
    starts with attacker_troops and must keep 1 — that bookkeeping is done in the engine.
    """
    rounds: list[BattleResult] = []
    atk = attacker_troops
    defn = defender_troops

    while defn > 0 and atk > 0:
        commit = min(attackers, atk, 3)
        if commit < 1:
            break
        result = resolve_battle(commit, defn)
        atk -= result.attacker_losses
        defn -= result.defender_losses
        rounds.append(result)
        if atk == 0:
            break

    return atk, defn, rounds
