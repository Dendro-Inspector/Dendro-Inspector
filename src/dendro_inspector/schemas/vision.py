"""Card-independent visual proposals, isolated from the production evidence ledger."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from dendro_inspector.schemas.base import Contract, Identifier, ShortText
from dendro_inspector.schemas.taxon import Resolution


class VisionSubject(Contract):
    subject_id: Identifier
    image_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=16)
    description: ShortText


class VisionObservation(Contract):
    observation_id: Identifier
    subject_id: Identifier
    image_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=16)
    description: ShortText


class VisionHypothesis(Contract):
    subject_id: Identifier
    taxon: Identifier
    resolution: Resolution
    observation_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=32)
    limitations: tuple[ShortText, ...] = Field(default=(), max_length=8)


class VisionAssessment(Contract):
    """Its identifiers are local to this assessment, never production evidence IDs."""

    subjects: tuple[VisionSubject, ...] = Field(default=(), max_length=16)
    observations: tuple[VisionObservation, ...] = Field(default=(), max_length=64)
    hypotheses: tuple[VisionHypothesis, ...] = Field(default=(), max_length=32)
    missing_diagnostics: tuple[ShortText, ...] = Field(default=(), max_length=16)
    limitations: tuple[ShortText, ...] = Field(default=(), max_length=16)

    @model_validator(mode="after")
    def _references_are_scoped(self) -> VisionAssessment:
        subjects = {subject.subject_id: subject for subject in self.subjects}
        observations = {obs.observation_id: obs for obs in self.observations}
        if len(subjects) != len(self.subjects) or len(observations) != len(self.observations):
            raise ValueError("vision subject and observation IDs must be unique")
        for obs in self.observations:
            if obs.subject_id not in subjects:
                raise ValueError("vision observation names an unknown subject")
            if not set(obs.image_ids) <= set(subjects[obs.subject_id].image_ids):
                raise ValueError("vision observation cites an image outside its subject")
        for hypothesis in self.hypotheses:
            if hypothesis.subject_id not in subjects or hypothesis.resolution is Resolution.UNKNOWN:
                raise ValueError("vision hypothesis must name a known subject and taxonomic level")
            if any(
                ref not in observations or observations[ref].subject_id != hypothesis.subject_id
                for ref in hypothesis.observation_ids
            ):
                raise ValueError("vision hypothesis cites missing or foreign observations")
        return self


class VisionShadowResult(Contract):
    status: Literal["completed", "skipped", "failed"]
    reason: str | None = Field(default=None, max_length=120)
    assessment: VisionAssessment | None = None
    prompt_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    adapter: str | None = None
    model: str | None = None
    duration_ms: float = Field(default=0, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    attempts: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _completion_has_an_assessment(self) -> VisionShadowResult:
        if (self.status == "completed") != (self.assessment is not None):
            raise ValueError("only a completed shadow result carries an assessment")
        return self
