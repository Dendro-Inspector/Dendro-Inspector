"""Which review stood behind a bound, a confidence step or a withdrawn claim.

Attribution is a record, never a rule. The pooled levels it explains were always computed;
what was missing was any way to tell, afterwards, which reviewer asked for a downgrade and
whether a finding stood behind the request.
"""

from __future__ import annotations

import asyncio

import pytest

from dendro_inspector.graph.state import EvidenceQualityReport, GraphState
from dendro_inspector.knowledge.candidate_validation import validate_candidate_set
from dendro_inspector.nodes import abstain, final_decision
from dendro_inspector.nodes.review_synthesizer import adjudicate
from dendro_inspector.schemas.candidates import Candidate, CandidateSet, SupportStrength
from dendro_inspector.schemas.evidence import (
    AttachmentStatus,
    EvidencePacket,
    Observation,
    ObservationSource,
    Subject,
)
from dendro_inspector.schemas.reviews import (
    FindingAttribution,
    Reviewer,
    ReviewFinding,
    ReviewResult,
    ReviewSynthesis,
)
from dendro_inspector.schemas.taxon import Confidence, Resolution


def _state(case, knowledge, subjects=("tree_a", "tree_b")) -> GraphState:
    """Each subject a strongly supported pine, so an unreviewed verdict is genus at high."""
    evidence = EvidencePacket(
        subjects=tuple(Subject(subject_id=subject) for subject in subjects),
        observations=tuple(
            Observation(
                observation_id=f"{subject}-cone",
                subject_id=subject,
                feature="cones.scale_shape",
                value="woody_umbo",
                source=ObservationSource.IMAGE,
                image_id="img-1",
                attachment=AttachmentStatus.CONFIRMED_ATTACHED,
            )
            for subject in subjects
        ),
    )
    sets = tuple(
        validate_candidate_set(
            CandidateSet(
                subject_id=subject,
                candidates=(
                    Candidate(
                        taxon="pinus",
                        resolution=Resolution.GENUS,
                        supporting_evidence_ids=(f"{subject}-cone",),
                        score=SupportStrength.STRONG,
                        rank=1,
                    ),
                ),
            ),
            evidence,
            knowledge,
        )
        for subject in subjects
    )
    return GraphState(
        case=case,
        evidence=evidence,
        candidate_sets=sets,
        quality=EvidenceQualityReport(sufficient=True, usable_subject_ids=subjects),
    )


def _finding(**updates) -> ReviewFinding:
    return ReviewFinding.model_validate(
        {
            "finding_id": "f1",
            "category": "confidence_miscalibration",
            "severity": "major",
            "subject_id": "tree_a",
            "evidence_ids": ("tree_a-cone",),
            "summary": "This observation warrants a more conservative confidence.",
            "required_action": "lower_confidence",
            "impact": "confidence_change",
            **updates,
        }
    )


def _lowering(**updates) -> ReviewFinding:
    return _finding(
        category="resolution_too_specific",
        required_action="lower_resolution",
        impact="resolution_change",
        **updates,
    )


def _review(reviewer: str, *findings: ReviewFinding, **fields) -> ReviewResult:
    return ReviewResult.model_validate(
        {
            "reviewer": reviewer,
            "status": "pass_with_findings" if findings else "pass",
            "findings": findings,
            **fields,
        }
    )


def _reviewed(state: GraphState, ctx, *reviews: ReviewResult) -> GraphState:
    return state.evolve(
        synthesis=adjudicate(reviews, evidence=state.evidence, knowledge=ctx.knowledge)
    )


def _arbitrated(state: GraphState, ctx, *findings: ReviewFinding) -> GraphState:
    return state.evolve(
        arbiter_synthesis=adjudicate(
            (_review("arbiter", *findings),), evidence=state.evidence, knowledge=ctx.knowledge
        )
    )


def _decided(state: GraphState, ctx, subject: str = "tree_a"):
    """The verdict for one subject and the derivation recorded for it."""
    candidates = state.candidates_for(subject)
    assert candidates is not None
    decision = final_decision.decide_subject(state, ctx, candidates)
    (derivation,) = ctx.recorder.build(final_decisions=(decision,)).decision_derivations
    return decision, derivation


