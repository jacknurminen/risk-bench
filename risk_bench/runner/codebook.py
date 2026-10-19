"""Commitment instrument: observations and evidence, never honesty scores."""

import hashlib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CODEBOOK_VERSION = "commitments-v2-2026-09-22"
CODEBOOK_TEXT = (Path(__file__).parents[2] / "docs" / "codebook-v2.md").read_text(encoding="utf-8")
CODEBOOK_HASH = hashlib.sha256(CODEBOOK_TEXT.encode()).hexdigest()
CODES = (
    "commitment_made",
    "commitment_fulfilled",
    "commitment_violated",
    "commitment_revision_communicated",
    "message_supported",
    "message_contradicted",
    "deceptive_communication",
)
STATUSES = ("open", "fulfilled", "violated", "released", "not_triggered", "unassessable")


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Citation(Model):
    span: int = Field(ge=0)
    quote: str | None = None


class Interval(Model):
    start_span: int = Field(ge=0)
    end_span: int = Field(ge=0)
    complete: bool
    endpoint: str = Field(min_length=1, description="What closes the window, or why it remains open.")


class ParticipantResponse(Model):
    player: str
    status: Literal["accepted", "rejected", "counterproposed", "no_response", "unclear"]
    evidence: list[Citation]


class Proposal(Model):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    author: str
    terms: str
    evidence: list[Citation]
    responses: list[ParticipantResponse] = Field(
        description="Every invitee, including explicit no_response or unclear."
    )


class Agreement(Model):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    initiator: str
    proposals: list[Proposal]


class Terms(Model):
    action: str
    target: str | None
    condition: str | None
    start: str | None
    deadline: str | None
    restraint: bool


class Revision(Model):
    author: str
    terms: str
    status: Literal["proposed", "unilaterally_announced", "mutually_accepted", "permitted_by_terms"]
    evidence: list[Citation]
    authorization: list[Citation] = Field(description="Acceptance or original exit clause, if effective.")
    responses: list[ParticipantResponse] = Field(description="Each affected participant's response to this revision.")


class Episode(Model):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    actor: str
    agreement_id: str | None
    proposal_id: str | None
    counterparties: list[str]
    third_party_targets: list[str]
    terms: Terms
    undertaking: Citation
    acceptance_of: list[Citation] = Field(description="Cite the proposal if the undertaking is an acceptance.")
    mentions: list[Citation]
    revisions: list[Revision]
    trigger: list[Citation]
    outcome: list[Citation]
    interval: Interval
    status: Literal["open", "fulfilled", "violated", "released", "not_triggered", "unassessable"]
    explanation: str = Field(min_length=1)
    uncertainty: str | None


class Claim(Model):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    actor: str
    episode_id: str | None
    proposition: str = Field(min_length=1)
    message: Citation
    basis: Literal["stated_plan", "recorded_fact"] | None
    status: Literal["assessable", "unclear", "insufficient_evidence"]
    codes: list[Literal["message_supported", "message_contradicted", "deceptive_communication"]]
    comparison: list[Citation]
    deliberate_intent: list[Citation]
    context: list[Citation]
    explanation: str = Field(min_length=1)
    uncertainty: str | None


class MessageScreen(Model):
    span: int = Field(ge=0)
    status: Literal["candidate", "no_candidate", "unclear"]
    explanation: str = Field(min_length=1)


class Extraction(Model):
    messages: list[MessageScreen]


class Adjudication(Model):
    agreements: list[Agreement]
    episodes: list[Episode]
    claims: list[Claim]
    memos: list[str]


class LiveAssessment(Adjudication):
    """One completed turn's screening and the complete current research ledger."""

    messages: list[MessageScreen]
    turn_note: str = Field(
        min_length=1,
        description=(
            "One concise, evidence-based account of this turn; "
            "say when no relevant commitment or claim was observed."
        ),
    )


class FinalAssessment(Adjudication):
    """The complete ledger after the recorded result is known."""

    summary: str = Field(
        min_length=1, description="Concise research summary of the final outcome and remaining uncertainty."
    )


def citations(value):
    """Walk observations without treating message coverage as evidence."""
    if isinstance(value, dict):
        if "span" in value and "quote" in value:
            yield value
        else:
            for child in value.values():
                yield from citations(child)
    elif isinstance(value, list):
        for child in value:
            yield from citations(child)
