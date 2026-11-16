"""The name the evidence supports and review held back, kept beside the verdict.

A genus its card had diagnosed used to vanish from the answer whenever a review recommended
a broader level. It is now recorded as a hypothesis: beside the verdict, never inside it,
and only where code found nothing against it. Every case here is synthetic and states a
rule, not an answer.
"""

from __future__ import annotations

import pytest

from dendro_inspector.config import ReviewAuthority
from dendro_inspector.graph.executor import NodeContext
from dendro_inspector.graph.state import EvidenceQualityReport, GraphState, SubjectAbstention
from dendro_inspector.knowledge.candidate_validation import validate_candidate_set
from dendro_inspector.knowledge.evidence_hierarchy import EvidenceTier
from dendro_inspector.nodes import final_decision
from dendro_inspector.nodes.consistency import (
    HYPOTHESIS_MISMATCH,
    TEXT_OMITS_HYPOTHESIS,
    VALIDATED_IDENTITY_MISMATCH,
    explanation_conflicts,
)
from dendro_inspector.nodes.response_composer import (
    build_result,
    render_human_readable,
    select_format,
)
from dendro_inspector.nodes.review_synthesizer import adjudicate
from dendro_inspector.nodes.tone_layer import apply_tone
from dendro_inspector.observability.trace import TraceRecorder
from dendro_inspector.schemas.candidates import Candidate, CandidateSet, SupportStrength
from dendro_inspector.schemas.decisions import (
    CaseResponse,
    DecisionStatus,
    FinalDecision,
    HypothesisStatus,
    ToneMode,
    ValidatedIdentity,
    VisualHypothesis,
)
from dendro_inspector.schemas.evidence import (
    AttachmentStatus,
    EvidencePacket,
    Observation,
    ObservationSource,
    Subject,
)
from dendro_inspector.schemas.review_context import ProposedAssessment
from dendro_inspector.schemas.reviews import ReviewFinding, ReviewResult
from dendro_inspector.schemas.taxon import Confidence, Resolution

SUBJECT = "tree_a"


def _observation(observation_id: str, feature: str, value: str, **extra: object) -> Observation:
    return Observation.model_validate(
        {
            "observation_id": observation_id,
            "subject_id": SUBJECT,
            "feature": feature,
            "value": value,
            "source": ObservationSource.IMAGE,
            "image_id": "img-1",
            **extra,
        }
    )


def _cone() -> Observation:
    """The pine card's own strong-positive reading: diagnostic of the genus."""
    return _observation(
        "cone", "cones.scale_shape", "woody_umbo", attachment=AttachmentStatus.CONFIRMED_ATTACHED
    )


def _plated_bark() -> Observation:
    """A reading the pine card lists as supporting only: it fits pine and other trees."""
    return _observation("bark", "bark.texture", "scaly_plates")


def _state(case, knowledge, support: Observation, *extra: Observation) -> GraphState:
    """A pine proposed at genus on one reading, with whatever else the packet holds."""
    evidence = EvidencePacket(
        subjects=(Subject(subject_id=SUBJECT),),
        observations=(support, *extra),
    )
    candidates = validate_candidate_set(
        CandidateSet(
            subject_id=SUBJECT,
            candidates=(
                Candidate(
                    taxon="pinus",
                    resolution=Resolution.GENUS,
                    supporting_evidence_ids=(support.observation_id,),
                    score=SupportStrength.STRONG,
                    rank=1,
                ),
            ),
        ),
        evidence,
        knowledge,
    )
    return GraphState(
        case=case,
        evidence=evidence,
        candidate_sets=(candidates,),
        quality=EvidenceQualityReport(sufficient=True, usable_subject_ids=(SUBJECT,)),
    )


def _finding(**updates: object) -> ReviewFinding:
    """A reviewer's doubt: it cites the cone and contradicts nothing."""
    return ReviewFinding.model_validate(
        {
            "finding_id": "f1",
            "category": "missing_decisive_feature",
            "severity": "major",
            "subject_id": SUBJECT,
            "evidence_ids": ("cone",),
            "summary": "No needles are visible, so the genus is not defensible.",
            "required_action": "request_additional_photo",
            "impact": "resolution_change",
            **updates,
        }
    )


def _review(*findings: ReviewFinding, **fields: object) -> ReviewResult:
    return ReviewResult.model_validate(
        {
            "reviewer": "botanical",
            "status": "pass_with_findings" if findings else "pass",
            "findings": findings,
            **fields,
        }
    )


