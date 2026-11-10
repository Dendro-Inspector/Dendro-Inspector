"""Candidate hypothesis contracts.

``score`` is an ordinal strength, not a percentage. A model that emits ``0.873`` for a
bark photograph is reporting a number it cannot justify; three ordered buckets are the
honest resolution of the underlying evidence.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from dendro_inspector.schemas.base import Contract, EvidenceRef, Identifier, ValueToken
from dendro_inspector.schemas.reference import (
    LOCAL_STATUSES,
    ReferenceCitation,
    ReferenceStatus,
)
from dendro_inspector.schemas.taxon import Confidence, Resolution, resolution_rank


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


#: Candidate support strength maps to a confidence ceiling, never to certainty. One
#: definition, read by the final decision's seed step and by the open-world ceiling.
STRENGTH_CONFIDENCE_CEILING: dict[SupportStrength, Confidence] = {
    SupportStrength.STRONG: Confidence.HIGH,
    SupportStrength.MODERATE: Confidence.MEDIUM,
    SupportStrength.WEAK: Confidence.LOW,
}


class Candidate(Contract):
    """One taxonomic hypothesis for one subject."""

    taxon: Identifier
    resolution: Resolution
    supporting_evidence_ids: tuple[EvidenceRef, ...] = ()
    contradicting_evidence_ids: tuple[EvidenceRef, ...] = ()
    missing_decisive_features: tuple[ValueToken, ...] = Field(
        default=(),
        description=(
            "Features that would settle the candidate, as feature paths or card requirement "
            "tokens, e.g. `leaf.underside`, `bark.peeling`, `acorn.presence`. Name the "
            "feature only — the photograph to take goes elsewhere, never `bark.peeling macro`."
        ),
    )
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


class OpenWorldCandidate(Contract):
    """A model hypothesis named by an id no taxon card carries.

    Two kinds, told apart by :attr:`covered_by`:

    * **uncovered** — a taxon this knowledge base has no card for at all. Missing knowledge
      is not missing visual evidence: a frame can show real diagnostic features of a taxon
      nobody has written a card for, and silently dropping that hypothesis made "the
      reference data stops here" read as "the photograph showed nothing";
    * **covered** — a name some card declares only as a broader identity or an alias
      (``juglans`` on the walnut card, ``walnut`` itself). Not missing taxonomy, and not
      admitted either, because admission matches card ids and is not widened to resolve
      names. Retained so it cannot vanish between the two.

    It is never admitted. No card means no feature rule can confirm the support, no
    high-confidence requirement can be checked, and no identity can be selected — so an
    open-world candidate never becomes a verdict's ``selected_taxon``. What it carries is
    bounded by the evidence alone: the resolution the support's tier allows, a score below
    ``strong``, and an image-identification confidence ceiling below ``high``. Reference
    status is a separate dimension and never moves that ceiling.
    """

    subject_id: Identifier
    taxon: Identifier
    proposed_rank: int = Field(ge=1, le=99, description="The model's rank, before admission.")
    proposed_resolution: Resolution
    resolution: Resolution = Field(
        description="The proposed resolution, capped by the evidence tier of the support.",
    )
    proposed_score: SupportStrength
    score: SupportStrength = Field(description="The proposed score, capped below strong.")
    supporting_evidence_ids: tuple[Identifier, ...] = Field(
        min_length=1,
        description="Proposed support that survived as trusted same-subject evidence.",
    )
    evidence_tier: int = Field(ge=1, le=7, description="Strongest tier among the support.")
    image_confidence_ceiling: Confidence
    covered_by: tuple[Identifier, ...] = Field(
        default=(),
        description="Cards declaring this name as a broader identity or alias. Empty: uncovered.",
    )
    reference_status: ReferenceStatus = ReferenceStatus.PROVISIONAL
    references: tuple[ReferenceCitation, ...] = ()

    @property
    def uncovered(self) -> bool:
        """Whether no card declares this name in any form — genuinely missing taxonomy."""
        return not self.covered_by

    @model_validator(mode="after")
    def _bounded_by_evidence_not_by_reference(self) -> OpenWorldCandidate:
        if self.resolution is Resolution.UNKNOWN:
            msg = "an open-world candidate must name a taxonomic level"
            raise ValueError(msg)
        if resolution_rank(self.resolution) > resolution_rank(self.proposed_resolution):
            msg = "an open-world candidate's resolution cannot exceed what was proposed"
            raise ValueError(msg)
        if self.score is SupportStrength.STRONG:
            msg = "strong support requires a card's requirements, which this taxon has none of"
            raise ValueError(msg)
        if self.image_confidence_ceiling is Confidence.HIGH:
            msg = "high image confidence requires a card's requirements, which this taxon lacks"
            raise ValueError(msg)
        if self.uncovered and self.reference_status in LOCAL_STATUSES:
            msg = f"an uncovered taxon has no local knowledge: {self.reference_status.value!r}"
            raise ValueError(msg)
        if not self.uncovered and (
            self.references
            or self.reference_status not in {*LOCAL_STATUSES, ReferenceStatus.DISPUTED}
        ):
            msg = "a covered name is described by local cards, not by external references"
            raise ValueError(msg)
        if self.reference_status is ReferenceStatus.EXTERNALLY_VERIFIED and not any(
            citation.outcome is ReferenceStatus.EXTERNALLY_VERIFIED for citation in self.references
        ):
            msg = "externally_verified requires a citation that verified it"
            raise ValueError(msg)
        return self


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
