"""Run-trace audit records."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import dendro_inspector.observability.trace as trace_module
from dendro_inspector.evaluation.runner import load_cases
from dendro_inspector.graph.state import GraphState
from dendro_inspector.nodes import arbiter_synthesizer, review_synthesizer
from dendro_inspector.nodes.review_synthesizer import adjudicate
from dendro_inspector.observability.events import (
    MAX_REVIEW_FINDINGS,
    MAX_REVIEW_ROUNDS,
    TRACE_SCHEMA_VERSION,
    ReviewRoundRecord,
    RunTrace,
)
from dendro_inspector.observability.trace import TraceRecorder
from dendro_inspector.runner import CaseRunResult, run_case
from dendro_inspector.schemas.decisions import (
    AuthorityCheckStatus,
    AuthorityCheckTrace,
    AuthorityOutcome,
    DecisionStatus,
    FinalDecision,
)
from dendro_inspector.schemas.evidence import (
    AttachmentStatus,
    EvidencePacket,
    Observation,
    ObservationSource,
    Subject,
    SubjectKind,
)
from dendro_inspector.schemas.reviews import ReviewFinding, ReviewResult
from dendro_inspector.schemas.taxon import Confidence, Resolution
from tests.conftest import make_config


def test_component_projection_provenance_survives_in_trace():
    packet = EvidencePacket(
        subjects=(
            Subject(subject_id="tree", kind=SubjectKind.STANDING_TREE),
            Subject(
                subject_id="branch",
                kind=SubjectKind.BRANCH,
                parent_subject_id="tree",
            ),
        ),
        observations=(
            Observation(
                observation_id="leaf",
                feature="leaf.shape",
                value="simple_lobed",
                subject_id="branch",
                source=ObservationSource.IMAGE,
                image_id="img-1",
                attachment=AttachmentStatus.CONFIRMED_ATTACHED,
            ),
        ),
    ).collapse_subject_components()
    recorder = TraceRecorder("component-trace")

    recorder.record_component_projections(packet)
    trace = recorder.build()

    assert trace.component_projections[0].identity_subject_id == "tree"
    assert trace.component_projections[0].source_component_id == "branch"
    assert trace.component_projections[0].observation_ids == ("leaf",)


def test_code_revision_and_dirty_state_are_frozen_into_trace(monkeypatch):
    commit = "a" * 40
    monkeypatch.setattr(trace_module, "_discover_code_revision", lambda _root: (commit, False))

    trace = TraceRecorder("revision-trace").build()

    assert trace.code_commit_sha == commit
    assert trace.code_dirty is False


def test_trace_records_attachment_counterfactual_and_correction_marginal_utility():
    """Authority is recorded per subject, because a union of subjects describes no world.

    The flattened form this replaced took its critical evidence ids from every sensitive
    decision and its counterfactual taxon from the first one. On a two-subject case that
    produced a record in which the evidence belonged to one tree and the alternate verdict to
    another — internally consistent, and about nothing.
    """
    recorder = TraceRecorder("authority-trace")
    recorder.record_retry()
    before = FinalDecision(
        subject_id="tree",
        selected_taxon="tilia",
        selected_taxon_display_name="Tilia",
        resolution=Resolution.GENUS,
        confidence=Confidence.MEDIUM,
        status=DecisionStatus.PROBABLE,
    )
    after = FinalDecision(subject_id="tree")
    checks = (
        AuthorityCheckTrace(
            subject_id="tree",
            status=AuthorityCheckStatus.SENSITIVE,
            critical_evidence_ids=("leaf",),
            risk_evidence_ids=("leaf",),
            policy_applied=True,
            actual_outcome=AuthorityOutcome(
                status=DecisionStatus.INSUFFICIENT_EVIDENCE,
                resolution=Resolution.UNKNOWN,
                confidence=Confidence.LOW,
            ),
            counterfactual_outcome=AuthorityOutcome(
                status=DecisionStatus.PROBABLE,
                taxon="tilia",
                resolution=Resolution.GENUS,
                confidence=Confidence.MEDIUM,
            ),
            counterfactual_attachment=AttachmentStatus.CONFIRMED_ATTACHED,
        ),
        AuthorityCheckTrace(
            subject_id="neighbour",
            status=AuthorityCheckStatus.NOT_TESTABLE,
        ),
    )

    trace = recorder.build(
        pre_correction_decisions=(before,),
        final_decisions=(after,),
        authority_checks=checks,
    )

    assert trace.graph_retry_count == 1
    assert trace.correction_changed_outcome is True
    assert trace.correction_changed_status is True
    assert trace.correction_changed_taxon is True
    assert trace.correction_changed_resolution is True
    assert trace.correction_changed_confidence is True
    assert trace.evidence_authority_sensitive is True
    assert tuple(check.subject_id for check in trace.authority_checks) == ("tree", "neighbour")
    sensitive = trace.authority_checks[0]
    assert sensitive.risk_evidence_ids == ("leaf",)
    assert sensitive.policy_applied is True
    assert sensitive.counterfactual_outcome is not None
    assert sensitive.counterfactual_outcome.taxon == "tilia"
    # A subject with nothing to test is not reported as a check that came back clean.
    assert trace.authority_checks[1].status is AuthorityCheckStatus.NOT_TESTABLE


def test_trace_reports_a_retry_that_did_not_change_the_outcome():
    recorder = TraceRecorder("no-change-trace")
    recorder.record_retry()
    decision = FinalDecision(subject_id="tree")

    trace = recorder.build(
        pre_correction_decisions=(decision,),
        final_decisions=(decision,),
    )

    assert trace.graph_retry_count == 1
    assert trace.correction_changed_outcome is False
    assert trace.correction_changed_status is False
    assert trace.correction_changed_taxon is False
    assert trace.correction_changed_resolution is False
    assert trace.correction_changed_confidence is False


def test_trace_records_what_the_arbiter_changed():
    recorder = TraceRecorder("arbiter-change-trace")
    recorder.record_arbiter_used()
    provisional = FinalDecision(
        subject_id="tree",
        selected_taxon="pinus",
        selected_taxon_display_name="Pinus",
        resolution=Resolution.GENUS,
        confidence=Confidence.HIGH,
        status=DecisionStatus.IDENTIFIED,
    )
    final = FinalDecision(
        subject_id="tree",
        selected_taxon="picea",
        selected_taxon_display_name="Picea",
        resolution=Resolution.FAMILY,
        confidence=Confidence.LOW,
        status=DecisionStatus.PROBABLE,
    )

    trace = recorder.build(
        provisional_decisions=(provisional,),
        final_decisions=(final,),
    )

    assert trace.provisional_decisions == (provisional,)
    assert trace.arbiter_changed_status is True
    assert trace.arbiter_changed_taxon is True
    assert trace.arbiter_changed_resolution is True
    assert trace.arbiter_changed_confidence is True


def test_trace_records_an_arbiter_pass_as_no_change():
    recorder = TraceRecorder("arbiter-pass-trace")
    recorder.record_arbiter_used()
    decision = FinalDecision(subject_id="tree")

    trace = recorder.build(
        provisional_decisions=(decision,),
        final_decisions=(decision,),
    )

    assert trace.arbiter_changed_status is False
    assert trace.arbiter_changed_taxon is False
    assert trace.arbiter_changed_resolution is False
    assert trace.arbiter_changed_confidence is False


def test_trace_leaves_arbiter_change_fields_unset_when_no_arbiter_ran():
    decision = FinalDecision(subject_id="tree")

    trace = TraceRecorder("no-arbiter-trace").build(
        provisional_decisions=(decision,),
        final_decisions=(decision,),
    )

    assert trace.provisional_decisions == (decision,)
    assert trace.arbiter_changed_status is None
    assert trace.arbiter_changed_taxon is None
    assert trace.arbiter_changed_resolution is None
    assert trace.arbiter_changed_confidence is None
    assert trace.arbiter_comparison is None


def _named(subject_id: str, taxon: str = "pinus") -> FinalDecision:
    return FinalDecision(
        subject_id=subject_id,
        selected_taxon=taxon,
        selected_taxon_display_name=taxon.capitalize(),
        resolution=Resolution.GENUS,
        confidence=Confidence.MEDIUM,
        status=DecisionStatus.PROBABLE,
    )


def test_a_subject_without_a_provisional_verdict_is_not_an_arbiter_change():
    """Provisional verdicts exist only for subjects that had candidates to rank.

    A second subject with nothing to rank therefore has a final verdict and no provisional
    one. Comparing the union of both sides reported that as the arbiter changing status,
    taxon, resolution and confidence, on runs where arbitration moved nothing at all.
    """
    recorder = TraceRecorder("arbiter-one-sided-trace")
    recorder.record_arbiter_used()
    named = _named("tree")

    trace = recorder.build(
        provisional_decisions=(named,),
        final_decisions=(named, FinalDecision(subject_id="background")),
    )

    assert trace.arbiter_changed_status is False
    assert trace.arbiter_changed_taxon is False
    assert trace.arbiter_changed_resolution is False
    assert trace.arbiter_changed_confidence is False
    comparison = trace.arbiter_comparison
    assert comparison is not None
    assert comparison.compared_subject_ids == ("tree",)
    assert comparison.changed_subject_ids == ()
    assert comparison.only_before_subject_ids == ()
    assert comparison.only_after_subject_ids == ("background",)


def test_the_arbiter_comparison_names_the_subjects_that_moved():
    recorder = TraceRecorder("arbiter-moved-trace")
    recorder.record_arbiter_used()

    trace = recorder.build(
        provisional_decisions=(_named("tree"), _named("log")),
        final_decisions=(_named("tree", "picea"), _named("log")),
    )

    assert trace.arbiter_changed_taxon is True
    assert trace.arbiter_changed_resolution is False
    comparison = trace.arbiter_comparison
    assert comparison is not None
    assert comparison.compared_subject_ids == ("tree", "log")
    assert comparison.changed_subject_ids == ("tree",)


def test_change_flags_stay_unset_when_no_subject_has_a_verdict_on_both_sides():
    """A re-extraction may rename every subject; that is not evidence of a changed verdict."""
    recorder = TraceRecorder("renamed-subjects-trace")
    recorder.record_retry()

    trace = recorder.build(
        pre_correction_decisions=(_named("tree"),),
        final_decisions=(_named("standing_tree"),),
    )

    assert trace.correction_changed_outcome is None
    assert trace.correction_changed_taxon is None
    comparison = trace.correction_comparison
    assert comparison is not None
    assert comparison.compared_subject_ids == ()
    assert comparison.only_before_subject_ids == ("tree",)
    assert comparison.only_after_subject_ids == ("standing_tree",)


def test_every_final_decision_has_a_derivation(simple_case, run_scenario):
    """A disputed verdict must be auditable without re-running the engine."""
    result = run_scenario(simple_case, "primary-pass")

    derivations = result.trace.decision_derivations

    assert len(derivations) == len(result.state.decisions)
    assert {item.subject_id for item in derivations} == {
        decision.subject_id for decision in result.state.decisions
    }


def test_knowledge_coverage_reaches_the_trace(simple_case, run_scenario):
    """A suite scorer must be able to tell a model miss from a knowledge-base gap.

    Before this field existed the measurement was made, classified and written to a log
    line, then dropped: the run artifact a scorer reads carried no trace of it, so every
    coverage gap looked exactly like a model that saw nothing useful.
    """
    result = run_scenario(simple_case, "primary-pass")

    coverage = result.trace.knowledge_coverage

    assert coverage is not None, "a run that measured coverage must record it"
    assert coverage.observations_total == len(result.state.evidence.observations)
    assert coverage.unmatchable_total == len(result.state.quality.unmatchable_evidence_ids)


REVIEWERS = ("botanical_reviewer", "confusion_reviewer", "confidence_reviewer")


def _fanout_recorder() -> TraceRecorder:
    recorder = TraceRecorder("critical-path")
    recorder.record_node("planner", duration_ms=100.0)
    recorder.record_node("botanical_reviewer", duration_ms=200.0)
    recorder.record_node("confusion_reviewer", duration_ms=500.0)
    recorder.record_node("confidence_reviewer", duration_ms=300.0)
    recorder.record_node("final_decision", duration_ms=10.0)
    return recorder


def test_critical_path_charges_a_fan_out_once_at_its_slowest_member():
    """Concurrency the executor really has must not be reported as time it cost.

    Summing the three reviewers would say 1,110 ms where the run waited 610. The gap is the
    whole point of running them together, and a latency budget built on the wrong number
    would go looking for savings that were never there.
    """
    trace = _fanout_recorder().build(concurrent_nodes=REVIEWERS)

    assert trace.critical_path_ms == pytest.approx(610.0)


def test_without_being_told_what_overlaps_every_node_is_serial():
    """Silence is read as "no concurrency", which over-reports rather than under-reports."""
    trace = _fanout_recorder().build()

    assert trace.critical_path_ms == pytest.approx(1110.0)


def test_a_second_fan_out_round_is_charged_again():
    """A retry really did run the reviewers twice."""
    recorder = TraceRecorder("retry-critical-path")
    for duration in (200.0, 500.0, 300.0):
        recorder.record_node(REVIEWERS[0], duration_ms=duration)
    recorder.record_node("correction_worker", duration_ms=1.0)
    for duration in (100.0, 700.0, 100.0):
        recorder.record_node(REVIEWERS[1], duration_ms=duration)

    trace = recorder.build(concurrent_nodes=REVIEWERS)

    assert trace.critical_path_ms == pytest.approx(500.0 + 1.0 + 700.0)


def test_a_run_with_no_events_reports_no_critical_path():
    assert TraceRecorder("empty").build().critical_path_ms is None


ROUND_PACKET = EvidencePacket(
    subjects=(Subject(subject_id="tree", kind=SubjectKind.STANDING_TREE),),
    observations=(
        Observation(
            observation_id="cone",
            feature="cones.scale_shape",
            value="woody_umbo",
            subject_id="tree",
            source=ObservationSource.IMAGE,
            image_id="img-1",
            attachment=AttachmentStatus.CONFIRMED_ATTACHED,
        ),
    ),
)


def _finding(**updates) -> ReviewFinding:
    return ReviewFinding.model_validate(
        {
            "finding_id": "f1",
            "category": "confidence_miscalibration",
            "severity": "major",
            "subject_id": "tree",
            "evidence_ids": ("cone",),
            "summary": "This observation warrants a more conservative confidence.",
            "required_action": "lower_confidence",
            "impact": "confidence_change",
            **updates,
        }
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


def _recorded_round(knowledge, *reviews: ReviewResult) -> ReviewRoundRecord:
    recorder = TraceRecorder("review-round")
    recorder.record_evidence(ROUND_PACKET)
    recorder.record_review_round(
        stage="internal",
        round_index=1,
        results=reviews,
        synthesis=adjudicate(reviews, evidence=ROUND_PACKET, knowledge=knowledge),
    )
    (recorded,) = recorder.build().review_rounds
    return recorded


class TestReviewRounds:
    """What each reviewer returned, and what adjudication made of it, survives the run."""

    def test_each_finding_is_kept_with_its_adjudicated_status_and_reason(self, knowledge):
        recorded = _recorded_round(
            knowledge,
            _review("botanical", _finding()),
            _review("confusion", _finding(evidence_ids=("no-such-observation",))),
        )

        assert (recorded.stage, recorded.round, recorded.evidence_revision) == ("internal", 1, 1)
        botanical, confusion = recorded.reviewers
        assert [
            (f.finding_ref, f.finding_id, f.status, f.reason_code, f.evidence_ids)
            for f in botanical.findings
        ] == [
            ("internal-1/1-botanical/1", "f1", "accepted", "references_visible_evidence", ("cone",))
        ]
        # A rejected finding is the half of a review the graph state never kept.
        assert [(f.finding_ref, f.status, f.reason_code) for f in confusion.findings] == [
            ("internal-1/2-confusion/1", "rejected", "evidence_id_unknown")
        ]

    def test_a_recommendation_is_marked_admitted_refused_or_absent(self, knowledge):
        recorded = _recorded_round(
            knowledge,
            _review("botanical", _finding()),
            _review(
                "confusion",
                _finding(evidence_ids=("no-such-observation",)),
                recommended_resolution="family",
            ),
            _review("confidence", recommended_confidence="medium"),
        )

        botanical, confusion, confidence = recorded.reviewers
        assert botanical.recommendation_admitted is None
        # Its only finding was rejected, and a rejected finding cannot carry a level in.
        assert confusion.recommended_resolution is Resolution.FAMILY
        assert confusion.recommendation_admitted is False
        assert confidence.recommendation_admitted is True
        assert [
            (r.reviewer, r.subject_id, r.resolution, r.confidence, r.finding_ref)
            for r in recorded.recommendations
        ] == [("confidence", "tree", None, Confidence.MEDIUM, None)]

    def test_a_round_a_retry_discarded_is_still_in_the_trace(self, simple_case, node_context):
        """The correction worker clears the reviews, so the round that asked was unrecoverable."""
        state = GraphState(case=simple_case, evidence=ROUND_PACKET)
        asking = _review(
            "confidence",
            _finding(required_action="re_extract_evidence", impact="observation_change"),
        )

        asyncio.run(review_synthesizer.run(state.evolve(reviews=(asking,)), node_context))
        retried = state.evolve(retries=1, reviews=(_review("confidence"),))
        asyncio.run(review_synthesizer.run(retried, node_context))
        asyncio.run(
            arbiter_synthesizer.run(
                retried.evolve(arbiter_reviews=(_review("arbiter"),)), node_context
            )
        )

        rounds = node_context.recorder.build().review_rounds
        assert [(r.stage, r.round, r.retry_required) for r in rounds] == [
            ("internal", 1, True),
            ("internal", 2, False),
            ("arbiter", 1, False),
        ]
        (finding,) = rounds[0].reviewers[0].findings
        assert finding.required_action == "re_extract_evidence"
        assert finding.finding_ref == "internal-1/1-confidence/1"

    def test_findings_past_the_record_bound_are_counted(self, knowledge):
        findings = tuple(
            _finding(finding_id=f"f{index}", evidence_ids=("no-such-observation",))
            for index in range(MAX_REVIEW_FINDINGS + 3)
        )

        (reviewer,) = _recorded_round(knowledge, _review("confusion", *findings)).reviewers

        assert len(reviewer.findings) == MAX_REVIEW_FINDINGS
        assert reviewer.findings_omitted == 3

    def test_rounds_past_the_record_bound_do_not_break_the_trace(self, knowledge):
        """The retry budget keeps a run far below the bound; the trace must build regardless."""
        recorder = TraceRecorder("many-rounds")
        synthesis = adjudicate((), evidence=ROUND_PACKET, knowledge=knowledge)

        for index in range(1, MAX_REVIEW_ROUNDS + 3):
            recorder.record_review_round(
                stage="internal", round_index=index, results=(), synthesis=synthesis
            )

        assert len(recorder.build().review_rounds) == MAX_REVIEW_ROUNDS


PUBLIC_CASES = load_cases(Path(__file__).resolve().parents[2] / "evals" / "public")


def _run_public(case_id: str, root: Path) -> CaseRunResult:
    (case,) = [case for case in PUBLIC_CASES if case.case_id == case_id]
    return asyncio.run(run_case(case.input, config=make_config(case.scenario), root=root))


def _cited_references(trace: RunTrace) -> set[str]:
    """Every finding reference a verdict or a withdrawn claim points at."""
    cited: set[str | None] = set()
    for derivation in trace.decision_derivations:
        for bound in derivation.resolution_bounds:
            cited.update(source.finding_ref for source in bound.sources)
        for step in derivation.confidence_steps:
            cited.add(step.finding_ref)
            cited.update(source.finding_ref for source in step.sources)
        cited.update(finding.finding_ref for finding in derivation.resolution_action_findings)
        if derivation.rerank_finding is not None:
            cited.add(derivation.rerank_finding.finding_ref)
    for abstention in trace.abstentions:
        cited.update(finding.finding_ref for finding in abstention.findings)
    return {reference for reference in cited if reference is not None}


def _recorded_references(trace: RunTrace) -> list[str]:
    return [
        finding.finding_ref
        for recorded in trace.review_rounds
        for reviewer in recorded.reviewers
        for finding in reviewer.findings
        if finding.finding_ref is not None
    ]


class TestRecordedRun:
    """The same records, read from complete runs of the graph."""

    def test_a_trace_states_its_own_shape_version(self):
        assert TraceRecorder("versioned").build().trace_schema_version == TRACE_SCHEMA_VERSION

    def test_the_packet_context_reaches_the_trace(self, simple_case, run_scenario):
        """Subject kinds, inferences and limitations are read by the decision rules."""
        result = run_scenario(simple_case, "primary-pass")

        context = result.trace.evidence_context
        packet = result.state.evidence

        assert context is not None
        assert context.revision == 1
        assert [(s.subject_id, s.kind) for s in context.subjects] == [
            (s.subject_id, s.kind.value) for s in packet.subjects
        ]
        assert context.inferences
        assert [(i.inference_id, i.derived_from) for i in context.inferences] == [
            (i.inference_id, i.derived_from) for i in packet.inferences
        ]
        assert context.context_limitations == packet.context_limitations
        assert [limitation.image_id for limitation in context.image_limitations] == [
            limitation.image_id for limitation in packet.image_limitations
        ]
        assert all(v.evidence_revision == 1 for v in result.trace.candidate_validations)

    def test_the_plan_reaches_the_trace(self, simple_case, run_scenario):
        result = run_scenario(simple_case, "primary-pass")

        plan = result.trace.plan

        assert plan is not None
        assert plan.target_features
        assert plan.target_features == result.state.plan.target_features
        assert plan.bark_only_input is result.state.plan.bark_only_input

    def test_an_arbitrated_run_records_both_passes(self, repo_root):
        trace = _run_public("arbiter-ranking-001", repo_root).trace

        assert [(r.stage, r.round) for r in trace.review_rounds] == [
            ("internal", 1),
            ("arbiter", 1),
        ]
        (derivation,) = trace.decision_derivations
        assert derivation.rerank_source == "arbiter"
        assert derivation.rerank_finding is not None
        assert derivation.rerank_finding.reviewer == "arbiter"
        assert derivation.rerank_finding.finding_ref in {
            rerank.finding_ref for rerank in trace.review_rounds[1].reranks
        }

    def test_a_suppressed_arbitration_says_what_suppressed_it(self, repo_root):
        trace = _run_public("abstention-visible-001", repo_root).trace

        assert trace.escalation_reasons
        assert not trace.arbiter_used
        assert trace.escalation_suppressed_by == ("already_abstaining",)
        assert set(trace.escalation_hard_reasons) <= set(trace.escalation_reasons)

    def test_a_withdrawn_claim_names_its_cause_and_its_finding(self, repo_root):
        trace = _run_public("abstention-visible-001", repo_root).trace

        (abstention,) = trace.abstentions
        assert abstention.cause == "unresolvable_finding"
        (finding,) = abstention.findings
        assert finding.finding_ref in _recorded_references(trace)

    def test_a_trace_reads_back_from_json_unchanged(self, repo_root):
        """Stored records are JSON; a record that does not survive the trip cannot be replayed."""
        trace = _run_public("arbiter-ranking-001", repo_root).trace

        assert RunTrace.model_validate(trace.model_dump(mode="json")) == trace

    @pytest.mark.parametrize("case", PUBLIC_CASES, ids=lambda case: case.case_id)
    def test_every_finding_a_verdict_cites_is_in_the_recorded_rounds(self, case, repo_root):
        """A reference that resolved to nothing would make the attribution unreadable."""
        trace = asyncio.run(
            run_case(case.input, config=make_config(case.scenario), root=repo_root)
        ).trace

        recorded = _recorded_references(trace)

        assert len(recorded) == len(set(recorded)), "a finding reference must be unique in a run"
        assert _cited_references(trace) <= set(recorded)
        assert all(
            step.finding_ref is not None
            for derivation in trace.decision_derivations
            for step in derivation.confidence_steps
            if step.finding_id is not None
        )