def _evidence_bound(ctx: NodeContext) -> NodeContext:
    graph = ctx.config.graph.model_copy(update={"review_authority": ReviewAuthority.EVIDENCE_BOUND})
    return NodeContext(
        config=ctx.config.model_copy(update={"graph": graph}),
        providers=ctx.providers,
        knowledge=ctx.knowledge,
        prompts=ctx.prompts,
        recorder=TraceRecorder("test-case"),
    )


def _decided(
    state: GraphState, ctx: NodeContext, *reviews: ReviewResult
) -> tuple[FinalDecision, GraphState]:
    """The verdict after these reviews, and the state it was decided in."""
    reviewed = state.evolve(
        synthesis=adjudicate(reviews, evidence=state.evidence, knowledge=ctx.knowledge)
    )
    candidates = reviewed.candidates_for(SUBJECT)
    assert candidates is not None
    decision = final_decision.decide_subject(reviewed, ctx, candidates)
    return decision, reviewed.evolve(decisions=(decision,))


def _response(decision: FinalDecision, state: GraphState, locale: str = "en") -> CaseResponse:
    result = build_result(decision, locale, state)
    text = render_human_readable(
        (result,),
        (decision,),
        state,
        locale=locale,
        response_format=select_format(decision, ToneMode.CAUTIOUS),
        placeholder_knowledge=False,
    )
    return CaseResponse(
        case_id=state.case.case_id,
        results=(result,),
        decisions=(decision,),
        human_readable=text,
        tone_mode=ToneMode.CAUTIOUS,
        locale=locale,
    )


def _hypothesis(**updates: object) -> VisualHypothesis:
    return VisualHypothesis.model_validate(
        {
            "taxon": "pinus",
            "display_name": "Pinus (pine)",
            "resolution": Resolution.GENUS,
            "held_back_by": "reviewer_recommendation",
            "diagnostic_evidence_ids": ("cone",),
            **updates,
        }
    )


class TestANameReviewHeldBackIsKept:
    """Released policy: the review still lowers the verdict, and the name stays beside it."""

    def test_a_recommended_level_leaves_the_diagnosed_genus_as_a_hypothesis(
        self, simple_case, node_context
    ):
        decision, _ = _decided(
            _state(simple_case, node_context.knowledge, _cone()),
            node_context,
            _review(_finding(), recommended_resolution="unknown"),
        )

        assert decision.selected_taxon is None
        assert decision.resolution is Resolution.UNKNOWN
        hypothesis = decision.visual_hypothesis
        assert hypothesis is not None
        assert (hypothesis.taxon, hypothesis.resolution) == ("pinus", Resolution.GENUS)
        assert hypothesis.status is HypothesisStatus.EVIDENCE_SUPPORTED
        assert hypothesis.held_back_by == "reviewer_recommendation"
        assert hypothesis.diagnostic_evidence_ids == ("cone",)

    def test_a_family_verdict_keeps_the_genus_beside_it(self, simple_case, node_context):
        decision, _ = _decided(
            _state(simple_case, node_context.knowledge, _cone()),
            node_context,
            _review(_finding(), recommended_resolution="family"),
        )

        assert (decision.selected_taxon, decision.resolution) == ("pinaceae", Resolution.FAMILY)
        assert decision.visual_hypothesis is not None
        assert decision.visual_hypothesis.taxon == "pinus"
        assert decision.visual_hypothesis.resolution is Resolution.GENUS

    def test_an_unverified_request_to_lower_the_level_is_named_as_the_cause(
        self, simple_case, node_context
    ):
        lowering = _finding(category="resolution_too_specific", required_action="lower_resolution")
        decision, _ = _decided(
            _state(simple_case, node_context.knowledge, _cone()), node_context, _review(lowering)
        )

        assert decision.resolution is not Resolution.GENUS
        assert decision.visual_hypothesis is not None
        assert decision.visual_hypothesis.held_back_by == "review_finding"

    def test_an_abstention_keeps_the_name(self, simple_case, node_context):
        state = _state(simple_case, node_context.knowledge, _cone()).evolve(
            abstained=True,
            abstention_bounds=(
                SubjectAbstention(subject_id=SUBJECT, resolution=Resolution.FAMILY),
            ),
        )
        candidates = state.candidates_for(SUBJECT)
        assert candidates is not None

        decision = final_decision.decide_subject(state, node_context, candidates)

        assert decision.resolution is Resolution.FAMILY
        assert decision.visual_hypothesis is not None
        assert decision.visual_hypothesis.taxon == "pinus"
        assert decision.visual_hypothesis.held_back_by == "abstention"

    def test_a_contradiction_read_from_untrusted_evidence_withdraws_nothing(
        self, simple_case, node_context
    ):
        # A broadleaf at the frame edge contradicts pine on the card, and the trust boundary
        # holds it as context: it cannot support a claim, and it cannot withdraw a hypothesis.
        stray = _observation(
            "stray-leaf", "leaf.type", "broadleaf_simple", attachment=AttachmentStatus.UNKNOWN
        )
        decision, _ = _decided(
            _state(simple_case, node_context.knowledge, _cone(), stray),
            node_context,
            _review(
                _finding(category="botanical_contradiction", evidence_ids=("stray-leaf",)),
                recommended_resolution="unknown",
            ),
        )

        assert decision.resolution is Resolution.UNKNOWN
        assert decision.visual_hypothesis is not None
        assert decision.visual_hypothesis.taxon == "pinus"


