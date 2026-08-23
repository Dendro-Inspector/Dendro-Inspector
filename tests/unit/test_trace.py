"""Run-trace audit records."""

from __future__ import annotations

from dendro_inspector.observability.trace import TraceRecorder
from dendro_inspector.schemas.evidence import (
    AttachmentStatus,
    EvidencePacket,
    Observation,
    ObservationSource,
    Subject,
    SubjectKind,
)


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
