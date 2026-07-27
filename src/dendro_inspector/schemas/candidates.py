"""Candidate hypothesis contracts.

``score`` is an ordinal strength, not a percentage. A model that emits ``0.873`` for a
bark photograph is reporting a number it cannot justify; three ordered buckets are the
honest resolution of the underlying evidence.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from dendro_inspector.schemas.base import Contract, Identifier, ValueToken
from dendro_inspector.schemas.taxon import Resolution


class SupportStrength(StrEnum):
    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"


_STRENGTH_ORDER: dict[SupportStrength, int] = {
    SupportStrength.WEAK: 0,
    SupportStrength.MODERATE: 1,
    SupportStrength.STRONG: 2,
}


def strength_rank(strength: SupportStrength) -> int:
    return _STRENGTH_ORDER[strength]


class Candidate(Contract):
    """One taxonomic hypothesis for one subject."""

    taxon: Identifier
    resolution: Resolution
    supporting_evidence_ids: tuple[Identifier, ...] = ()
    contradicting_evidence_ids: tuple[Identifier, ...] = ()
    missing_decisive_features: tuple[ValueToken, ...] = ()
    score: SupportStrength = SupportStrength.WEAK
    rank: int = Field(ge=1, le=99)


class CandidateSet(Contract):
    """The ranked hypotheses for a single subject."""

    subject_id: Identifier
    candidates: tuple[Candidate, ...] = ()

    @model_validator(mode="after")
    def _ranks_are_unique_and_dense(self) -> CandidateSet:
        if not self.candidates:
            return self
        ranks = [candidate.rank for candidate in self.candidates]
        if len(set(ranks)) != len(ranks):
            msg = f"duplicate candidate rank in subject {self.subject_id!r}"
            raise ValueError(msg)
        if sorted(ranks) != list(range(1, len(ranks) + 1)):
            msg = (
                f"candidate ranks for subject {self.subject_id!r} must be dense and 1-based, "
                f"got {sorted(ranks)}"
            )
            raise ValueError(msg)
        taxa = [candidate.taxon for candidate in self.candidates]
        if len(set(taxa)) != len(taxa):
            msg = f"duplicate taxon in candidate set for subject {self.subject_id!r}"
            raise ValueError(msg)
        return self

    @property
    def ordered(self) -> tuple[Candidate, ...]:
        return tuple(sorted(self.candidates, key=lambda candidate: candidate.rank))

    @property
    def leader(self) -> Candidate | None:
        ordered = self.ordered
        return ordered[0] if ordered else None

    @property
    def runner_up(self) -> Candidate | None:
        ordered = self.ordered
        return ordered[1] if len(ordered) > 1 else None

    def leaders_are_close(self) -> bool:
        """Whether the top two candidates are separated by less than one strength step."""
        leader, runner_up = self.leader, self.runner_up
        if leader is None or runner_up is None:
            return False
        return strength_rank(leader.score) - strength_rank(runner_up.score) < 1


class CandidateProposal(Contract):
    """A model's candidate proposal across every subject in the frame.

    One response object rather than one call per subject, so the model can express
    "these two logs are the same taxon" without the graph inventing that link itself.
    """

    sets: tuple[CandidateSet, ...] = ()

    @model_validator(mode="after")
    def _subjects_are_unique(self) -> CandidateProposal:
        subject_ids = [candidate_set.subject_id for candidate_set in self.sets]
        if len(set(subject_ids)) != len(subject_ids):
            msg = "duplicate subject_id in candidate proposal"
            raise ValueError(msg)
        return self