class TestNoHypothesisWhereTheEvidenceIsTheReason:
    """A hypothesis is what review alone held back. Anything code can check ends it."""

    def test_a_confirmed_name_leaves_nothing_to_hypothesise(self, simple_case, node_context):
        decision, _ = _decided(
            _state(simple_case, node_context.knowledge, _cone()), node_context, _review()
        )

        assert (decision.selected_taxon, decision.resolution) == ("pinus", Resolution.GENUS)
        assert decision.visual_hypothesis is None

    def test_a_leader_without_a_diagnostic_reading_is_not_singled_out(
        self, simple_case, node_context
    ):
        """Plated bark fits pine and several other trees; the first guess is not a hypothesis."""
        state = _state(simple_case, node_context.knowledge, _plated_bark())

        unreviewed, _ = _decided(state, node_context, _review())
        reviewed, _ = _decided(
            state,
            node_context,
            _review(_finding(evidence_ids=("bark",)), recommended_resolution="unknown"),
        )

        assert unreviewed.selected_taxon is None
        assert unreviewed.visual_hypothesis is None
        assert reviewed.selected_taxon is None
        assert reviewed.visual_hypothesis is None

    def test_a_cited_card_contradiction_ends_it(self, simple_case, node_context):
        # Resin blisters are on the pine card's own list of contradictions (fir-like bark).
        blisters = _observation("blisters", "bark.surface_marks", "resin_blisters")
        decision, _ = _decided(
            _state(simple_case, node_context.knowledge, _cone(), blisters),
            node_context,
            _review(
                _finding(category="botanical_contradiction", evidence_ids=("blisters",)),
                recommended_resolution="family",
            ),
        )

        assert decision.resolution is Resolution.FAMILY
        assert decision.visual_hypothesis is None

    def test_a_trusted_contradiction_no_review_cited_ends_it_too(self, simple_case, node_context):
        blisters = _observation("blisters", "bark.surface_marks", "resin_blisters")
        decision, _ = _decided(
            _state(simple_case, node_context.knowledge, _cone(), blisters),
            node_context,
            _review(_finding(), recommended_resolution="unknown"),
        )

        assert decision.resolution is Resolution.UNKNOWN
        assert decision.visual_hypothesis is None

    def test_contamination_of_its_own_support_ends_it(self, simple_case, node_context):
        decision, _ = _decided(
            _state(simple_case, node_context.knowledge, _cone()),
            node_context,
            _review(
                _finding(category="subject_contamination", evidence_ids=("cone",)),
                recommended_resolution="unknown",
            ),
        )

        assert decision.resolution is Resolution.UNKNOWN
        assert decision.visual_hypothesis is None

    def test_a_code_raised_lowering_ends_it(self, simple_case, node_context):
        raised = _finding(
            origin="deterministic",
            finding_id="auto-1",
            category="resolution_too_specific",
            required_action="lower_resolution",
            evidence_ids=(),
        )
        decision, _ = _decided(
            _state(simple_case, node_context.knowledge, _cone()), node_context, _review(raised)
        )

        assert decision.resolution is not Resolution.GENUS
        assert decision.visual_hypothesis is None

    def test_the_evidence_bound_policy_confirms_the_name_instead(self, simple_case, node_context):
        """The same test decides the other way round there, so no hypothesis is left over."""
        ctx = _evidence_bound(node_context)
        decision, _ = _decided(
            _state(simple_case, ctx.knowledge, _cone()),
            ctx,
            _review(_finding(), recommended_resolution="unknown"),
        )

        assert (decision.selected_taxon, decision.resolution) == ("pinus", Resolution.GENUS)
        assert decision.visual_hypothesis is None