def _attributions(synthesis: ReviewSynthesis) -> tuple[FindingAttribution, ...]:
    assert synthesis.attributions is not None
    return synthesis.attributions


def _review_bound(derivation):
    (bound,) = [b for b in derivation.resolution_bounds if b.source == "reviewer_recommendation"]
    return bound


def _steps(derivation, source: str):
    return [step for step in derivation.confidence_steps if step.source == source]


class TestFindingAttribution:
    """Every adjudicated finding is bound to the review that raised it."""

    def _synthesis(self, case, ctx) -> ReviewSynthesis:
        state = _state(case, ctx.knowledge)
        return adjudicate(
            (
                _review("botanical", _finding()),
                _review("confusion"),
                _review(
                    "confidence",
                    _finding(category="missing_decisive_feature"),
                    _finding(finding_id="f2", evidence_ids=("no-such-observation",)),
                ),
            ),
            evidence=state.evidence,
            knowledge=ctx.knowledge,
        )

    def test_accepted_and_rejected_findings_are_both_attributed(self, simple_case, node_context):
        attributions = _attributions(self._synthesis(simple_case, node_context))

        assert [
            (a.reviewer.value, a.result_index, a.position, a.finding.status.value)
            for a in attributions
        ] == [
            ("botanical", 0, 0, "accepted"),
            ("confidence", 2, 0, "accepted"),
            ("confidence", 2, 1, "rejected"),
        ]
        assert attributions[-1].finding.reason_code == "evidence_id_unknown"

    def test_reviewers_that_reuse_a_finding_id_get_different_references(
        self, simple_case, node_context
    ):
        """Three reviewers each number from one, so an id alone names three claims."""
        references = [
            attribution.reference("internal", 1)
            for attribution in _attributions(self._synthesis(simple_case, node_context))
            if attribution.finding.finding_id == "f1"
        ]

        assert references == ["internal-1/1-botanical/1", "internal-1/3-confidence/1"]

    def test_attributions_cannot_leave_out_an_adjudicated_finding(self, simple_case, node_context):
        payload = self._synthesis(simple_case, node_context).model_dump()
        payload["attributions"] = payload["attributions"][:-1]

        with pytest.raises(ValueError, match="attributions must list exactly"):
            ReviewSynthesis.model_validate(payload)

    def test_a_finding_nobody_adjudicated_cannot_be_attributed(self):
        with pytest.raises(ValueError, match="adjudicated status"):
            FindingAttribution(
                reviewer=Reviewer.BOTANICAL, result_index=0, position=0, finding=_finding()
            )


class TestReviewBoundSources:
    """A `reviewer_recommendation` bound says whose recommendation it was."""

    def test_every_recommendation_is_listed_and_the_binding_one_is_marked(
        self, simple_case, node_context
    ):
        state = _reviewed(
            _state(simple_case, node_context.knowledge),
            node_context,
            _review("confusion", subject_id="tree_a", recommended_resolution="genus"),
            _review("confidence", _finding(), subject_id="tree_a", recommended_resolution="family"),
        )

        decision, derivation = _decided(state, node_context)

        bound = _review_bound(derivation)
        assert bound.value is Resolution.FAMILY
        assert [
            (s.stage, s.reviewer, s.resolution, s.finding_id, s.finding_ref, s.binding)
            for s in bound.sources
        ] == [
            ("internal", "confusion", Resolution.GENUS, None, None, False),
            ("internal", "confidence", Resolution.FAMILY, "f1", "internal-1/2-confidence/1", True),
        ]
        assert derivation.resolution_binding_source == "reviewer_recommendation"
        assert decision.resolution is Resolution.FAMILY

    def test_a_bare_recommendation_that_sets_the_claim_is_recorded_as_bare(
        self, simple_case, node_context
    ):
        """The case the record exists for: a level with no finding behind it lowers a claim."""
        state = _reviewed(
            _state(simple_case, node_context.knowledge),
            node_context,
            _review(
                "confidence",
                subject_id="tree_a",
                recommended_resolution="family",
                recommended_confidence="low",
            ),
        )

        decision, derivation = _decided(state, node_context)

        (resolution_source,) = _review_bound(derivation).sources
        assert resolution_source.reviewer == "confidence"
        assert resolution_source.finding_id is None
        assert resolution_source.finding_ref is None
        assert resolution_source.binding
        (step,) = _steps(derivation, "reviewer_recommendation")
        assert step.applied
        assert [(s.reviewer, s.confidence, s.finding_id, s.binding) for s in step.sources] == [
            ("confidence", Confidence.LOW, None, True)
        ]
        assert (decision.resolution, decision.confidence) == (Resolution.FAMILY, Confidence.LOW)

    def test_a_pooled_level_with_no_bindings_is_named_a_legacy_aggregate(
        self, simple_case, node_context
    ):
        state = _state(simple_case, node_context.knowledge, subjects=("tree_a",)).evolve(
            synthesis=ReviewSynthesis(resolution_delta=Resolution.FAMILY)
        )

        _, derivation = _decided(state, node_context)

        assert [(s.stage, s.reviewer, s.binding) for s in _review_bound(derivation).sources] == [
            ("legacy_aggregate", None, True)
        ]

    def test_another_subjects_recommendation_is_not_listed(self, simple_case, node_context):
        state = _reviewed(
            _state(simple_case, node_context.knowledge),
            node_context,
            _review("confidence", subject_id="tree_b", recommended_resolution="family"),
        )

        _, derivation = _decided(state, node_context)

        assert all(b.source != "reviewer_recommendation" for b in derivation.resolution_bounds)


