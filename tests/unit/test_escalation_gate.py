"""Escalation policy: triggers, suppressors, and the precedence between them."""

from __future__ import annotations

import asyncio

import pytest

from dendro_inspector.config import EscalationPolicy
from dendro_inspector.graph.state import (
    EvidenceQualityReport,
    GraphState,
    GuardReport,
    InspectionPlan,
)
from dendro_inspector.nodes.escalation_gate import decide
from dendro_inspector.nodes.escalation_gate import run as run_escalation_gate
from dendro_inspector.nodes.final_decision import decide_subject
from dendro_inspector.schemas.candidates import Candidate, CandidateSet, SupportStrength
from dendro_inspector.schemas.decisions import FinalDecision
from dendro_inspector.schemas.evidence import (
    AttachmentStatus,
    EvidencePacket,
    Observation,
    ObservationSource,
    Subject,
)
from dendro_inspector.schemas.input import CaseInput
from dendro_inspector.schemas.reviews import (
    FindingCategory,
    RequiredAction,
    ReviewFinding,
    ReviewSynthesis,
    Severity,
)
from dendro_inspector.schemas.taxon import Confidence, Resolution


def _state(**changes) -> GraphState:
    base = GraphState(
        case=CaseInput(case_id="c1", user_text="x"),
        quality=EvidenceQualityReport(sufficient=True, usable_subject_ids=("log_1",)),
        evidence=EvidencePacket(subjects=(Subject(subject_id="log_1"),)),
        synthesis=ReviewSynthesis(),
        provisional_decisions=(FinalDecision(subject_id="log_1"),),
        candidate_sets=(
            CandidateSet(
                subject_id="log_1",
                candidates=(
                    Candidate(
                        taxon="pinus",
                        resolution=Resolution.GENUS,
                        rank=1,
                        score=SupportStrength.STRONG,
                    ),
                ),
            ),
        ),
    )
    return base.evolve(**changes)


POLICY = EscalationPolicy()


def _decide(state: GraphState, policy: EscalationPolicy = POLICY):
    return decide(state, policy, state.provisional_decisions)


