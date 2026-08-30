"""Typed, bounded input contracts for reviewer model calls.

A reviewer projection is deliberately separate from ``GraphState``. The graph may know
more than a reviewer needs; crossing the model boundary requires an explicit projection so
missing data fails closed instead of silently widening back to the full state.
"""

from __future__ import annotations

from dendro_inspector.schemas.base import Contract, Identifier
from dendro_inspector.schemas.candidates import CandidateSet
from dendro_inspector.schemas.decisions import DecisionStatus
from dendro_inspector.schemas.evidence import EvidencePacket
from dendro_inspector.schemas.input import CaseInput
from dendro_inspector.schemas.reviews import Reviewer
from dendro_inspector.schemas.taxon import Confidence, Resolution


class ProposedAssessment(Contract):
    """Deterministic pre-arbitration result visible to the independent arbiter."""

    subject_id: Identifier
    selected_taxon: Identifier | None = None
    resolution: Resolution
    confidence: Confidence
    confidence_band: str
    status: DecisionStatus


class ReviewProjection(Contract):
    """The complete model-visible input for one reviewer node.

    No caller may recover omitted state from this object. Knowledge-card inclusion is
    explicit because different reviewers need different project data even when they inspect
    the same candidate set.
    """

    reviewer: Reviewer
    case: CaseInput
    evidence: EvidencePacket
    candidate_sets: tuple[CandidateSet, ...]
    taxon_ids: tuple[Identifier, ...] = ()
    include_comparison_cards: bool = True
    include_regional_pack: bool = True
    proposed_assessments: tuple[ProposedAssessment, ...] = ()

    @property
    def evidence_ids(self) -> tuple[str, ...]:
        observation_ids = tuple(
            observation.observation_id for observation in self.evidence.observations
        )
        inference_ids = tuple(inference.inference_id for inference in self.evidence.inferences)
        return observation_ids + inference_ids

    @property
    def image_ids(self) -> tuple[str, ...]:
        """Only the photographs that actually reach the provider.

        `image_inputs` skips unreadable files rather than faking them, so reporting every
        declared id here would let the trace claim a reviewer saw a photograph that was
        never transmitted - the precise failure this projection exists to make impossible.
        """
        return tuple(image.image_id for image in self.case.images if image.exists)

    @property
    def candidate_subject_ids(self) -> tuple[str, ...]:
        return tuple(candidate_set.subject_id for candidate_set in self.candidate_sets)