class TestFindingSteps:
    """A step a finding caused names the reviewer and the pass it came from."""

    def test_each_confidence_step_names_the_review_behind_its_finding(
        self, simple_case, node_context
    ):
        state = _reviewed(
            _state(simple_case, node_context.knowledge),
            node_context,
            _review("botanical", _finding()),
            _review("confidence", _finding(category="missing_decisive_feature")),
        )

        _, derivation = _decided(state, node_context)

        assert [
            (s.finding_id, s.stage, s.reviewer, s.finding_ref)
            for s in _steps(derivation, "model_finding")
        ] == [
            ("f1", "internal", "botanical", "internal-1/1-botanical/1"),
            ("f1", "internal", "confidence", "internal-1/2-confidence/1"),
        ]

    def test_an_arbiter_finding_is_attributed_to_the_arbiter_pass(self, simple_case, node_context):
        state = _arbitrated(
            _reviewed(_state(simple_case, node_context.knowledge), node_context),
            node_context,
            _finding(),
        )

        _, derivation = _decided(state, node_context)

        (step,) = _steps(derivation, "model_finding")
        assert (step.stage, step.reviewer, step.finding_ref) == (
            "arbiter",
            "arbiter",
            "arbiter-1/1-arbiter/1",
        )

    def test_the_round_in_a_reference_is_the_one_that_survived_a_retry(
        self, simple_case, node_context
    ):
        state = _reviewed(
            _state(simple_case, node_context.knowledge),
            node_context,
            _review("botanical", _finding()),
        ).evolve(retries=1)

        _, derivation = _decided(state, node_context)

        (step,) = _steps(derivation, "model_finding")
        assert step.finding_ref == "internal-2/1-botanical/1"

    def test_an_owed_lowering_names_its_finding(self, simple_case, node_context):
        state = _reviewed(
            _state(simple_case, node_context.knowledge),
            node_context,
            _review("botanical", _lowering()),
        )

        decision, derivation = _decided(state, node_context)

        assert derivation.resolution_action_applied
        assert [
            (f.reviewer, f.finding_ref, f.category) for f in derivation.resolution_action_findings
        ] == [("botanical", "internal-1/1-botanical/1", "resolution_too_specific")]
        assert decision.resolution is Resolution.FAMILY

    def test_a_lowering_a_bound_absorbed_is_still_named(self, simple_case, node_context):
        """Otherwise a finding would vanish from the record whenever a bound got there first."""
        state = _reviewed(
            _state(simple_case, node_context.knowledge),
            node_context,
            _review("botanical", _lowering()),
            _review("confidence", subject_id="tree_a", recommended_resolution="family"),
        )

        decision, derivation = _decided(state, node_context)

        assert not derivation.resolution_action_applied
        assert [f.finding_ref for f in derivation.resolution_action_findings] == [
            "internal-1/1-botanical/1"
        ]
        assert decision.resolution is Resolution.FAMILY

    def test_a_synthesis_without_attributions_decides_the_same_and_names_no_reviewer(
        self, simple_case, node_context
    ):
        attributed = _reviewed(
            _state(simple_case, node_context.knowledge),
            node_context,
            _review("botanical", _finding()),
            _review(
                "confidence", _lowering(), subject_id="tree_a", recommended_confidence="medium"
            ),
        )
        assert attributed.synthesis is not None
        unattributed = attributed.evolve(
            synthesis=ReviewSynthesis.model_validate(
                {**attributed.synthesis.model_dump(), "attributions": None}
            )
        )

        with_record, _ = _decided(attributed, node_context)
        without_record, derivation = _decided(unattributed, node_context)

        assert without_record == with_record
        (step,) = _steps(derivation, "model_finding")
        assert (step.finding_id, step.stage) == ("f1", "internal")
        assert step.reviewer is None
        assert step.finding_ref is None


