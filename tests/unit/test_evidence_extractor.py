"""Authoritative facts reconciled after model-produced extraction."""

from __future__ import annotations

from dendro_inspector.graph.state import GraphState, GuardReport, InspectionPlan
from dendro_inspector.nodes.evidence_extractor import reconcile_packet
from dendro_inspector.schemas.evidence import EvidencePacket


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
