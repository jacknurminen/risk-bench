"""Maps each experiment profile to its single-call judge."""

from risk_bench.runner.judges import deception, gaslighting, probing
from risk_bench.runner.judges.core import JudgeDefinition

_JUDGES = {judge.profile_id: judge for judge in (probing.DEFINITION, deception.DEFINITION, gaslighting.DEFINITION)}


def judge_for_profile(profile_id: str | None) -> JudgeDefinition | None:
    """Return the judge for an experiment profile.

    Args:
        profile_id: The experiment player's profile id, or None when the game has none.

    Returns:
        The profile's judge, or None when the profile has no judge.
    """
    if profile_id is None:
        return None
    return _JUDGES.get(profile_id)
