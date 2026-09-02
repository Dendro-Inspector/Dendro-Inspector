"""Deterministic context projection for reviewer model nodes.

Projection policy belongs to graph orchestration, not to prompts and not to providers.
This module is intentionally model-free: the same graph state always produces the same
reviewer view.

Case and evidence selection are deliberately pass-through in this first boundary. The
candidate generator is one of the things under review, so its output must not decide which
subjects, observations, or photographs a reviewer is allowed to inspect. Narrowing belongs
in a separate policy change with an evaluation delta attached.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dendro_inspector.graph.definition import NodeName
from dendro_inspector.graph.state import GraphState
from dendro_inspector.schemas.evidence import EvidencePacket
from dendro_inspector.schemas.review_context import ProposedAssessment, ReviewProjection
from dendro_inspector.schemas.reviews import Reviewer

if TYPE_CHECKING:
    from dendro_inspector.graph.executor import NodeContext


class ReviewProjectionError(RuntimeError):
    """Raised when orchestration cannot construct the required bounded reviewer input."""


_REVIEWER_BY_NODE: dict[NodeName, Reviewer] = {
    NodeName.BOTANICAL_REVIEWER: Reviewer.BOTANICAL,
    NodeName.CONFUSION_REVIEWER: Reviewer.CONFUSION,
    NodeName.CONFIDENCE_REVIEWER: Reviewer.CONFIDENCE,
    NodeName.ARBITER: Reviewer.ARBITER,
}

REVIEWER_NODES: frozenset[NodeName] = frozenset(_REVIEWER_BY_NODE)


def _proposed_taxa(state: GraphState) -> tuple[str, ...]:
    seen: list[str] = []
    for candidate_set in state.candidate_sets:
        for candidate in candidate_set.ordered:
            if candidate.taxon not in seen:
                seen.append(candidate.taxon)
    return tuple(seen)


def _project_evidence(state: GraphState, reviewer: Reviewer) -> EvidencePacket:
    evidence = state.evidence
    if evidence is None:
        msg = f"{reviewer.value} review requires evidence"
        raise ReviewProjectionError(msg)
    return evidence


def _proposed_assessments(state: GraphState) -> tuple[ProposedAssessment, ...]:
    decisions = state.provisional_decisions
    if not decisions:
        msg = "arbiter projection requires provisional decisions from the escalation gate"
        raise ReviewProjectionError(msg)
    return tuple(
        ProposedAssessment(
            subject_id=decision.subject_id,
            selected_taxon=decision.selected_taxon,
            resolution=decision.resolution,
            confidence=decision.confidence,
            confidence_band=decision.confidence_band,
            status=decision.status,
        )
        for decision in decisions
    )


def build_review_projection(
    node: NodeName,
    state: GraphState,
    ctx: NodeContext,
) -> ReviewProjection:
    """Build the only model-visible input allowed for a reviewer node."""
    reviewer = _REVIEWER_BY_NODE.get(node)
    if reviewer is None:
        msg = f"node {node.value!r} has no reviewer projection policy"
        raise ReviewProjectionError(msg)

    evidence = _project_evidence(state, reviewer)
    return ReviewProjection(
        reviewer=reviewer,
        case=state.case,
        evidence=evidence,
        candidate_sets=state.candidate_sets,
        taxon_ids=_proposed_taxa(state),
        include_comparison_cards=True,
        include_regional_pack=True,
        proposed_assessments=(_proposed_assessments(state) if reviewer is Reviewer.ARBITER else ()),
    )
