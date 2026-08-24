"""Pure routing.

Routing is a total function of ``(current node, state, config)``. It performs no I/O and
holds no state, so every branch in the graph is unit-testable without a provider, and the
termination argument is a property of this module alone:

* the only edge that goes backwards is ``correction_worker -> evidence_extractor``;
* that edge is taken only while ``state.retries < config.retry_budget``;
* ``correction_worker`` increments ``retries`` on every pass.

Therefore the loop can be taken at most ``retry_budget`` times and the graph terminates.
"""

from __future__ import annotations

from dendro_inspector.config import GraphConfig
from dendro_inspector.graph.definition import NodeName
from dendro_inspector.graph.state import GraphState


class RoutingError(RuntimeError):
    """Raised when routing is asked for a next step it cannot determine."""


def route_internal_gate(state: GraphState, config: GraphConfig) -> NodeName:
    """Resolve the ``internal_gate`` decision diamond.

    Precedence matters: an unresolvable finding must not be retried, and a retry request
    that has run out of budget degrades to abstention rather than looping.
    """
    synthesis = state.synthesis
    if synthesis is None:
        msg = "internal gate reached without a review synthesis"
        raise RoutingError(msg)

    if synthesis.unresolvable:
        return NodeName.ABSTAIN
    if synthesis.retry_required:
        if state.retries < config.retry_budget:
            return NodeName.CORRECTION_WORKER
        return NodeName.ABSTAIN
    return NodeName.ESCALATION_GATE


def next_step(current: NodeName, state: GraphState, config: GraphConfig) -> NodeName:
    """Return the next step after ``current`` has run."""
    match current:
        case NodeName.INPUT_GUARD:
            return NodeName.PLANNER

        case NodeName.PLANNER:
            return NodeName.EVIDENCE_EXTRACTOR

        case NodeName.EVIDENCE_EXTRACTOR:
            return NodeName.EVIDENCE_QUALITY

        case NodeName.EVIDENCE_QUALITY:
            if state.quality is None:
                msg = "evidence quality gate produced no report"
                raise RoutingError(msg)
            return (
                NodeName.CANDIDATE_GENERATOR if state.quality.sufficient else NodeName.PHOTO_PLANNER
            )

        case NodeName.PHOTO_PLANNER:
            return NodeName.RESPONSE_COMPOSER

        case NodeName.CANDIDATE_GENERATOR:
            return NodeName.ATTACHMENT_AUTHORITY_GATE

        case NodeName.ATTACHMENT_AUTHORITY_GATE:
            return NodeName.BOTANICAL_REVIEWER

        case (
            NodeName.BOTANICAL_REVIEWER | NodeName.CONFUSION_REVIEWER | NodeName.CONFIDENCE_REVIEWER
        ):
            return NodeName.REVIEW_SYNTHESIZER

        case NodeName.REVIEW_SYNTHESIZER:
            return route_internal_gate(state, config)

        case NodeName.CORRECTION_WORKER:
            return NodeName.EVIDENCE_EXTRACTOR

        case NodeName.ABSTAIN:
            return NodeName.FINAL_DECISION

        case NodeName.ESCALATION_GATE:
            if state.escalation is None:
                msg = "escalation gate produced no decision"
                raise RoutingError(msg)
            return NodeName.ARBITER if state.escalation.required else NodeName.FINAL_DECISION

        case NodeName.ARBITER:
            return NodeName.ARBITER_SYNTHESIZER

        case NodeName.ARBITER_SYNTHESIZER:
            return NodeName.FINAL_DECISION

        case NodeName.FINAL_DECISION:
            return NodeName.RESPONSE_COMPOSER

        case NodeName.RESPONSE_COMPOSER:
            return NodeName.TONE_LAYER

        case NodeName.TONE_LAYER:
            return NodeName.OUTPUT

        case _:
            msg = f"no routing rule for node {current.value!r}"
            raise RoutingError(msg)
