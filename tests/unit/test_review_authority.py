"""What a model review may do to the taxonomic level, under each review-authority policy.

The released policy lets the broadest recommended level cap the claim once any finding of
that review is accepted. The experimental ``evidence_bound`` policy lets a review lower the
level only through a finding code can check against the evidence, and records the rest as
advisory. Every case here is synthetic and states a rule, not an answer.
"""

from __future__ import annotations

import pytest

from dendro_inspector.config import GraphConfig, ReviewAuthority, load_config
from dendro_inspector.graph.executor import NodeContext
from dendro_inspector.graph.state import EvidenceQualityReport, GraphState
from dendro_inspector.knowledge.candidate_validation import validate_candidate_set
from dendro_inspector.nodes import final_decision
from dendro_inspector.nodes.review_synthesizer import adjudicate
from dendro_inspector.observability.trace import TraceRecorder
from dendro_inspector.schemas.candidates import Candidate, CandidateSet, SupportStrength
from dendro_inspector.schemas.decisions import DecisionDerivation, ResolutionBound
from dendro_inspector.schemas.evidence import (
    AttachmentStatus,
    EvidencePacket,
    Observation,
    ObservationSource,
    Subject,
)
from dendro_inspector.schemas.reviews import ReviewFinding, ReviewResult
from dendro_inspector.schemas.taxon import Resolution, resolution_rank

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


