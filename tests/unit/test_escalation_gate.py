"""Escalation policy: triggers, suppressors, and the precedence between them."""

from __future__ import annotations

import pytest

from evil_duck_dendro.config import EscalationPolicy
from evil_duck_dendro.graph.state import (
    EvidenceQualityReport,
    GraphState,
    GuardReport,
    InspectionPlan,
)
from evil_duck_dendro.nodes.escalation_gate import decide
from evil_duck_dendro.schemas.candidates import Candidate, CandidateSet, SupportStrength
from evil_duck_dendro.schemas.evidence import EvidencePacket, Subject
from evil_duck_dendro.schemas.input import CaseInput
from evil_duck_dendro.schemas.reviews import (
    FindingCategory,
    RequiredAction,
    ReviewFinding,
    ReviewSynthesis,
    Severity,
)
from evil_duck_dendro.schemas.taxon import Resolution


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
