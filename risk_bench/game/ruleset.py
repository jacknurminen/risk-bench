from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True)
class Ruleset:
    """Which optional rulebook clauses the engine enforces.

    The ruleset is part of the game state so that a journal replays under the
    rules it was played with: a game recorded before a clause was enforced must
    not be re-judged by it, or its own recorded actions become illegal.
    """

    # at least as many armies as dice rolled in the capturing battle must advance into a conquest
    minimum_advance_on_capture: bool
    # a hand of six or more cards taken from an eliminated player must be traded before attacking resumes
    forced_trade_during_attack: bool


CURRENT_RULESET: Final[Ruleset] = Ruleset(minimum_advance_on_capture=True, forced_trade_during_attack=True)
# games journaled before schema version 3 were played without either clause
LEGACY_RULESET: Final[Ruleset] = Ruleset(minimum_advance_on_capture=False, forced_trade_during_attack=False)