class TestAHypothesisIsCappedAsAVerdictIs:
    def test_it_is_no_narrower_than_its_card_declares(self, knowledge):
        card = knowledge.taxon("pinus")
        overclaimed = Candidate(
            taxon="pinus",
            resolution=Resolution.SPECIES,
            supporting_evidence_ids=("cone",),
            score=SupportStrength.STRONG,
            rank=1,
        )

        identity = final_decision.hypothesis_identity(overclaimed, card, EvidenceTier.FRUIT_SEED)

        assert identity is not None
        assert (identity.taxon_id, identity.resolution) == ("pinus", Resolution.GENUS)

    def test_it_is_no_narrower_than_the_kind_of_evidence_can_name(self, knowledge):
        card = knowledge.taxon("pinus")
        proposed = Candidate(
            taxon="pinus",
            resolution=Resolution.GENUS,
            supporting_evidence_ids=("outline",),
            score=SupportStrength.STRONG,
            rank=1,
        )

        identity = final_decision.hypothesis_identity(proposed, card, EvidenceTier.SILHOUETTE)

        assert identity is not None
        assert (identity.taxon_id, identity.resolution) == ("pinaceae", Resolution.FAMILY)


class TestTheContractKeepsTheTwoNamesApart:
    def test_the_selected_taxon_cannot_also_be_the_hypothesis(self):
        with pytest.raises(ValueError, match="not a hypothesis beside it"):
            FinalDecision(
                subject_id=SUBJECT,
                selected_taxon="pinus",
                selected_taxon_display_name="Pinus (pine)",
                resolution=Resolution.GENUS,
                confidence=Confidence.MEDIUM,
                status=DecisionStatus.PROBABLE,
                visual_hypothesis=_hypothesis(),
            )

    def test_a_hypothesis_is_narrower_than_the_verdict(self):
        with pytest.raises(ValueError, match="narrower level than the verdict"):
            FinalDecision(
                subject_id=SUBJECT,
                selected_taxon="pinus",
                selected_taxon_display_name="Pinus (pine)",
                resolution=Resolution.GENUS,
                confidence=Confidence.MEDIUM,
                status=DecisionStatus.PROBABLE,
                visual_hypothesis=_hypothesis(
                    taxon="pinaceae",
                    display_name="Pinaceae (pine family)",
                    resolution=Resolution.FAMILY,
                ),
            )

    def test_a_hypothesis_names_a_level(self):
        with pytest.raises(ValueError, match="must name a taxonomic level"):
            _hypothesis(resolution=Resolution.UNKNOWN)

    def test_a_hypothesis_cites_the_reading_it_rests_on(self):
        with pytest.raises(ValueError, match="diagnostic_evidence_ids"):
            _hypothesis(diagnostic_evidence_ids=())

    def test_the_arbiter_is_shown_the_verdict_and_not_the_hypothesis(self):
        """A name beside the verdict must not come back as a model's reason for it."""
        assert "selected_taxon" in ProposedAssessment.model_fields
        assert "visual_hypothesis" not in ProposedAssessment.model_fields