class TestTriggers:
    def test_species_claim_escalates(self):
        state = _state(
            candidate_sets=(
                CandidateSet(
                    subject_id="log_1",
                    candidates=(Candidate(taxon="pinus", resolution=Resolution.SPECIES, rank=1),),
                ),
            )
        )
        decision = _decide(state)
        assert decision.required
        assert "species_level_proposed" in decision.reasons

    def test_close_leading_candidates_escalate(self):
        state = _state(
            candidate_sets=(
                CandidateSet(
                    subject_id="log_1",
                    candidates=(
                        Candidate(
                            taxon="pinus",
                            resolution=Resolution.GENUS,
                            rank=1,
                            score=SupportStrength.MODERATE,
                        ),
                        Candidate(
                            taxon="picea",
                            resolution=Resolution.GENUS,
                            rank=2,
                            score=SupportStrength.MODERATE,
                        ),
                    ),
                ),
            ),
            synthesis=ReviewSynthesis(
                accepted_findings=(
                    ReviewFinding(
                        finding_id="f1",
                        category=FindingCategory.MISSING_DECISIVE_FEATURE,
                        severity=Severity.MINOR,
                        summary="Needs a fascicle macro.",
                        required_action=RequiredAction.REQUEST_ADDITIONAL_PHOTO,
                    ),
                )
            ),
        )
        decision = _decide(state)
        assert decision.required
        assert "leading_candidates_close" in decision.reasons

    def test_bark_only_input_escalates(self):
        state = _state(
            plan=InspectionPlan(bark_only_input=True),
            synthesis=ReviewSynthesis(
                accepted_findings=(
                    ReviewFinding(
                        finding_id="f1",
                        category=FindingCategory.COLOUR_OVERWEIGHTING,
                        severity=Severity.MAJOR,
                        summary="Colour dependence.",
                        required_action=RequiredAction.LOWER_CONFIDENCE,
                    ),
                )
            ),
        )
        assert "bark_only_input" in _decide(state).reasons

    def test_user_challenge_escalates(self):
        state = _state(guard=GuardReport(user_challenges_previous_result=True))
        assert _decide(state).required

    def test_reviewer_disagreement_has_its_own_reason(self):
        state = _state(
            synthesis=ReviewSynthesis(
                reviewer_disagreement=True,
                escalation_recommended=True,
                accepted_findings=(
                    ReviewFinding(
                        finding_id="f1",
                        category=FindingCategory.MISSING_DECISIVE_FEATURE,
                        severity=Severity.MINOR,
                        summary="Needs another view.",
                        required_action=RequiredAction.REQUEST_ADDITIONAL_PHOTO,
                    ),
                ),
            )
        )

        decision = _decide(state)

        assert decision.required
        assert "reviewer_disagreement" in decision.reasons
        assert "critical_finding" not in decision.reasons

    def test_critical_finding_has_its_own_reason(self):
        state = _state(
            synthesis=ReviewSynthesis(
                escalation_recommended=True,
                accepted_findings=(
                    ReviewFinding(
                        finding_id="f1",
                        category=FindingCategory.UNSUPPORTED_CLAIM,
                        severity=Severity.CRITICAL,
                        summary="The claim is unsupported.",
                        required_action=RequiredAction.ABSTAIN,
                    ),
                ),
            )
        )

        decision = _decide(
            state,
            EscalationPolicy(on_unresolved_contradiction=False),
        )

        assert decision.required
        assert "critical_finding" in decision.reasons
        assert "reviewer_disagreement" not in decision.reasons

    def test_legacy_combined_flag_preserves_behavior_but_names_unknown_provenance(self):
        state = _state(
            synthesis=ReviewSynthesis(
                escalation_recommended=True,
                accepted_findings=(
                    ReviewFinding(
                        finding_id="f1",
                        category=FindingCategory.MISSING_DECISIVE_FEATURE,
                        severity=Severity.MINOR,
                        summary="Needs another view.",
                        required_action=RequiredAction.REQUEST_ADDITIONAL_PHOTO,
                    ),
                ),
            )
        )

        decision = _decide(state)

        assert decision.required
        assert "escalation_provenance_unknown" in decision.reasons


class TestSuppressors:
    def test_insufficient_evidence_blocks_escalation(self):
        """A second opinion on 'I cannot tell' is still 'I cannot tell', at twice the price."""
        state = _state(quality=EvidenceQualityReport(sufficient=False))
        decision = _decide(state)
        assert not decision.required
        assert "evidence_insufficient" in decision.suppressed_by

    def test_abstaining_blocks_escalation(self):
        state = _state(abstained=True)
        decision = _decide(state)
        assert not decision.required
        assert "already_abstaining" in decision.suppressed_by

    def test_clean_broad_result_is_not_escalated(self):
        decision = _decide(_state())
        assert not decision.required
        assert "broad_and_low_risk" in decision.suppressed_by

    def test_disabled_policy_never_escalates(self):
        state = _state(
            candidate_sets=(
                CandidateSet(
                    subject_id="log_1",
                    candidates=(Candidate(taxon="pinus", resolution=Resolution.SPECIES, rank=1),),
                ),
            )
        )
        decision = _decide(state, EscalationPolicy(enabled=False))
        assert not decision.required
        assert decision.suppressed_by == ("policy_disabled",)


class TestPrecedence:
    def test_cost_suppressor_cannot_override_a_hard_trigger(self):
        """The regression this guards: a cheap-looking clean result that hides mixed taxa."""
        state = _state(
            evidence=EvidencePacket(
                subjects=(Subject(subject_id="log_1"),),
                possible_multiple_taxa=True,
            )
        )
        decision = _decide(state)
        assert decision.required
        assert "possible_multiple_taxa" in decision.reasons
        assert decision.suppressed_by == ()

    def test_blocking_suppressor_does_override_a_hard_trigger(self):
        state = _state(
            quality=EvidenceQualityReport(sufficient=False),
            evidence=EvidencePacket(
                subjects=(Subject(subject_id="log_1"),),
                possible_multiple_taxa=True,
            ),
        )
        decision = _decide(state)
        assert not decision.required
        assert "possible_multiple_taxa" in decision.reasons  # recorded, but not acted on

    @pytest.mark.parametrize(
        "field",
        ["on_species_resolution", "on_multiple_taxa", "on_bark_only_input", "on_user_challenge"],
    )
    def test_every_trigger_is_individually_switchable(self, field):
        assert getattr(EscalationPolicy(**{field: False}), field) is False


