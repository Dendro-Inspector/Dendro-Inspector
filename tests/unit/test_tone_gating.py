"""When the duck is allowed to bite — sections 4, 5 and 12 of the domain prompt.

Hard mode is a conjunction, not a mood. Each test below removes exactly one clause from an
otherwise-qualifying case and asserts the mode falls back, because "жорсткість без доказів —
це не хард-режим, а самовпевнена маячня".
"""

from __future__ import annotations

from typing import Any

import pytest

from evil_duck_dendro.graph.state import GraphState, GuardReport
from evil_duck_dendro.knowledge.evidence_hierarchy import EvidenceTier
from evil_duck_dendro.nodes.response_composer import decide_tone, select_format
from evil_duck_dendro.schemas.candidates import Candidate, CandidateSet, SupportStrength
from evil_duck_dendro.schemas.decisions import (
    DecisionStatus,
    FinalDecision,
    ResponseFormat,
    ToneMode,
    UserClaimVerdict,
)
from evil_duck_dendro.schemas.input import CaseInput
from evil_duck_dendro.schemas.reviews import (
    FindingCategory,
    RequiredAction,
    ReviewFinding,
    ReviewSynthesis,
    Severity,
)
from evil_duck_dendro.schemas.taxon import Confidence, Resolution


def _decision(**changes) -> FinalDecision:
    base: dict[str, Any] = {
        "subject_id": "log_1",
        "selected_taxon": "betula",
        "resolution": Resolution.GENUS,
        "confidence": Confidence.HIGH,
        "status": DecisionStatus.IDENTIFIED,
        "user_claim_verdict": UserClaimVerdict.REJECTED,
        "evidence_tier": int(EvidenceTier.FOLIAGE),
    }
    return FinalDecision(**(base | changes))


def _qualifying_state(**changes) -> GraphState:
    """A case that satisfies every clause of section 4 — the baseline for hard mode."""
    case_changes = {
        key: changes.pop(key)
        for key in list(changes)
        if key in {"user_claim", "user_has_field_context"}
    }
    base = GraphState(
        case=CaseInput(
            case_id="c1",
            user_text="Це сосна",
            user_claim="сосна",
            **case_changes,
        ),
        decisions=(_decision(),),
        synthesis=ReviewSynthesis(),
        candidate_sets=(
            CandidateSet(
                subject_id="log_1",
                candidates=(
                    Candidate(
                        taxon="betula",
                        resolution=Resolution.GENUS,
                        rank=1,
                        score=SupportStrength.STRONG,
                    ),
                    Candidate(
                        taxon="populus_alba",
                        resolution=Resolution.GENUS,
                        rank=2,
                        score=SupportStrength.WEAK,
                    ),
                ),
            ),
        ),
    )
    return base.evolve(**changes)


def _finding(category: FindingCategory) -> ReviewSynthesis:
    return ReviewSynthesis(
        accepted_findings=(
            ReviewFinding(
                finding_id="f1",
                category=category,
                severity=Severity.MAJOR,
                summary="Something restrains the verdict.",
                required_action=RequiredAction.LOWER_CONFIDENCE,
            ),
        )
    )


