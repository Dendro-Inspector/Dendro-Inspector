"""Escalation policy: triggers, suppressors, and the precedence between them."""

from __future__ import annotations

import pytest

from dendro_inspector.config import EscalationPolicy
from dendro_inspector.graph.state import (
    EvidenceQualityReport,
    GraphState,
    GuardReport,
    InspectionPlan,
)
from dendro_inspector.nodes.escalation_gate import decide
from dendro_inspector.schemas.candidates import Candidate, CandidateSet, SupportStrength
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
from dendro_inspector.schemas.taxon import Resolution


def _state(**changes) -> GraphState:
    base = GraphState(
        case=CaseInput(case_id="c1", user_text="x"),
        quality=EvidenceQualityReport(sufficient=True, usable_subject_ids=("log_1",)),
        evidence=EvidencePacket(subjects=(Subject(subject_id="log_1"),)),
        synthesis=ReviewSynthesis(),
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
        decision = decide(state, POLICY)
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
        decision = decide(state, POLICY)
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
        assert "bark_only_input" in decide(state, POLICY).reasons

    def test_user_challenge_escalates(self):
        state = _state(guard=GuardReport(user_challenges_previous_result=True))
        assert decide(state, POLICY).required

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

        decision = decide(state, POLICY)

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

        decision = decide(
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

        decision = decide(state, POLICY)

        assert decision.required
        assert "escalation_provenance_unknown" in decision.reasons


class TestSuppressors:
    def test_insufficient_evidence_blocks_escalation(self):
        """A second opinion on 'I cannot tell' is still 'I cannot tell', at twice the price."""
        state = _state(quality=EvidenceQualityReport(sufficient=False))
        decision = decide(state, POLICY)
        assert not decision.required
        assert "evidence_insufficient" in decision.suppressed_by

    def test_abstaining_blocks_escalation(self):
        state = _state(abstained=True)
        decision = decide(state, POLICY)
        assert not decision.required
        assert "already_abstaining" in decision.suppressed_by

    def test_clean_broad_result_is_not_escalated(self):
        decision = decide(_state(), POLICY)
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
        decision = decide(state, EscalationPolicy(enabled=False))
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
        decision = decide(state, POLICY)
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
        decision = decide(state, POLICY)
        assert not decision.required
        assert "possible_multiple_taxa" in decision.reasons  # recorded, but not acted on

    @pytest.mark.parametrize(
        "field",
        ["on_species_resolution", "on_multiple_taxa", "on_bark_only_input", "on_user_challenge"],
    )
    def test_every_trigger_is_individually_switchable(self, field):
        assert getattr(EscalationPolicy(**{field: False}), field) is False


@pytest.mark.xfail(
    strict=True,
    reason=(
        "F2 (docs/specs/core-logic-hardening.md): the gate reads a reviewer recommendation "
        "rather than the verdict, so silent reviewers read as modest confidence while the "
        "deterministic decision for the same state is high/identified."
    ),
)
def test_strong_leader_with_silent_reviewers_escalates():
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

    decision = decide(state, POLICY)

    assert decision.required
    assert "high_confidence_proposed" in decision.reasons
