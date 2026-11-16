"""Adaptive routing: a review plan derived by code, measured in shadow, followed when enabled.

The plan decides which reviewer *models* are called. These tests hold it to three promises:
anything ambiguous keeps the full review, a skipped model call never skips a code-raised
finding, and shadow mode changes nothing but the trace.
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from dendro_inspector.config import AdaptiveMode, GraphConfig
from dendro_inspector.evaluation.runner import load_cases
from dendro_inspector.graph.definition import REVIEW_FANOUT, NodeName
from dendro_inspector.graph.executor import GraphExecutionError, planned_fanout
from dendro_inspector.graph.projections import build_review_projection
from dendro_inspector.graph.routing import RoutingError, next_step, route_adaptive_gate
from dendro_inspector.graph.state import EvidenceQualityReport, GraphState, GuardReport
from dendro_inspector.nodes.adaptive_gate import SubjectFacts, assess
from dendro_inspector.nodes.review_synthesizer import adjudicate
from dendro_inspector.runner import run_case
from dendro_inspector.schemas.decisions import DecisionStatus
from dendro_inspector.schemas.evidence import EvidencePacket, Subject
from dendro_inspector.schemas.input import CaseInput
from dendro_inspector.schemas.reviews import (
    FindingCategory,
    FindingOrigin,
    FindingStatus,
    ReasonCode,
    RequiredAction,
    Reviewer,
    ReviewFinding,
    ReviewResult,
    ReviewStatus,
    ReviewSynthesis,
    Severity,
)
from dendro_inspector.schemas.routing import (
    PLANNED_REVIEWERS,
    ProvisionalOutcome,
    ReviewQuestion,
    ReviewRoute,
    RoutingAssessment,
)
from dendro_inspector.schemas.taxon import Confidence, Resolution
from tests.conftest import make_config

SUBJECT = "tree_1"


def _outcome(**changes) -> ProvisionalOutcome:
    base = {
        "subject_id": SUBJECT,
        "status": DecisionStatus.IDENTIFIED,
        "selected_taxon": "betula",
        "resolution": Resolution.GENUS,
        "confidence": Confidence.HIGH,
    }
    return ProvisionalOutcome(**{**base, **changes})


def _fact(**changes) -> SubjectFacts:
    """A subject code alone names at genus level with high confidence, uncontested."""
    base = {
        "subject_id": SUBJECT,
        "outcome": _outcome(),
        "leader": "betula",
        "runner_up": "populus",
        "discriminating_evidence_ids": ("obs_1",),
    }
    return SubjectFacts(**{**base, **changes})


def _state(**changes) -> GraphState:
    base = GraphState(
        case=CaseInput(case_id="c1", user_text="x"),
        guard=GuardReport(),
        quality=EvidenceQualityReport(sufficient=True, usable_subject_ids=(SUBJECT,)),
        evidence=EvidencePacket(subjects=(Subject(subject_id=SUBJECT),)),
    )
    return base.evolve(**changes)


def _failed(plan: RoutingAssessment) -> set[str]:
    return {check.name for check in plan.checks if not check.passed}


def _codes(plan: RoutingAssessment) -> tuple[str, ...]:
    return tuple(question.code for question in plan.questions)


CLEAN = ReviewSynthesis()


class TestCleanCase:
    def test_without_the_fast_path_a_clean_case_keeps_the_confidence_reviewer(self):
        plan = assess(_state(), (_fact(),), CLEAN, applied=True)
        assert plan.route is ReviewRoute.TARGETED_REVIEW
        assert plan.fast_path_eligible
        assert plan.reviewers == (Reviewer.CONFIDENCE,)
        assert plan.skipped_reviewers == (Reviewer.BOTANICAL, Reviewer.CONFUSION)
        assert _codes(plan) == ("claim_strength",)
        assert _failed(plan) == set()

    def test_the_fast_path_switch_calls_no_reviewer(self):
        plan = assess(_state(), (_fact(),), CLEAN, applied=True, fast_path=True)
        assert plan.route is ReviewRoute.FAST_PATH
        assert plan.reviewers == ()
        assert plan.bypassed_model_calls == len(PLANNED_REVIEWERS)
        assert plan.questions == ()

    def test_shadow_records_the_plan_and_calls_every_reviewer(self):
        plan = assess(_state(), (_fact(),), CLEAN, applied=False, fast_path=True)
        assert plan.route is ReviewRoute.FAST_PATH
        assert not plan.applied
        assert all(plan.calls(reviewer) for reviewer in PLANNED_REVIEWERS)

    def test_the_provisional_verdict_is_recorded(self):
        plan = assess(_state(), (_fact(),), CLEAN)
        assert plan.provisional == (_outcome(),)

    def test_the_plan_is_a_pure_function_of_its_inputs(self):
        state, facts = _state(), (_fact(),)
        assert assess(state, facts, CLEAN, applied=True) == assess(
            state, facts, CLEAN, applied=True
        )


class TestCaseRulesKeepFullReview:
    @pytest.mark.parametrize(
        ("changes", "fact_changes", "rule"),
        [
            ({"retries": 1}, {}, "first_review_round"),
            (
                {"case": CaseInput(case_id="c1", user_text="x", user_claim="oak")},
                {},
                "no_user_claim_or_challenge",
            ),
            (
                {"guard": GuardReport(instruction_like_signals=("ignore_previous",))},
                {},
                "no_instruction_like_content",
            ),
            (
                {
                    "evidence": EvidencePacket(
                        subjects=(Subject(subject_id=SUBJECT),), possible_multiple_taxa=True
                    )
                },
                {},
                "single_taxon_expected",
            ),
            (
                {
                    "quality": EvidenceQualityReport(
                        sufficient=True,
                        usable_subject_ids=(SUBJECT,),
                        unattached_evidence_ids=("obs_9",),
                    )
                },
                {},
                "attachment_settled",
            ),
            ({}, {"species_proposed": True}, "no_species_level_candidate"),
            ({"guard": None}, {}, "evidence_and_quality_present"),
        ],
    )
    def test_one_failed_case_rule_keeps_every_reviewer(self, changes, fact_changes, rule):
        plan = assess(
            _state(**changes), (_fact(**fact_changes),), CLEAN, applied=True, fast_path=True
        )
        assert plan.route is ReviewRoute.FULL_REVIEW
        assert plan.reasons == ("case_rule_failed",)
        assert rule in _failed(plan)
        assert plan.reviewers == PLANNED_REVIEWERS
        assert plan.skipped_reviewers == ()
        assert plan.questions == ()
        assert not plan.fast_path_eligible

    def test_several_subjects_keep_every_reviewer(self):
        state = _state(
            evidence=EvidencePacket(
                subjects=(Subject(subject_id=SUBJECT), Subject(subject_id="tree_2"))
            )
        )
        facts = (_fact(), _fact(subject_id="tree_2", outcome=_outcome(subject_id="tree_2")))
        plan = assess(state, facts, CLEAN, applied=True, fast_path=True)
        assert plan.route is ReviewRoute.FULL_REVIEW
        assert "single_subject" in _failed(plan)
        assert len(plan.provisional) == 2


class TestNothingAdmitted:
    def test_no_admitted_candidate_calls_no_reviewer(self):
        plan = assess(_state(), (SubjectFacts(subject_id=SUBJECT),), CLEAN, applied=True)
        assert plan.route is ReviewRoute.REQUEST_PHOTOS
        assert plan.reasons == ("nothing_admitted",)
        assert plan.reviewers == ()
        assert plan.provisional == ()

    def test_a_failed_case_rule_still_wins(self):
        plan = assess(_state(retries=1), (SubjectFacts(subject_id=SUBJECT),), CLEAN, applied=True)
        assert plan.route is ReviewRoute.FULL_REVIEW


def _actionable_synthesis() -> ReviewSynthesis:
    finding = ReviewFinding(
        finding_id="auto-colour-tree_1",
        category=FindingCategory.COLOUR_OVERWEIGHTING,
        severity=Severity.MAJOR,
        status=FindingStatus.ACCEPTED,
        origin=FindingOrigin.DETERMINISTIC,
        reason_code=ReasonCode.IMPROVES_CALIBRATION,
        summary="The claim leans on colour.",
        subject_id=SUBJECT,
        required_action=RequiredAction.LOWER_CONFIDENCE,
    )
    return ReviewSynthesis(accepted_findings=(finding,))


class TestFastPathRules:
    @pytest.mark.parametrize(
        ("changes", "fact_changes", "rule"),
        [
            ({}, {"outcome": _outcome(confidence=Confidence.MEDIUM)}, "high_confidence"),
            ({}, {"outcome": _outcome(resolution=Resolution.FAMILY)}, "named_at_genus_level"),
            ({}, {"discriminating_evidence_ids": ()}, "discriminating_support"),
            ({}, {"contradicting_evidence_ids": ("obs_2",)}, "no_contradicting_evidence"),
            ({}, {"leaders_close": True}, "leaders_separated"),
            (
                {
                    "quality": EvidenceQualityReport(
                        sufficient=True,
                        usable_subject_ids=(SUBJECT,),
                        colour_dependence_detected=True,
                    )
                },
                {},
                "not_colour_dependent",
            ),
            (
                {
                    "quality": EvidenceQualityReport(
                        sufficient=True,
                        usable_subject_ids=(SUBJECT,),
                        unmatchable_evidence_ids=("obs_7",),
                    )
                },
                {},
                "no_knowledge_coverage_gap",
            ),
        ],
    )
    def test_one_failed_rule_withholds_the_fast_path(self, changes, fact_changes, rule):
        plan = assess(
            _state(**changes), (_fact(**fact_changes),), CLEAN, applied=True, fast_path=True
        )
        assert plan.route is not ReviewRoute.FAST_PATH
        assert not plan.fast_path_eligible
        assert _failed(plan) == {rule}
        assert plan.reviewers, "a case that is not eligible is reviewed by someone"

    def test_an_unreadable_photograph_is_not_a_reason_to_call_a_reviewer(self):
        """No reviewer sees a file that could not be read, so a call recovers nothing."""
        state = _state(guard=GuardReport(missing_images=("img-2",)))
        plan = assess(state, (_fact(),), CLEAN, applied=True, fast_path=True)
        assert plan.route is ReviewRoute.FAST_PATH

    def test_a_code_finding_that_asks_for_something_withholds_the_fast_path(self):
        plan = assess(_state(), (_fact(),), _actionable_synthesis(), applied=True, fast_path=True)
        assert plan.route is ReviewRoute.TARGETED_REVIEW
        assert _failed(plan) == {"no_code_finding_requires_action"}


class TestQuestions:
    def test_evidence_against_the_leader_calls_the_botanical_reviewer(self):
        plan = assess(
            _state(), (_fact(contradicting_evidence_ids=("obs_2",)),), CLEAN, applied=True
        )
        assert plan.route is ReviewRoute.TARGETED_REVIEW
        assert plan.reviewers == (Reviewer.BOTANICAL, Reviewer.CONFIDENCE)
        assert plan.skipped_reviewers == (Reviewer.CONFUSION,)
        botanical = next(q for q in plan.questions if q.reviewer is Reviewer.BOTANICAL)
        assert botanical.code == "contradiction_against_leader"
        assert botanical.evidence_ids == ("obs_2",)

    def test_close_leaders_call_the_confusion_reviewer(self):
        plan = assess(_state(), (_fact(leaders_close=True),), CLEAN, applied=True)
        assert plan.reviewers == (Reviewer.CONFUSION, Reviewer.CONFIDENCE)
        assert "leaders_not_separated" in _codes(plan)

    def test_a_leader_code_cannot_name_goes_to_the_confusion_reviewer_alone(self):
        unnamed = _outcome(
            status=DecisionStatus.INSUFFICIENT_EVIDENCE,
            selected_taxon=None,
            resolution=Resolution.UNKNOWN,
            confidence=Confidence.LOW,
        )
        plan = assess(
            _state(),
            (_fact(outcome=unnamed, discriminating_evidence_ids=()),),
            CLEAN,
            applied=True,
        )
        assert plan.route is ReviewRoute.TARGETED_REVIEW
        assert plan.reviewers == (Reviewer.CONFUSION,)
        assert _codes(plan) == ("leader_not_established",)

    def test_a_question_for_every_reviewer_is_the_full_review(self):
        plan = assess(
            _state(),
            (_fact(contradicting_evidence_ids=("obs_2",), leaders_close=True),),
            CLEAN,
            applied=True,
        )
        assert plan.route is ReviewRoute.FULL_REVIEW
        assert plan.reasons == ("every_reviewer_has_a_question",)
        assert plan.questions == (), "full review is exactly the review without the experiment"


class TestPlanContract:
    def test_the_fast_path_needs_eligibility(self):
        with pytest.raises(ValidationError, match="every eligibility rule"):
            RoutingAssessment(route=ReviewRoute.FAST_PATH, skipped_reviewers=PLANNED_REVIEWERS)

    def test_every_reviewer_is_accounted_for(self):
        with pytest.raises(ValidationError, match="accounts for every internal reviewer"):
            RoutingAssessment(route=ReviewRoute.TARGETED_REVIEW, reviewers=(Reviewer.CONFUSION,))

    def test_full_review_skips_nobody(self):
        with pytest.raises(ValidationError, match="skips no reviewer"):
            RoutingAssessment(
                route=ReviewRoute.FULL_REVIEW,
                reviewers=(Reviewer.BOTANICAL, Reviewer.CONFUSION),
                skipped_reviewers=(Reviewer.CONFIDENCE,),
            )

    def test_a_question_goes_to_a_reviewer_that_is_called(self):
        with pytest.raises(ValidationError, match="reviewer the plan calls"):
            RoutingAssessment(
                route=ReviewRoute.TARGETED_REVIEW,
                reviewers=(Reviewer.CONFUSION,),
                skipped_reviewers=(Reviewer.BOTANICAL, Reviewer.CONFIDENCE),
                questions=(ReviewQuestion(reviewer=Reviewer.BOTANICAL, code="x", question="Why?"),),
            )

    def test_the_arbiter_is_never_part_of_a_plan(self):
        with pytest.raises(ValidationError):
            RoutingAssessment(
                route=ReviewRoute.FULL_REVIEW, reviewers=(*PLANNED_REVIEWERS, Reviewer.ARBITER)
            )


def _targeted(*, applied: bool) -> RoutingAssessment:
    return RoutingAssessment(
        route=ReviewRoute.TARGETED_REVIEW,
        applied=applied,
        reviewers=(Reviewer.CONFIDENCE,),
        skipped_reviewers=(Reviewer.BOTANICAL, Reviewer.CONFUSION),
    )


def _no_model_review(*, applied: bool) -> RoutingAssessment:
    return RoutingAssessment(
        route=ReviewRoute.REQUEST_PHOTOS, applied=applied, skipped_reviewers=PLANNED_REVIEWERS
    )


class TestRouting:
    def test_off_keeps_the_released_topology(self):
        assert (
            next_step(NodeName.ATTACHMENT_AUTHORITY_GATE, _state(), GraphConfig())
            is NodeName.BOTANICAL_REVIEWER
        )

    @pytest.mark.parametrize("mode", [AdaptiveMode.SHADOW, AdaptiveMode.ENABLED])
    def test_any_other_mode_runs_the_gate(self, mode):
        config = GraphConfig(adaptive_routing=mode)
        assert (
            next_step(NodeName.ATTACHMENT_AUTHORITY_GATE, _state(), config)
            is NodeName.ADAPTIVE_GATE
        )

    def test_a_missing_plan_is_an_error_not_a_guess(self):
        with pytest.raises(RoutingError, match="no review plan"):
            route_adaptive_gate(_state())

    def test_an_applied_plan_without_reviewers_goes_to_the_synthesizer(self):
        state = _state(routing_assessment=_no_model_review(applied=True))
        assert route_adaptive_gate(state) is NodeName.REVIEW_SYNTHESIZER

    def test_a_recorded_plan_without_reviewers_still_runs_the_review(self):
        state = _state(routing_assessment=_no_model_review(applied=False))
        assert route_adaptive_gate(state) is NodeName.BOTANICAL_REVIEWER

    def test_a_targeted_plan_goes_to_the_fan_out(self):
        state = _state(routing_assessment=_targeted(applied=True))
        assert route_adaptive_gate(state) is NodeName.BOTANICAL_REVIEWER


class TestFanOut:
    def test_no_plan_calls_every_reviewer(self):
        assert planned_fanout(_state()) == REVIEW_FANOUT

    def test_a_recorded_plan_calls_every_reviewer(self):
        assert planned_fanout(_state(routing_assessment=_targeted(applied=False))) == REVIEW_FANOUT

    def test_an_applied_plan_calls_only_its_reviewers(self):
        state = _state(routing_assessment=_targeted(applied=True))
        assert planned_fanout(state) == (NodeName.CONFIDENCE_REVIEWER,)

    def test_a_plan_without_reviewers_must_not_reach_the_fan_out(self):
        state = _state(routing_assessment=_no_model_review(applied=True))
        with pytest.raises(GraphExecutionError, match="calls no reviewer"):
            planned_fanout(state)


class TestCodeOnlyReviewsAreNotOpinions:
    def test_a_pass_nobody_gave_is_not_dissent(self, knowledge):
        """A reviewer that was not asked cannot disagree with one that failed the result."""
        results = (
            ReviewResult(reviewer=Reviewer.BOTANICAL, status=ReviewStatus.PASS),
            ReviewResult(reviewer=Reviewer.CONFUSION, status=ReviewStatus.FAIL_CORRECTABLE),
        )
        asked = adjudicate(results, evidence=None, knowledge=knowledge)
        unasked = adjudicate(
            results,
            evidence=None,
            knowledge=knowledge,
            without_opinion=frozenset({Reviewer.BOTANICAL}),
        )
        assert asked.reviewer_disagreement
        assert not unasked.reviewer_disagreement


def _public_case(repo_root, case_id: str):
    return next(
        case for case in load_cases(repo_root / "evals" / "public") if case.case_id == case_id
    )


def _run(
    repo_root,
    case_id: str,
    mode: AdaptiveMode,
    *,
    fast_path: bool = False,
    without_user_claim: bool = False,
):
    case = _public_case(repo_root, case_id)
    base = make_config(case.scenario)
    config = base.model_copy(
        update={
            "graph": base.graph.model_copy(
                update={"adaptive_routing": mode, "adaptive_fast_path": fast_path}
            )
        }
    )
    case_input = (
        case.input.model_copy(update={"user_claim": None}) if without_user_claim else case.input
    )
    return asyncio.run(run_case(case_input, config=config, root=repo_root))


def _model_calls(result) -> list[str]:
    return [call.node for event in result.trace.events for call in event.provider_calls]


def _verdicts(result) -> list[dict[str, object]]:
    return [decision.model_dump(mode="json") for decision in result.state.decisions]


class TestGraphRuns:
    """End to end on the fake provider, with public fixtures used as inputs only."""

    @pytest.mark.parametrize(
        "case_id",
        ["light-trunk-birch-001", "strong-label-thin-support-001", "candidate-sanitization-001"],
    )
    def test_shadow_changes_nothing_but_the_trace(self, repo_root, case_id):
        off = _run(repo_root, case_id, AdaptiveMode.OFF)
        shadow = _run(repo_root, case_id, AdaptiveMode.SHADOW)
        assert _verdicts(shadow) == _verdicts(off)
        assert _model_calls(shadow) == _model_calls(off)
        assert off.trace.routing_history == ()
        (plan,) = shadow.trace.routing_history
        assert not plan.applied
        assert all(
            reviewer.model_called
            for record in shadow.trace.review_rounds
            for reviewer in record.reviewers
        )

    def test_enabled_calls_only_the_planned_reviewer(self, repo_root):
        result = _run(repo_root, "strong-label-thin-support-001", AdaptiveMode.ENABLED)
        (plan,) = result.trace.routing_history
        assert plan.applied
        assert plan.route is ReviewRoute.TARGETED_REVIEW
        assert plan.reviewers == (Reviewer.CONFUSION,)
        reviewer_calls = [node for node in _model_calls(result) if node.endswith("_reviewer")]
        assert reviewer_calls == ["confusion_reviewer"]
        internal = next(r for r in result.trace.review_rounds if r.stage == "internal")
        assert {r.reviewer: r.model_called for r in internal.reviewers} == {
            "botanical": False,
            "confusion": True,
            "confidence": False,
        }

    def test_the_fast_path_calls_no_reviewer_and_leaves_the_arbiter_to_its_policy(self, repo_root):
        """A fruit in the frame and no user claim: code alone names the genus, high.

        The fixture's own user claim is removed, because a claim always keeps the full
        review. The arbiter is still called for the high-confidence verdict: skipping the
        reviewers and skipping arbitration are separate decisions.
        """
        runs = {
            label: _run(
                repo_root, "apple-with-fruit-001", mode, fast_path=fast, without_user_claim=True
            )
            for label, mode, fast in (
                ("off", AdaptiveMode.OFF, False),
                ("confidence_only", AdaptiveMode.ENABLED, False),
                ("fast", AdaptiveMode.ENABLED, True),
            )
        }
        (plan,) = runs["fast"].trace.routing_history
        assert plan.route is ReviewRoute.FAST_PATH
        assert plan.fast_path_eligible
        assert [n for n in _model_calls(runs["fast"]) if n.endswith("_reviewer")] == []
        assert "arbiter" in _model_calls(runs["fast"])
        assert "high_confidence_proposed" in runs["fast"].trace.escalation_reasons

        (without_switch,) = runs["confidence_only"].trace.routing_history
        assert without_switch.route is ReviewRoute.TARGETED_REVIEW
        assert without_switch.fast_path_eligible
        assert without_switch.reviewers == (Reviewer.CONFIDENCE,)

        assert _verdicts(runs["fast"]) == _verdicts(runs["off"])

    def test_a_skipped_reviewer_still_contributes_what_its_code_raises(self, repo_root):
        """The same code-raised findings are adjudicated on every route."""
        off = _run(repo_root, "strong-label-thin-support-001", AdaptiveMode.OFF)
        enabled = _run(repo_root, "strong-label-thin-support-001", AdaptiveMode.ENABLED)

        def code_findings(result) -> set[tuple[str, str]]:
            return {
                (reviewer.reviewer, finding.finding_id)
                for record in result.trace.review_rounds
                if record.stage == "internal"
                for reviewer in record.reviewers
                for finding in reviewer.findings
                if finding.origin == "deterministic"
            }

        assert code_findings(enabled) == code_findings(off)

    def test_nothing_admitted_skips_the_reviewers_and_keeps_the_synthesizer(self, repo_root):
        off = _run(repo_root, "candidate-sanitization-001", AdaptiveMode.OFF)
        enabled = _run(repo_root, "candidate-sanitization-001", AdaptiveMode.ENABLED)
        (plan,) = enabled.trace.routing_history
        assert plan.route is ReviewRoute.REQUEST_PHOTOS
        assert not [node for node in _model_calls(enabled) if node.endswith("_reviewer")]
        executed = enabled.trace.executed_nodes
        assert "review_synthesizer" in executed
        assert not set(executed) & {node.value for node in REVIEW_FANOUT}
        assert _verdicts(enabled) == _verdicts(off)

    def test_a_targeted_reviewer_is_told_why_it_was_called(self, repo_root, node_context):
        """The question reaches the projection of the reviewer it names, and no other."""
        state = _run(repo_root, "light-trunk-birch-001", AdaptiveMode.ENABLED).state
        confusion = build_review_projection(NodeName.CONFUSION_REVIEWER, state, node_context)
        confidence = build_review_projection(NodeName.CONFIDENCE_REVIEWER, state, node_context)
        assert [q.code for q in confusion.questions] == ["declared_look_alike_open"]
        assert [q.code for q in confidence.questions] == ["claim_strength"]

    def test_a_recorded_plan_asks_no_question(self, repo_root, node_context):
        """In shadow mode every reviewer sees exactly the context it had before."""
        state = _run(repo_root, "light-trunk-birch-001", AdaptiveMode.SHADOW).state
        assert state.routing_assessment is not None
        assert state.routing_assessment.questions
        for node in REVIEW_FANOUT:
            assert build_review_projection(node, state, node_context).questions == ()
