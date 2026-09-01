"""Run-trace audit records."""

from __future__ import annotations

import pytest

import dendro_inspector.observability.trace as trace_module
from dendro_inspector.observability.trace import TraceRecorder
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
from dendro_inspector.schemas.taxon import Confidence, Resolution


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


@pytest.mark.xfail(
    strict=True,
    reason=(
        "F7 (docs/specs/core-logic-hardening.md): the decision engine's docstring promises "
        "the derivation is inspectable in the trace, but nothing records which bound bound "
        "or which confidence steps applied."
    ),
)
def test_every_final_decision_has_a_derivation(simple_case, run_scenario):
    """A disputed verdict must be auditable without re-running the engine."""
    result = run_scenario(simple_case, "primary-pass")

    derivations = result.trace.decision_derivations

    assert len(derivations) == len(result.state.decisions)
    assert {item.subject_id for item in derivations} == {
        decision.subject_id for decision in result.state.decisions
    }