class TestAbstentionRecord:
    """A withdrawn claim says which of four different reasons withdrew it."""

    def _abstentions(self, ctx):
        return {record.subject_id: record for record in ctx.recorder.build().abstentions}

    def test_a_grounded_finding_is_recorded_as_the_cause(self, simple_case, node_context):
        state = _reviewed(
            _state(simple_case, node_context.knowledge),
            node_context,
            _review("confusion", _finding(required_action="abstain", severity="critical")),
        )

        asyncio.run(abstain.run(state, node_context))

        (record,) = self._abstentions(node_context).values()
        assert (record.subject_id, record.cause) == ("tree_a", "unresolvable_finding")
        assert record.resolution is Resolution.FAMILY
        assert [(f.reviewer, f.finding_ref) for f in record.findings] == [
            ("confusion", "internal-1/1-confusion/1")
        ]

    def test_a_spent_retry_budget_is_told_apart_from_an_unresolvable_finding(
        self, simple_case, node_context
    ):
        state = _reviewed(
            _state(simple_case, node_context.knowledge),
            node_context,
            _review(
                "confidence", _finding(required_action="re_extract_evidence", severity="critical")
            ),
        ).evolve(retries=1)

        asyncio.run(abstain.run(state, node_context))

        (record,) = self._abstentions(node_context).values()
        assert record.cause == "retry_budget_spent"
        assert [f.finding_ref for f in record.findings] == ["internal-2/1-confidence/1"]

    def test_an_abstention_no_arbiter_looked_at_is_recorded_as_unarbitrated(
        self, simple_case, node_context
    ):
        state = _reviewed(
            _state(simple_case, node_context.knowledge),
            node_context,
            _review("confidence", _unverified()),
        )

        abstain.settle_unverified_abstention(state, node_context)

        records = self._abstentions(node_context)
        assert {subject: record.cause for subject, record in records.items()} == {
            "tree_a": "unverified_abstention_unarbitrated",
            "tree_b": "unverified_abstention_unarbitrated",
        }
        assert [f.reviewer for f in records["tree_b"].findings] == ["confidence"]

    def test_an_abstention_the_arbiter_confirmed_is_recorded_for_that_subject_only(
        self, simple_case, node_context
    ):
        state = _arbitrated(
            _reviewed(
                _state(simple_case, node_context.knowledge),
                node_context,
                _review("confidence", _unverified()),
            ),
            node_context,
            _finding(required_action="abstain", severity="critical"),
        )

        abstain.settle_unverified_abstention(state, node_context)

        records = self._abstentions(node_context)
        assert {subject: record.cause for subject, record in records.items()} == {
            "tree_a": "unverified_abstention_confirmed"
        }

    def test_an_abstention_the_arbiter_refused_leaves_no_record(self, simple_case, node_context):
        state = _arbitrated(
            _reviewed(
                _state(simple_case, node_context.knowledge),
                node_context,
                _review("confidence", _unverified()),
            ),
            node_context,
        )

        abstain.settle_unverified_abstention(state, node_context)

        assert self._abstentions(node_context) == {}


def _unverified() -> ReviewFinding:
    """A model's critical abstain request that cites nothing."""
    return _finding(
        finding_id="unverified",
        subject_id=None,
        evidence_ids=(),
        required_action="abstain",
        severity="critical",
    )