def test_strong_leader_with_silent_reviewers_escalates(node_context):
    """`high_confidence_proposed` must describe the claim, not a reviewer's opinion of it.

    Attached, clearly visible fascicles carry foliage authority, so this state's
    deterministic verdict is `high` / `identified`. Three reviewers that pass without
    recommending anything leave `confidence_delta` unset, which the cost suppressor
    currently reads as modest confidence.
    """
    observation = Observation(
        observation_id="obs-1",
        feature="needles.fascicles",
        value="two",
        subject_id="log_1",
        source=ObservationSource.IMAGE,
        image_id="img-1",
        attachment=AttachmentStatus.CONFIRMED_ATTACHED,
    )
    state = _state(
        evidence=EvidencePacket(
            subjects=(Subject(subject_id="log_1"),),
            observations=(observation,),
        ),
        candidate_sets=(
            CandidateSet(
                subject_id="log_1",
                candidates=(
                    Candidate(
                        taxon="pinus",
                        resolution=Resolution.GENUS,
                        rank=1,
                        score=SupportStrength.STRONG,
                        supporting_evidence_ids=("obs-1",),
                    ),
                ),
            ),
        ),
    )

    provisional = tuple(
        decide_subject(state, node_context, candidate_set) for candidate_set in state.candidate_sets
    )
    state = state.evolve(provisional_decisions=provisional)

    decision = _decide(state)

    assert decision.required
    assert "high_confidence_proposed" in decision.reasons


def test_medium_provisional_decision_remains_suppressed(node_context):
    observation = Observation(
        observation_id="obs-1",
        feature="needles.fascicles",
        value="two",
        subject_id="log_1",
        source=ObservationSource.IMAGE,
        image_id="img-1",
        attachment=AttachmentStatus.CONFIRMED_ATTACHED,
    )
    state = _state(
        evidence=EvidencePacket(
            subjects=(Subject(subject_id="log_1"),),
            observations=(observation,),
        ),
        candidate_sets=(
            CandidateSet(
                subject_id="log_1",
                candidates=(
                    Candidate(
                        taxon="pinus",
                        resolution=Resolution.GENUS,
                        rank=1,
                        score=SupportStrength.MODERATE,
                        supporting_evidence_ids=("obs-1",),
                    ),
                ),
            ),
        ),
    )
    provisional = tuple(
        decide_subject(state, node_context, candidate_set) for candidate_set in state.candidate_sets
    )
    state = state.evolve(provisional_decisions=provisional)

    decision = _decide(state)

    assert not decision.required
    assert "high_confidence_proposed" not in decision.reasons
    assert "broad_and_low_risk" in decision.suppressed_by


def test_run_stores_the_verdict_it_used_for_escalation(node_context):
    observation = Observation(
        observation_id="obs-1",
        feature="needles.fascicles",
        value="two",
        subject_id="log_1",
        source=ObservationSource.IMAGE,
        image_id="img-1",
        attachment=AttachmentStatus.CONFIRMED_ATTACHED,
    )
    state = _state(
        evidence=EvidencePacket(
            subjects=(Subject(subject_id="log_1"),),
            observations=(observation,),
        ),
        provisional_decisions=(),
        candidate_sets=(
            CandidateSet(
                subject_id="log_1",
                candidates=(
                    Candidate(
                        taxon="pinus",
                        resolution=Resolution.GENUS,
                        rank=1,
                        score=SupportStrength.STRONG,
                        supporting_evidence_ids=("obs-1",),
                    ),
                ),
            ),
        ),
    )

    updated = asyncio.run(run_escalation_gate(state, node_context))

    assert updated.escalation is not None
    assert updated.escalation.required
    assert len(updated.provisional_decisions) == 1
    assert updated.provisional_decisions[0].confidence is Confidence.HIGH
