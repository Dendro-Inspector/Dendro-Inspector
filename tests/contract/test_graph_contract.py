"""The declared graph, the executable graph and the rendered diagram must be one thing."""

from __future__ import annotations

import contextlib

import pytest

from dendro_inspector.config import GraphConfig
from dendro_inspector.graph.definition import (
    ENTRY_NODE,
    EXECUTABLE_NODES,
    GRAPH_EDGES,
    NON_EXECUTABLE,
    REVIEW_FANOUT,
    TERMINAL_NODE,
    NodeName,
    reachable_targets,
    render_mermaid,
    validate_definition,
)
from dendro_inspector.graph.routing import RoutingError, next_step
from dendro_inspector.graph.state import (
    EscalationDecision,
    EvidenceQualityReport,
    GraphState,
)
from dendro_inspector.nodes import build_registry
from dendro_inspector.schemas.input import CaseInput
from dendro_inspector.schemas.reviews import ReviewSynthesis

pytestmark = pytest.mark.contract


def _saturated_state() -> GraphState:
    """A state with every routing input populated, so no branch raises for lack of data."""
    return GraphState(
        case=CaseInput(case_id="c1", user_text="x"),
        quality=EvidenceQualityReport(sufficient=True, usable_subject_ids=("s",)),
        synthesis=ReviewSynthesis(),
        escalation=EscalationDecision(required=True),
    )


def test_definition_is_internally_consistent():
    validate_definition()


def test_every_executable_node_has_an_implementation():
    registry = build_registry()
    missing = [node.value for node in EXECUTABLE_NODES if node not in registry]
    assert missing == []


def test_no_implementation_exists_for_a_node_outside_the_graph():
    unknown = [node.value for node in build_registry() if node not in set(NodeName)]
    assert unknown == []


def test_pseudo_nodes_are_never_implemented():
    """`input`, `output` and `internal_gate` are rendering constructs, not steps."""
    registry = build_registry()
    assert not (NON_EXECUTABLE & set(registry))


@pytest.mark.parametrize("source", [node for node in NodeName if node not in NON_EXECUTABLE])
def test_every_routing_target_is_a_declared_edge(source):
    """The guarantee that the diagram cannot drift from the executor."""
    declared = reachable_targets(source)
    state = _saturated_state()
    config = GraphConfig()

    observed: set[NodeName] = set()
    for escalation_required in (True, False):
        for sufficient in (True, False):
            for retry_required, retries, unresolvable in (
                (False, 0, False),
                (True, 0, False),
                (True, 1, False),
                (False, 0, True),
            ):
                candidate = state.evolve(
                    quality=EvidenceQualityReport(
                        sufficient=sufficient, usable_subject_ids=("s",) if sufficient else ()
                    ),
                    synthesis=ReviewSynthesis(
                        retry_required=retry_required, unresolvable=unresolvable
                    ),
                    escalation=EscalationDecision(required=escalation_required),
                    retries=retries,
                )
                # A branch that cannot be reached from this state combination is not a
                # failure: the point is that whatever IS produced must be a declared edge.
                with contextlib.suppress(RoutingError):
                    observed.add(next_step(source, candidate, config))

    assert observed, f"{source.value} produced no routing outcome"
    assert observed <= declared, (
        f"{source.value} routes to {sorted(n.value for n in observed - declared)}, "
        f"which is not declared in GRAPH_EDGES"
    )


def test_entry_and_terminal_are_wired():
    sources = {edge.source for edge in GRAPH_EDGES}
    targets = {edge.target for edge in GRAPH_EDGES}
    assert ENTRY_NODE in targets
    assert TERMINAL_NODE in targets
    assert TERMINAL_NODE not in sources


def test_review_fanout_members_all_converge_on_the_synthesizer():
    for member in REVIEW_FANOUT:
        assert reachable_targets(member) == {NodeName.REVIEW_SYNTHESIZER}


def test_only_one_backward_edge_exists():
    """Termination rests on this: the correction worker is the sole loop."""
    order = list(NodeName)
    backward = [
        (edge.source.value, edge.target.value)
        for edge in GRAPH_EDGES
        if order.index(edge.target) < order.index(edge.source)
    ]
    assert backward == [("correction_worker", "evidence_extractor")]


class TestMermaidRendering:
    def test_every_node_appears_in_the_diagram(self):
        diagram = render_mermaid()
        for node in NodeName:
            assert node.value.upper() in diagram

    def test_every_edge_appears_in_the_diagram(self):
        diagram = render_mermaid()
        for edge in GRAPH_EDGES:
            assert f"{edge.source.value.upper()} " in diagram
            assert edge.target.value.upper() in diagram

    def test_decision_nodes_render_as_diamonds(self):
        diagram = render_mermaid()
        assert "EVIDENCE_QUALITY{" in diagram
        assert "ESCALATION_GATE{" in diagram
        assert "INTERNAL_GATE{" in diagram

    def test_diagram_is_a_flowchart(self):
        assert render_mermaid().startswith("flowchart TD")