class TestHardModeRequiresEveryClause:
    def test_the_baseline_qualifies(self):
        mode, joke = decide_tone(_qualifying_state())
        assert mode is ToneMode.HARD
        assert joke

    def test_no_user_version_means_nothing_to_be_hard_about(self):
        state = _qualifying_state()
        state = state.evolve(case=state.case.model_copy(update={"user_claim": None}))
        assert decide_tone(state)[0] is not ToneMode.HARD

    def test_a_version_that_was_not_rejected_disqualifies(self):
        state = _qualifying_state(
            decisions=(_decision(user_claim_verdict=UserClaimVerdict.POSSIBLE),)
        )
        assert decide_tone(state)[0] is not ToneMode.HARD

    def test_confidence_below_high_disqualifies(self):
        state = _qualifying_state(decisions=(_decision(confidence=Confidence.MEDIUM),))
        assert decide_tone(state)[0] is not ToneMode.HARD

    def test_evidence_below_clear_foliage_disqualifies(self):
        """Section 4: no aggression when only bark is visible."""
        state = _qualifying_state(decisions=(_decision(evidence_tier=int(EvidenceTier.BARK)),))
        assert decide_tone(state)[0] is not ToneMode.HARD

    def test_close_leading_candidates_disqualify(self):
        state = _qualifying_state(
            candidate_sets=(
                CandidateSet(
                    subject_id="log_1",
                    candidates=(
                        Candidate(
                            taxon="betula",
                            resolution=Resolution.GENUS,
                            rank=1,
                            score=SupportStrength.MODERATE,
                        ),
                        Candidate(
                            taxon="populus_alba",
                            resolution=Resolution.GENUS,
                            rank=2,
                            score=SupportStrength.MODERATE,
                        ),
                    ),
                ),
            )
        )
        assert decide_tone(state)[0] is not ToneMode.HARD

    def test_field_context_from_the_user_disqualifies(self):
        state = _qualifying_state(user_has_field_context=True)
        assert decide_tone(state)[0] is not ToneMode.HARD

    @pytest.mark.parametrize(
        "category",
        [
            FindingCategory.COLOUR_OVERWEIGHTING,
            FindingCategory.UNSUPPORTED_CLAIM,
            FindingCategory.OVERLOOKED_ALTERNATIVE,
            FindingCategory.MISSING_DECISIVE_FEATURE,
        ],
    )
    def test_any_restraint_finding_disqualifies(self, category):
        state = _qualifying_state(synthesis=_finding(category))
        assert decide_tone(state)[0] is not ToneMode.HARD


class TestCorrection:
    def test_being_challenged_outranks_everything(self):
        """Section 12: after a correction there is no sarcasm and no joke."""
        state = _qualifying_state(guard=GuardReport(user_challenges_previous_result=True))
        mode, joke = decide_tone(state)
        assert mode is ToneMode.CORRECTIVE
        assert not joke


class TestCaution:
    def test_low_confidence_is_delivered_cautiously(self):
        state = _qualifying_state(
            decisions=(
                _decision(
                    confidence=Confidence.LOW,
                    user_claim_verdict=UserClaimVerdict.POSSIBLE,
                ),
            )
        )
        assert decide_tone(state)[0] is ToneMode.CAUTIOUS

    def test_bark_only_is_delivered_cautiously(self):
        state = _qualifying_state(
            decisions=(
                _decision(
                    evidence_tier=int(EvidenceTier.BARK),
                    confidence=Confidence.MEDIUM,
                    user_claim_verdict=UserClaimVerdict.POSSIBLE,
                ),
            )
        )
        assert decide_tone(state)[0] is ToneMode.CAUTIOUS

    def test_a_case_with_no_decisions_is_cautious(self):
        assert decide_tone(_qualifying_state(decisions=()))[0] is ToneMode.CAUTIOUS


class TestFormatSelection:
    @pytest.mark.parametrize(
        ("verdict", "tone", "expected"),
        [
            (UserClaimVerdict.NOT_PROVIDED, ToneMode.MEASURED, ResponseFormat.NO_VERSION),
            (UserClaimVerdict.ACCEPTED, ToneMode.MEASURED, ResponseFormat.USER_CORRECT),
            (UserClaimVerdict.REJECTED, ToneMode.HARD, ResponseFormat.USER_WRONG),
            (UserClaimVerdict.REJECTED, ToneMode.CAUTIOUS, ResponseFormat.WITH_VERSION),
            (UserClaimVerdict.POSSIBLE, ToneMode.MEASURED, ResponseFormat.WITH_VERSION),
            (UserClaimVerdict.DOUBTFUL, ToneMode.MEASURED, ResponseFormat.WITH_VERSION),
        ],
    )
    def test_the_prompts_five_shapes_are_selected(self, verdict, tone, expected):
        assert select_format(_decision(user_claim_verdict=verdict), tone) is expected

    def test_insufficient_evidence_always_uses_the_weak_photo_shape(self):
        decision = _decision(status=DecisionStatus.INSUFFICIENT_EVIDENCE)
        assert select_format(decision, ToneMode.HARD) is ResponseFormat.WEAK_PHOTO