class TestTheAnswerNeverConfirmsAHypothesis:
    def _held_back(self, case, ctx, recommended: str) -> tuple[FinalDecision, GraphState]:
        return _decided(
            _state(case, ctx.knowledge, _cone()),
            ctx,
            _review(_finding(), recommended_resolution=recommended),
        )

    def test_the_result_carries_no_confirmed_name_when_nothing_was_confirmed(
        self, simple_case, node_context
    ):
        decision, state = self._held_back(simple_case, node_context, "unknown")

        result = build_result(decision, "en", state)

        assert result.validated_identity is None
        assert result.visual_hypothesis is not None
        assert result.visual_hypothesis.taxon == "pinus"
        assert result.visual_hypothesis.status is HypothesisStatus.EVIDENCE_SUPPORTED
        assert result.visual_hypothesis.held_back_by == "reviewer_recommendation"
        assert result.visual_hypothesis.explanation

    def test_the_confirmed_name_and_the_hypothesis_are_separate_objects(
        self, simple_case, node_context
    ):
        decision, state = self._held_back(simple_case, node_context, "family")

        result = build_result(decision, "en", state)

        confirmed, hypothesis = result.validated_identity, result.visual_hypothesis
        assert confirmed is not None
        assert hypothesis is not None
        assert (confirmed.taxon, confirmed.resolution) == ("pinaceae", Resolution.FAMILY)
        assert confirmed.status is decision.status
        assert confirmed.confidence is decision.confidence
        assert (hypothesis.taxon, hypothesis.resolution) == ("pinus", Resolution.GENUS)
        assert confirmed.explanation != hypothesis.explanation

    def test_a_confirmed_name_is_reported_with_no_hypothesis(self, simple_case, node_context):
        decision, state = _decided(
            _state(simple_case, node_context.knowledge, _cone()), node_context, _review()
        )

        result = build_result(decision, "en", state)

        assert result.validated_identity is not None
        assert result.validated_identity.taxon == "pinus"
        assert result.visual_hypothesis is None

    @pytest.mark.parametrize(
        ("locale", "hedge", "nothing_confirmed"),
        [
            ("en", "Most likely Pinus (pine) (genus) — not confirmed.", "no taxon is confirmed"),
            (
                "uk",
                "Ймовірно, Pinus (pine) (рід) — не підтверджено.",
                "жодну породу не підтверджено",
            ),
        ],
    )
    def test_the_text_says_most_likely_and_not_confirmed(
        self, simple_case, node_context, locale, hedge, nothing_confirmed
    ):
        decision, state = self._held_back(simple_case, node_context, "unknown")

        response = _response(decision, state, locale)
        verdict = response.results[0].verdict

        assert hedge in response.human_readable
        assert nothing_confirmed in verdict
        assert "Pinus" not in verdict

    def test_a_verdict_with_no_hypothesis_reads_as_it_did(self, simple_case, node_context):
        decision, state = _decided(
            _state(simple_case, node_context.knowledge, _plated_bark()), node_context, _review()
        )

        response = _response(decision, state)

        assert "cannot honestly carry a taxon" in response.results[0].verdict
        assert "hypothesis" not in response.human_readable.lower()

    def test_the_tone_layer_delivers_it_unchanged(self, simple_case, node_context):
        decision, state = self._held_back(simple_case, node_context, "unknown")
        response = _response(decision, state)

        toned = apply_tone(response, node_context.prompts.personality)

        assert toned.results == response.results
        assert toned.decisions == response.decisions
        assert toned.consistency_issues == ()

    def test_a_result_that_confirms_the_hypothesis_is_reported(self, simple_case, node_context):
        decision, state = self._held_back(simple_case, node_context, "unknown")
        response = _response(decision, state)
        hypothesis = response.results[0].visual_hypothesis
        assert hypothesis is not None
        promoted = response.results[0].model_copy(
            update={
                "validated_identity": ValidatedIdentity(
                    taxon=hypothesis.taxon,
                    display_name=hypothesis.display_name,
                    resolution=hypothesis.resolution,
                    confidence=Confidence.MEDIUM,
                    confidence_band="60-79",
                    status=DecisionStatus.PROBABLE,
                    explanation="Promoted by hand.",
                )
            }
        )

        issues = explanation_conflicts(response.model_copy(update={"results": (promoted,)}))

        assert f"{VALIDATED_IDENTITY_MISMATCH}:{SUBJECT}" in issues

    def test_a_result_that_drops_the_hypothesis_is_reported(self, simple_case, node_context):
        decision, state = self._held_back(simple_case, node_context, "unknown")
        response = _response(decision, state)
        dropped = response.results[0].model_copy(update={"visual_hypothesis": None})

        issues = explanation_conflicts(response.model_copy(update={"results": (dropped,)}))

        assert f"{HYPOTHESIS_MISMATCH}:{SUBJECT}" in issues

    def test_text_that_omits_the_hypothesis_is_reported(self, simple_case, node_context):
        decision, state = self._held_back(simple_case, node_context, "unknown")
        response = _response(decision, state)
        verdict_only = response.model_copy(update={"human_readable": response.results[0].verdict})

        assert f"{TEXT_OMITS_HYPOTHESIS}:{SUBJECT}" in explanation_conflicts(verdict_only)
