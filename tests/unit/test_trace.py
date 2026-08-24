"""Run-trace audit records."""

from __future__ import annotations

import dendro_inspector.observability.trace as trace_module
from dendro_inspector.observability.trace import TraceRecorder
from dendro_inspector.schemas.decisions import DecisionStatus, FinalDecision
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
    recorder = TraceRecorder("authority-trace")
    recorder.record_retry()
    before = FinalDecision(
        subject_id="tree",
        selected_taxon="tilia",
        selected_taxon_display_name="Tilia",
        resolution=Resolution.GENUS,
        confidence=Confidence.MEDIUM,
        status=DecisionStatus.PROBABLE,
        evidence_authority_sensitive=True,
        critical_evidence_ids=("leaf",),
        authority_policy_applied=True,
        counterfactual_status=DecisionStatus.INSUFFICIENT_EVIDENCE,
        counterfactual_resolution=Resolution.UNKNOWN,
        counterfactual_confidence=Confidence.LOW,
        counterfactual_attachment=AttachmentStatus.UNKNOWN,
    )
    after = FinalDecision(subject_id="tree")

    trace = recorder.build(
        pre_correction_decisions=(before,),
        final_decisions=(after,),
    )

    assert trace.graph_retry_count == 1
    assert trace.correction_changed_outcome is True
    assert trace.correction_changed_status is True
    assert trace.correction_changed_taxon is True
    assert trace.correction_changed_resolution is True
    assert trace.correction_changed_confidence is True
    assert trace.evidence_authority_sensitive is True
    assert trace.critical_evidence_ids == ("leaf",)
    assert trace.authority_policy_applied is True
    assert trace.counterfactual_status is DecisionStatus.INSUFFICIENT_EVIDENCE
    assert trace.counterfactual_attachment is AttachmentStatus.UNKNOWN


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
