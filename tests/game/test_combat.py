import random

from risk_bench.game.combat import max_attacker_dice, max_defender_dice, resolve_battle


class ScriptedRandom(random.Random):
    """Returns predetermined dice rolls; attacker dice are rolled before defender dice."""

    def __init__(self, rolls: list[int]) -> None:
        super().__init__()
        self._rolls = rolls

    def randint(self, a: int, b: int) -> int:
        return self._rolls.pop(0)


def test_higher_attacker_dice_win_and_ties_go_to_the_defender():
    outcome = resolve_battle(attacker_dice_count=3, defender_dice_count=2, rng=ScriptedRandom([3, 5, 4, 5, 3]))

    assert outcome.attacker_dice == (5, 4, 3)
    assert outcome.defender_dice == (5, 3)
    assert outcome.attacker_losses == 1  # 5 ties 5, defender wins
    assert outcome.defender_losses == 1  # 4 beats 3


def test_one_die_each_tie_goes_to_the_defender():
    outcome = resolve_battle(attacker_dice_count=1, defender_dice_count=1, rng=ScriptedRandom([4, 4]))

    assert outcome.attacker_losses == 1
    assert outcome.defender_losses == 0


def test_only_the_lowest_common_dice_count_causes_losses():
    outcome = resolve_battle(attacker_dice_count=3, defender_dice_count=1, rng=ScriptedRandom([6, 6, 6, 1]))

    assert outcome.attacker_losses == 0
    assert outcome.defender_losses == 1


def test_total_losses_always_equal_the_compared_dice_count():
    for seed in range(50):
        rng = random.Random(seed)
        for attacker_dice_count in (1, 2, 3):
            for defender_dice_count in (1, 2):
                outcome = resolve_battle(
                    attacker_dice_count=attacker_dice_count, defender_dice_count=defender_dice_count, rng=rng
                )
                assert len(outcome.attacker_dice) == attacker_dice_count
                assert len(outcome.defender_dice) == defender_dice_count
                assert all(1 <= die <= 6 for die in outcome.attacker_dice + outcome.defender_dice)
                expected_losses = min(attacker_dice_count, defender_dice_count)
                assert outcome.attacker_losses + outcome.defender_losses == expected_losses


def test_max_attacker_dice_requires_one_army_to_stay_behind():
    assert max_attacker_dice(armies_in_territory=1) == 0
    assert max_attacker_dice(armies_in_territory=2) == 1
    assert max_attacker_dice(armies_in_territory=3) == 2
    assert max_attacker_dice(armies_in_territory=4) == 3
    assert max_attacker_dice(armies_in_territory=10) == 3


def test_max_defender_dice_is_capped_at_two():
    assert max_defender_dice(armies_in_territory=1) == 1
    assert max_defender_dice(armies_in_territory=2) == 2
    assert max_defender_dice(armies_in_territory=9) == 2
