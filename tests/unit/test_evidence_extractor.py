"""Authoritative facts reconciled after model-produced extraction."""

from __future__ import annotations

from dendro_inspector.graph.state import GraphState, GuardReport, InspectionPlan
from dendro_inspector.nodes.evidence_extractor import reconcile_packet
from dendro_inspector.schemas.evidence import (
    AttachmentStatus,
    EvidencePacket,
    GeneratedEvidencePacket,
    GeneratedObservation,
    ObservationSource,
    Subject,
    SubjectKind,
)


def test_split_firewood_plan_forces_possible_multiple_taxa(simple_case):
    state = GraphState(case=simple_case, plan=InspectionPlan(split_firewood_input=True))

    packet = reconcile_packet(state, EvidencePacket(possible_multiple_taxa=False))

    assert packet.possible_multiple_taxa


def test_ordinary_log_plan_does_not_force_possible_multiple_taxa(simple_case):
    state = GraphState(case=simple_case, plan=InspectionPlan())

    packet = reconcile_packet(state, EvidencePacket(possible_multiple_taxa=False))

    assert not packet.possible_multiple_taxa


def test_guard_and_split_firewood_facts_compose(simple_case):
    state = GraphState(
        case=simple_case,
        guard=GuardReport(instruction_like_signals=("prompt_injection",)),
        plan=InspectionPlan(split_firewood_input=True),
    )

    packet = reconcile_packet(state, EvidencePacket())

    assert packet.instruction_like_content_detected
    assert packet.possible_multiple_taxa


def test_generated_component_is_canonicalized_before_graph_use():
    generated = GeneratedEvidencePacket(
        subjects=(
            Subject(subject_id="tree_1", kind=SubjectKind.STANDING_TREE),
            Subject(
                subject_id="shoot_1",
                kind=SubjectKind.BRANCH,
                parent_subject_id="tree_1",
            ),
        ),
        observations=(
            GeneratedObservation(
                observation_id="leaf_1",
                feature="leaf.shape",
                value="simple_lobed",
                subject_id="shoot_1",
                source=ObservationSource.IMAGE,
                image_id="img-1",
                attachment=AttachmentStatus.CONFIRMED_ATTACHED,
            ),
        ),
    )

    packet = generated.to_evidence_packet()

    assert [subject.subject_id for subject in packet.subjects] == ["tree_1"]
    assert packet.observations[0].subject_id == "tree_1"
