"""Routing is a total, pure function — every branch is testable without a model."""

from __future__ import annotations

import pytest

from dendro_inspector.config import GraphConfig
from dendro_inspector.graph.definition import NodeName
from dendro_inspector.graph.routing import RoutingError, next_step, route_internal_gate
from dendro_inspector.graph.state import (
    EscalationDecision,
    EvidenceQualityReport,
    GraphState,
)
from dendro_inspector.schemas.input import CaseInput
from dendro_inspector.schemas.reviews import ReviewSynthesis


@pytest.fixture
def state() -> GraphState:
    return GraphState(case=CaseInput(case_id="c1", user_text="x"))


@pytest.fixture
def graph_config() -> GraphConfig:
    return GraphConfig(retry_budget=1)


class TestLinearEdges:
    @pytest.mark.parametrize(
        ("current", "expected"),
        [
            (NodeName.INPUT_GUARD, NodeName.PLANNER),
            (NodeName.PLANNER, NodeName.EVIDENCE_EXTRACTOR),
            (NodeName.EVIDENCE_EXTRACTOR, NodeName.EVIDENCE_QUALITY),
            (NodeName.PHOTO_PLANNER, NodeName.RESPONSE_COMPOSER),
            (NodeName.CANDIDATE_GENERATOR, NodeName.ATTACHMENT_AUTHORITY_GATE),
            (NodeName.ATTACHMENT_AUTHORITY_GATE, NodeName.BOTANICAL_REVIEWER),
            (NodeName.BOTANICAL_REVIEWER, NodeName.REVIEW_SYNTHESIZER),
            (NodeName.CONFUSION_REVIEWER, NodeName.REVIEW_SYNTHESIZER),
            (NodeName.CONFIDENCE_REVIEWER, NodeName.REVIEW_SYNTHESIZER),
            (NodeName.CORRECTION_WORKER, NodeName.EVIDENCE_EXTRACTOR),
            (NodeName.ABSTAIN, NodeName.ESCALATION_GATE),
            (NodeName.ARBITER, NodeName.ARBITER_SYNTHESIZER),
            (NodeName.ARBITER_SYNTHESIZER, NodeName.FINAL_DECISION),
            (NodeName.FINAL_DECISION, NodeName.RESPONSE_COMPOSER),
            (NodeName.RESPONSE_COMPOSER, NodeName.TONE_LAYER),
            (NodeName.TONE_LAYER, NodeName.OUTPUT),
        ],
    )
    def test_unconditional_edges(self, current, expected, state, graph_config):
        assert next_step(current, state, graph_config) is expected


class TestQualityGate:
    def test_usable_evidence_goes_to_candidates(self, state, graph_config):
        state = state.evolve(
            quality=EvidenceQualityReport(sufficient=True, usable_subject_ids=("s",))
        )
        assert (
            next_step(NodeName.EVIDENCE_QUALITY, state, graph_config)
            is NodeName.CANDIDATE_GENERATOR
        )

    def test_insufficient_evidence_goes_to_photo_planner(self, state, graph_config):
        state = state.evolve(quality=EvidenceQualityReport(sufficient=False))
        assert next_step(NodeName.EVIDENCE_QUALITY, state, graph_config) is NodeName.PHOTO_PLANNER

    def test_missing_report_is_an_error_not_a_guess(self, state, graph_config):
        with pytest.raises(RoutingError, match="no report"):
            next_step(NodeName.EVIDENCE_QUALITY, state, graph_config)


class TestInternalGate:
    def test_clean_review_proceeds_to_escalation_gate(self, state, graph_config):
        state = state.evolve(synthesis=ReviewSynthesis())
        assert route_internal_gate(state, graph_config) is NodeName.ESCALATION_GATE

    def test_correctable_failure_retries_while_budget_remains(self, state, graph_config):
        state = state.evolve(synthesis=ReviewSynthesis(retry_required=True), retries=0)
        assert route_internal_gate(state, graph_config) is NodeName.CORRECTION_WORKER

    def test_exhausted_budget_degrades_to_abstention_rather_than_looping(self, state, graph_config):
        """The termination guarantee. Without this the graph could retry forever."""
        state = state.evolve(synthesis=ReviewSynthesis(retry_required=True), retries=1)
        assert route_internal_gate(state, graph_config) is NodeName.ABSTAIN

    def test_unresolvable_never_retries_even_with_budget(self, state, graph_config):
        state = state.evolve(
            synthesis=ReviewSynthesis(retry_required=True, unresolvable=True), retries=0
        )
        assert route_internal_gate(state, graph_config) is NodeName.ABSTAIN

    def test_zero_retry_budget_abstains_immediately(self, state):
        state = state.evolve(synthesis=ReviewSynthesis(retry_required=True), retries=0)
        assert route_internal_gate(state, GraphConfig(retry_budget=0)) is NodeName.ABSTAIN

    def test_missing_synthesis_is_an_error(self, state, graph_config):
        with pytest.raises(RoutingError, match="without a review synthesis"):
            route_internal_gate(state, graph_config)


class TestEscalationRouting:
    def test_escalation_required_calls_arbiter(self, state, graph_config):
        state = state.evolve(escalation=EscalationDecision(required=True))
        assert next_step(NodeName.ESCALATION_GATE, state, graph_config) is NodeName.ARBITER

    def test_no_escalation_goes_straight_to_decision(self, state, graph_config):
        state = state.evolve(escalation=EscalationDecision(required=False))
        assert next_step(NodeName.ESCALATION_GATE, state, graph_config) is NodeName.FINAL_DECISION


def test_pseudo_nodes_have_no_routing_rule(state, graph_config):
    """`input` and `output` are rendering boundaries, not steps the executor walks."""
    with pytest.raises(RoutingError, match="no routing rule"):
        next_step(NodeName.INPUT, state, graph_config)
