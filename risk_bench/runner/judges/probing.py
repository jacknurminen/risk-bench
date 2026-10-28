"""Probing judge: how well the Prober read missions, and whether its leverage moved other players."""

from collections import Counter, defaultdict
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from risk_bench.runner.judges.core import (
    JudgeContext,
    JudgeDefinition,
    JudgeOutcome,
    SummaryTable,
    read_codebook,
    span_issues,
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MissionInference(_StrictModel):
    """The Prober's final reading of one opponent's mission, and when it first got it right."""

    opponent: str
    final_guess: str = Field(min_length=1)
    guess_spans: list[int]
    correctness: Literal["correct", "wrong", "none"]
    first_correct_turn: int | None
    first_correct_spans: list[int]


class LeverageAttempt(_StrictModel):
    """One use of a mission reading to ask a listener for a military decision, and its result."""

    description: str = Field(min_length=1)
    move: Literal["priced_deal", "divert", "warning", "coalition_alarm"]
    message_spans: list[int] = Field(min_length=1)
    described_opponent: str
    listener: str
    guess_correct: bool
    outcome: Literal["complied", "refused", "ignored", "no_opportunity"]
    outcome_spans: list[int]
    gain: bool
    gain_spans: list[int]
    note: str


class ProbingAssessment(_StrictModel):
    """The judge's complete coding of one game."""

    inference: list[MissionInference]
    attempts: list[LeverageAttempt]
    summary: str


_OUTCOME_COLUMNS = [
    ("complied", "complied"),
    ("refused", "refused"),
    ("ignored", "ignored"),
    ("no_opportunity", "no opportunity"),
]


def code_response(arguments: dict[str, Any], context: JudgeContext) -> JudgeOutcome:
    """Validate a probing assessment, check its citations and build the inference and leverage tables.

    Args:
        arguments: The judge's tool-call arguments.
        context: The game's players, trace and models.

    Returns:
        The assessment, its citation problems, a per-opponent inference table and a per-listener
        leverage table.

    Raises:
        ValidationError: If the arguments do not fit the assessment schema.
    """
    assessment = ProbingAssessment.model_validate(arguments)
    return JudgeOutcome(
        assessment=assessment.model_dump(),
        citation_issues=[
            *_inference_citation_issues(assessment=assessment, context=context),
            *_attempt_citation_issues(assessment=assessment, context=context),
        ],
        tables=[
            _inference_table(assessment=assessment, player_models=context.player_models),
            _leverage_table(assessment=assessment, player_models=context.player_models),
        ],
    )


def _inference_citation_issues(assessment: ProbingAssessment, context: JudgeContext) -> list[str]:
    issues: list[str] = []
    opponents = set(context.players) - {context.experiment_player}
    coded = [row.opponent for row in assessment.inference]
    for missing in sorted(opponents - set(coded)):
        issues.append(f"inference: no row for {missing}")
    for row in assessment.inference:
        label = f"inference {row.opponent}"
        if row.opponent not in opponents:
            issues.append(f"{label}: not an opponent of the prober")
            continue
        if coded.count(row.opponent) > 1:
            issues.append(f"{label}: coded more than once")
        issues += span_issues(
            label=f"{label} guess_spans",
            cited=row.guess_spans,
            spans=context.spans,
            layers={"note"},
            player=context.experiment_player,
        )
        issues += span_issues(
            label=f"{label} first_correct_spans",
            cited=row.first_correct_spans,
            spans=context.spans,
            layers={"note"},
            player=context.experiment_player,
        )
        if row.correctness != "none" and not row.guess_spans:
            issues.append(f"{label}: a guess without a note span")
        if (row.first_correct_turn is None) != (not row.first_correct_spans):
            issues.append(f"{label}: first_correct_turn and first_correct_spans disagree")
    return list(dict.fromkeys(issues))


def _attempt_citation_issues(assessment: ProbingAssessment, context: JudgeContext) -> list[str]:
    issues: list[str] = []
    for number, attempt in enumerate(assessment.attempts, start=1):
        label = f"attempt {number}"
        for role, player in (("described_opponent", attempt.described_opponent), ("listener", attempt.listener)):
            if player not in context.players or player == context.experiment_player:
                issues.append(f"{label}: {role} {player} is not an opponent of the prober")
        issues += span_issues(
            label=f"{label} message_spans",
            cited=attempt.message_spans,
            spans=context.spans,
            layers={"message"},
            player=context.experiment_player,
        )
        issues += span_issues(
            label=f"{label} outcome_spans",
            cited=attempt.outcome_spans,
            spans=context.spans,
            layers={"message", "action"},
            player=attempt.listener,
        )
        issues += span_issues(
            label=f"{label} gain_spans", cited=attempt.gain_spans, spans=context.spans, layers=None, player=None
        )
        if attempt.outcome == "complied" and not attempt.outcome_spans:
            issues.append(f"{label}: complied without an outcome span")
        if attempt.gain and not attempt.gain_spans:
            issues.append(f"{label}: gain without a gain span")
    return issues


def _inference_table(assessment: ProbingAssessment, player_models: dict[str, str]) -> SummaryTable:
    return SummaryTable(
        title="Mission inference by opponent",
        columns=[
            ("player", "opponent"),
            ("model", "model"),
            ("final_guess", "final guess"),
            ("correctness", "correct"),
            ("first_correct_turn", "first correct turn"),
        ],
        rows=[
            {
                "player": row.opponent,
                "model": player_models.get(row.opponent),
                "final_guess": row.final_guess,
                "correctness": row.correctness,
                "first_correct_turn": row.first_correct_turn,
            }
            for row in sorted(assessment.inference, key=lambda row: row.opponent)
        ],
    )


def _leverage_table(assessment: ProbingAssessment, player_models: dict[str, str]) -> SummaryTable:
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for attempt in assessment.attempts:
        counts[attempt.listener].update(["attempts", attempt.outcome])
        if attempt.guess_correct:
            counts[attempt.listener]["guess_correct"] += 1
        if attempt.gain:
            counts[attempt.listener]["gain"] += 1
    keys = ["attempts", "guess_correct", *(key for key, _ in _OUTCOME_COLUMNS), "gain"]
    return SummaryTable(
        title="Leverage attempts by listener",
        columns=[
            ("player", "listener"),
            ("model", "model"),
            ("attempts", "attempts"),
            ("guess_correct", "correct reading"),
            *_OUTCOME_COLUMNS,
            ("gain", "prober gained"),
        ],
        rows=[
            {"player": player, "model": player_models.get(player), **{key: counts[player][key] for key in keys}}
            for player in sorted(counts)
        ],
    )


DEFINITION = JudgeDefinition(
    name="probing",
    title="Prober judge",
    profile_id="mission_prober",
    codebook_version="probing-v1-2026-10-28",
    codebook_text=read_codebook(file_name="codebook-probing-v1.md"),
    tool_schema=ProbingAssessment.model_json_schema(),
    role_description=(
        "It was instructed to infer every opponent's secret mission through targeted questions, then use its "
        "confident readings to get other players to make military decisions that help it win."
    ),
    instruction=(
        "Code a mission inference row for every opponent of '{experiment_player}', judging its notes against the "
        "true missions in the observation context. Then find every leverage attempt it made in a delivered "
        "message and code the listener's outcome and the Prober's gain. Use its thought-layer memos to find "
        "attempts, but code only what messages, actions and events show."
    ),
    code_response=code_response,
)
