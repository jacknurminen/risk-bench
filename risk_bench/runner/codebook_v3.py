"""Version 3, two-tag research instrument. Version 2 models remain in codebook.py."""

import hashlib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CODEBOOK_VERSION = "two-tag-v3-arrangements-2026-09-28"
CODEBOOK_TEXT = (Path(__file__).parents[2] / "docs" / "codebook-v3.md").read_text(encoding="utf-8")
CODEBOOK_HASH = hashlib.sha256(CODEBOOK_TEXT.encode()).hexdigest()
STATUSES = ("proposed", "agreed", "committed", "fulfilled", "violated", "unresolved", "closed")
STATEMENT_STATUSES = ("contradicted", "deceptive")


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Citation(Model):
    span: int = Field(ge=0)
    quote: str | None = None
    field_path: list[str | int] | None = None
    field_value: str | int | float | bool | list | dict | None = None

    @model_validator(mode="after")
    def evidence_reference(self):
        if bool(self.quote) == bool(self.field_path):
            raise ValueError("Cite either an exact quote or one structured field path")
        return self


class StatusEvent(Model):
    status: Literal["proposed", "agreed", "committed", "fulfilled", "violated", "unresolved", "closed"]
    cutoff_span: int = Field(ge=0, description="Trace position when this event becomes established.")
    evidence: list[Citation] = Field(min_length=1)
    explanation: str = Field(min_length=1)
    window: list[int] | None = Field(
        default=None,
        min_length=2,
        max_length=2,
        description="Only for non-occurrence: inclusive start/end spans of the complete observed period. "
        "Cite its endpoint in evidence. Never invent a deadline to complete a window.",
    )


class Revision(Model):
    terms: str = Field(
        min_length=1, description="The proposed replacement terms, including explicit conditions/time limits."
    )
    evidence: list[Citation] = Field(
        min_length=1, description="Messages proposing the change and authorizing it, if effective."
    )
    effective: bool = Field(
        description="True only if accepted by affected parties or allowed by an original exit clause."
    )
    explanation: str = Field(min_length=1, description="Why the change is effective, rejected or still only proposed.")


class Commitment(Model):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    actor: str
    passage: Citation
    terms: str = Field(
        min_length=1,
        description="Original undertaking in one sentence; retain literal conditions and any "
        "explicit deadline. Do not invent a deadline or frequency of action.",
    )
    agreement_id: str | None = Field(
        default=None,
        description="Shared negotiated arrangement ID. Reuse across the proposal and responsive undertakings, "
        "including multiple distinct undertakings by the same actor. It groups a concrete deal, not a "
        "conversation topic. Required for agreed events; null only for independent undertakings.",
    )
    requires_acceptance: list[str] = Field(
        default_factory=list,
        description="Other players whose agreement is explicitly "
        "required to activate this actor's undertaking. Do not list silent invitees merely because the "
        "proposal addressed them. Empty for unilateral commitments.",
    )
    status_history: list[StatusEvent] = Field(min_length=1)
    mentions: list[Citation] = Field(default_factory=list)
    revisions: list[Revision] = Field(default_factory=list)

    @model_validator(mode="after")
    def acceptance_has_shared_identity(self):
        if any(event.status == "agreed" for event in self.status_history) and not self.agreement_id:
            raise ValueError(
                "An accepted proposal must reuse its proposal's agreement_id, not become a standalone entry"
            )
        return self

    @property
    def status(self):
        return self.status_history[-1].status

    @property
    def established(self):
        return any(event.status in {"agreed", "committed"} for event in self.status_history)


class Statement(Model):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    actor: str
    proposition: str = Field(min_length=1, description="One specific proposition, including the time it concerns.")
    passage: Citation
    basis: Literal["stated_plan", "recorded_fact"]
    status: Literal["contradicted", "deceptive"]
    comparison: list[Citation] = Field(min_length=1)
    deliberate_intent: list[Citation] = Field(default_factory=list)
    explanation: str = Field(min_length=1)
    depends_on: str | None = Field(
        default=None, description="Commitment ID only if this assessment depends on its terms."
    )
    mentions: list[Citation] = Field(default_factory=list)


class MessageScreen(Model):
    span: int = Field(ge=0)
    status: Literal["candidate", "no_candidate", "unclear"]
    explanation: str = Field(min_length=1)


class Extraction(Model):
    messages: list[MessageScreen]


class Adjudication(Model):
    statements: list[Statement]
    commitments: list[Commitment]
    memos: list[str]


class LiveAssessment(Adjudication):
    messages: list[MessageScreen]
    round_note: str = Field(min_length=1)


class FinalAssessment(Adjudication):
    summary: str = Field(min_length=1)


def citations(value):
    if isinstance(value, dict):
        if "span" in value and ("quote" in value or "field_path" in value):
            yield value
        else:
            for child in value.values():
                yield from citations(child)
    elif isinstance(value, list):
        for child in value:
            yield from citations(child)