def _state(case, knowledge, *extra: Observation) -> GraphState:
    """A pine named at genus on a cone, the card's own strong-positive reading."""
    evidence = EvidencePacket(
        subjects=(Subject(subject_id=SUBJECT),),
        observations=(
            _observation(
                "cone",
                "cones.scale_shape",
                "woody_umbo",
                attachment=AttachmentStatus.CONFIRMED_ATTACHED,
            ),
            *extra,
        ),
    )
    candidates = validate_candidate_set(
        CandidateSet(
            subject_id=SUBJECT,
            candidates=(
                Candidate(
                    taxon="pinus",
                    resolution=Resolution.GENUS,
                    supporting_evidence_ids=("cone",),
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


def _decided(state: GraphState, ctx: NodeContext, *reviews: ReviewResult):
    reviewed = state.evolve(
        synthesis=adjudicate(reviews, evidence=state.evidence, knowledge=ctx.knowledge)
    )
    candidates = reviewed.candidates_for(SUBJECT)
    assert candidates is not None
    decision = final_decision.decide_subject(reviewed, ctx, candidates)
    (derivation,) = ctx.recorder.build(final_decisions=(decision,)).decision_derivations
    return decision, derivation


def _review_bounds(derivation: DecisionDerivation) -> list[ResolutionBound]:
    return [b for b in derivation.resolution_bounds if b.source == "reviewer_recommendation"]


class TestTheReleasedPolicyIsTheDefault:
    def test_the_setting_defaults_to_recommendation(self, monkeypatch):
        monkeypatch.delenv("DENDRO_REVIEW_AUTHORITY", raising=False)

        assert GraphConfig().review_authority is ReviewAuthority.RECOMMENDATION
        assert load_config().graph.review_authority is ReviewAuthority.RECOMMENDATION

    def test_the_environment_selects_the_experimental_policy(self, monkeypatch):
        monkeypatch.setenv("DENDRO_REVIEW_AUTHORITY", "evidence_bound")
        assert load_config().graph.review_authority is ReviewAuthority.EVIDENCE_BOUND

    def test_an_unknown_policy_fails_at_load(self, monkeypatch):
        monkeypatch.setenv("DENDRO_REVIEW_AUTHORITY", "advisory")
        with pytest.raises(ValueError, match="advisory"):
            load_config()

    def test_a_doubt_with_a_recommended_level_still_caps_the_claim(self, simple_case, node_context):
        """Pinned so the experiment cannot change what ships: today this names nothing."""
        decision, derivation = _decided(
            _state(simple_case, node_context.knowledge),
            node_context,
            _review(_finding(), recommended_resolution="unknown"),
        )

        assert decision.resolution is Resolution.UNKNOWN
        assert derivation.review_authority == "recommendation"
        assert derivation.resolution_binding_source == "reviewer_recommendation"
        assert derivation.advisory_recommendations == ()
        assert derivation.advisory_action_findings == ()


class TestADoubtDoesNotMoveTheLevel:
    """Under ``evidence_bound``: uncertainty is recorded, and the supported genus stands."""

    def test_a_finding_that_contradicts_nothing_leaves_the_genus_named(
        self, simple_case, node_context
    ):
        ctx = _evidence_bound(node_context)
        decision, derivation = _decided(
            _state(simple_case, ctx.knowledge),
            ctx,
            _review(_finding(), recommended_resolution="unknown"),
        )

        assert decision.selected_taxon == "pinus"
        assert decision.resolution is Resolution.GENUS
        assert derivation.review_authority == "evidence_bound"
        assert _review_bounds(derivation) == []
        (advisory,) = derivation.advisory_recommendations
        assert advisory.reviewer == "botanical"
        assert advisory.resolution is Resolution.UNKNOWN
        assert advisory.finding_id == "f1"
        assert advisory.advisory_reason == "not_validated"
        assert advisory.binding is False

    def test_a_finding_that_cites_nothing_is_advisory(self, simple_case, node_context):
        ctx = _evidence_bound(node_context)
        decision, derivation = _decided(
            _state(simple_case, ctx.knowledge),
            ctx,
            _review(_finding(evidence_ids=()), recommended_resolution="family"),
        )

        assert decision.resolution is Resolution.GENUS
        (advisory,) = derivation.advisory_recommendations
        assert advisory.advisory_reason == "no_cited_evidence"

    def test_a_level_with_no_finding_behind_it_is_advisory(self, simple_case, node_context):
        ctx = _evidence_bound(node_context)
        decision, derivation = _decided(
            _state(simple_case, ctx.knowledge), ctx, _review(recommended_resolution="unknown")
        )

        assert decision.resolution is Resolution.GENUS
        (advisory,) = derivation.advisory_recommendations
        assert advisory.finding_id is None
        assert advisory.advisory_reason == "no_finding"

    def test_an_unverified_request_to_lower_the_level_is_recorded_not_taken(
        self, simple_case, node_context
    ):
        lowering = _finding(category="resolution_too_specific", required_action="lower_resolution")
        state = _state(simple_case, node_context.knowledge)

        released, _ = _decided(state, node_context, _review(lowering))
        ctx = _evidence_bound(node_context)
        decision, derivation = _decided(state, ctx, _review(lowering))

        assert resolution_rank(released.resolution) < resolution_rank(Resolution.GENUS)
        assert decision.resolution is Resolution.GENUS
        assert derivation.resolution_action_applied is False
        assert derivation.resolution_action_findings == ()
        (unverified,) = derivation.advisory_action_findings
        assert (unverified.reviewer, unverified.finding_id) == ("botanical", "f1")

    def test_the_recommended_confidence_still_binds(self, simple_case, node_context):
        """The experiment is about the level. Confidence is composed as it always was."""
        state = _state(simple_case, node_context.knowledge)
        review = _review(_finding(), recommended_resolution="unknown", recommended_confidence="low")

        released, _ = _decided(state, node_context, review)
        decision, _ = _decided(state, _evidence_bound(node_context), review)

        assert decision.resolution is Resolution.GENUS
        assert decision.confidence == released.confidence


class TestAVerifiedFindingStillLowersTheLevel:
    """Under ``evidence_bound``: what code can check keeps its force."""

    def test_a_cited_card_contradiction_binds_the_recommended_level(
        self, simple_case, node_context
    ):
        # Resin blisters are on the pine card's own list of contradictions (fir-like bark).
        blisters = _observation("blisters", "bark.surface_marks", "resin_blisters")
        ctx = _evidence_bound(node_context)
        decision, derivation = _decided(
            _state(simple_case, ctx.knowledge, blisters),
            ctx,
            _review(
                _finding(category="botanical_contradiction", evidence_ids=("blisters",)),
                recommended_resolution="family",
            ),
        )

        assert decision.resolution is Resolution.FAMILY
        (bound,) = _review_bounds(derivation)
        (source,) = bound.sources
        assert (source.binding, source.ground) == (True, "card_contradiction")
        assert derivation.advisory_recommendations == ()

    def test_the_reviewers_category_does_not_decide_what_the_reading_is(
        self, simple_case, node_context
    ):
        blisters = _observation("blisters", "bark.surface_marks", "resin_blisters")
        ctx = _evidence_bound(node_context)
        decision, _ = _decided(
            _state(simple_case, ctx.knowledge, blisters),
            ctx,
            _review(_finding(evidence_ids=("blisters",)), recommended_resolution="family"),
        )

        assert decision.resolution is Resolution.FAMILY

    def test_a_contradiction_read_from_untrusted_evidence_does_not_bind(
        self, simple_case, node_context
    ):
        # A broadleaf at the frame edge contradicts pine on the card, and the trust boundary
        # holds it as context: it cannot support a claim, and it cannot withdraw one.
        stray = _observation(
            "stray-leaf", "leaf.type", "broadleaf_simple", attachment=AttachmentStatus.UNKNOWN
        )
        ctx = _evidence_bound(node_context)
        decision, derivation = _decided(
            _state(simple_case, ctx.knowledge, stray),
            ctx,
            _review(
                _finding(category="botanical_contradiction", evidence_ids=("stray-leaf",)),
                recommended_resolution="unknown",
            ),
        )

        assert decision.selected_taxon == "pinus"
        assert decision.resolution is Resolution.GENUS
        assert derivation.advisory_recommendations[0].advisory_reason == "not_validated"

    def test_contamination_binds_when_it_names_the_support_the_claim_rests_on(
        self, simple_case, node_context
    ):
        ctx = _evidence_bound(node_context)
        decision, derivation = _decided(
            _state(simple_case, ctx.knowledge),
            ctx,
            _review(
                _finding(category="subject_contamination", evidence_ids=("cone",)),
                recommended_resolution="unknown",
            ),
        )

        assert decision.resolution is Resolution.UNKNOWN
        (source,) = _review_bounds(derivation)[0].sources
        assert source.ground == "contaminated_support"

    def test_contamination_elsewhere_leaves_the_claim_standing(self, simple_case, node_context):
        stray = _observation(
            "stray-leaf", "leaf.type", "broadleaf_simple", attachment=AttachmentStatus.UNKNOWN
        )
        ctx = _evidence_bound(node_context)
        decision, _ = _decided(
            _state(simple_case, ctx.knowledge, stray),
            ctx,
            _review(
                _finding(category="subject_contamination", evidence_ids=("stray-leaf",)),
                recommended_resolution="unknown",
            ),
        )

        assert decision.selected_taxon == "pinus"
        assert decision.resolution is Resolution.GENUS

    def test_a_verified_request_to_lower_the_level_is_taken(self, simple_case, node_context):
        blisters = _observation("blisters", "bark.surface_marks", "resin_blisters")
        ctx = _evidence_bound(node_context)
        decision, derivation = _decided(
            _state(simple_case, ctx.knowledge, blisters),
            ctx,
            _review(
                _finding(
                    category="botanical_contradiction",
                    evidence_ids=("blisters",),
                    required_action="lower_resolution",
                )
            ),
        )

        assert resolution_rank(decision.resolution) < resolution_rank(Resolution.GENUS)
        assert derivation.resolution_action_applied is True
        assert derivation.advisory_action_findings == ()

    def test_an_alternative_binds_only_when_the_cited_readings_support_it_as_well(
        self, simple_case, node_context
    ):
        ctx = _evidence_bound(node_context)
        state = _state(simple_case, ctx.knowledge)
        # The cited cone is pine's own strong reading; it supports no larch.
        decision, derivation = _decided(
            state,
            ctx,
            _review(
                _finding(
                    category="overlooked_alternative",
                    proposed_taxon="larix",
                    evidence_ids=("cone",),
                ),
                recommended_resolution="family",
            ),
        )

        assert decision.resolution is Resolution.GENUS
        assert derivation.advisory_recommendations[0].advisory_reason == "not_validated"


class TestCodeRaisedFindingsKeepTheirForce:
    def test_a_deterministic_lowering_is_taken_under_both_policies(self, simple_case, node_context):
        raised = _finding(
            origin="deterministic",
            finding_id="auto-1",
            category="resolution_too_specific",
            required_action="lower_resolution",
            evidence_ids=(),
        )
        state = _state(simple_case, node_context.knowledge)

        released, _ = _decided(state, node_context, _review(raised))
        decision, derivation = _decided(state, _evidence_bound(node_context), _review(raised))

        assert decision.resolution is released.resolution
        assert resolution_rank(decision.resolution) < resolution_rank(Resolution.GENUS)
        assert derivation.advisory_action_findings == ()
