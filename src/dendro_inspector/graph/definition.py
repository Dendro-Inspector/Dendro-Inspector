"""The graph declaration.

This module is the single source of truth for the graph's shape. The Mermaid diagram in
``docs/agent-graph.md`` and the output of ``dendro graph`` are both *rendered from
here*, so the picture cannot drift from the executable topology — a test asserts that
every routing target is a declared edge.

``input``, ``output`` and ``internal_gate`` are rendering pseudo-nodes: the first two mark
the boundary, and ``internal_gate`` is a pure routing decision over the review synthesis
rather than a node with side effects.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class NodeName(StrEnum):
    INPUT = "input"
    INPUT_GUARD = "input_guard"
    PLANNER = "planner"
    EVIDENCE_EXTRACTOR = "evidence_extractor"
    EVIDENCE_QUALITY = "evidence_quality"
    PHOTO_PLANNER = "photo_planner"
    CANDIDATE_GENERATOR = "candidate_generator"
    BOTANICAL_REVIEWER = "botanical_reviewer"
    CONFUSION_REVIEWER = "confusion_reviewer"
    CONFIDENCE_REVIEWER = "confidence_reviewer"
    REVIEW_SYNTHESIZER = "review_synthesizer"
    INTERNAL_GATE = "internal_gate"
    CORRECTION_WORKER = "correction_worker"
    ABSTAIN = "abstain"
    ESCALATION_GATE = "escalation_gate"
    ARBITER = "arbiter"
    ARBITER_SYNTHESIZER = "arbiter_synthesizer"
    FINAL_DECISION = "final_decision"
    RESPONSE_COMPOSER = "response_composer"
    TONE_LAYER = "tone_layer"
    OUTPUT = "output"


class NodeKind(StrEnum):
    BOUNDARY = "boundary"
    EXECUTABLE = "executable"
    DECISION = "decision"
    ROUTING_DECISION = "routing_decision"


NODE_KINDS: dict[NodeName, NodeKind] = {
    NodeName.INPUT: NodeKind.BOUNDARY,
    NodeName.OUTPUT: NodeKind.BOUNDARY,
    NodeName.INTERNAL_GATE: NodeKind.ROUTING_DECISION,
    NodeName.EVIDENCE_QUALITY: NodeKind.DECISION,
    NodeName.ESCALATION_GATE: NodeKind.DECISION,
    NodeName.INPUT_GUARD: NodeKind.EXECUTABLE,
    NodeName.PLANNER: NodeKind.EXECUTABLE,
    NodeName.EVIDENCE_EXTRACTOR: NodeKind.EXECUTABLE,
    NodeName.PHOTO_PLANNER: NodeKind.EXECUTABLE,
    NodeName.CANDIDATE_GENERATOR: NodeKind.EXECUTABLE,
    NodeName.BOTANICAL_REVIEWER: NodeKind.EXECUTABLE,
    NodeName.CONFUSION_REVIEWER: NodeKind.EXECUTABLE,
    NodeName.CONFIDENCE_REVIEWER: NodeKind.EXECUTABLE,
    NodeName.REVIEW_SYNTHESIZER: NodeKind.EXECUTABLE,
    NodeName.CORRECTION_WORKER: NodeKind.EXECUTABLE,
    NodeName.ABSTAIN: NodeKind.EXECUTABLE,
    NodeName.ARBITER: NodeKind.EXECUTABLE,
    NodeName.ARBITER_SYNTHESIZER: NodeKind.EXECUTABLE,
    NodeName.FINAL_DECISION: NodeKind.EXECUTABLE,
    NodeName.RESPONSE_COMPOSER: NodeKind.EXECUTABLE,
    NodeName.TONE_LAYER: NodeKind.EXECUTABLE,
}

DISPLAY_LABELS: dict[NodeName, str] = {
    NodeName.INPUT: "Input: images plus optional context",
    NodeName.INPUT_GUARD: "Input guard",
    NodeName.PLANNER: "Planner",
    NodeName.EVIDENCE_EXTRACTOR: "Evidence extractor",
    NodeName.EVIDENCE_QUALITY: "Evidence quality gate",
    NodeName.PHOTO_PLANNER: "Additional photo planner",
    NodeName.CANDIDATE_GENERATOR: "Candidate generator",
    NodeName.BOTANICAL_REVIEWER: "Botanical reviewer",
    NodeName.CONFUSION_REVIEWER: "Confusion reviewer",
    NodeName.CONFIDENCE_REVIEWER: "Confidence reviewer",
    NodeName.REVIEW_SYNTHESIZER: "Review synthesizer",
    NodeName.INTERNAL_GATE: "Internal review passes?",
    NodeName.CORRECTION_WORKER: "Correction worker",
    NodeName.ABSTAIN: "Lower resolution or abstain",
    NodeName.ESCALATION_GATE: "Arbiter required?",
    NodeName.ARBITER: "Independent arbiter review",
    NodeName.ARBITER_SYNTHESIZER: "Arbiter synthesis",
    NodeName.FINAL_DECISION: "Final decision engine",
    NodeName.RESPONSE_COMPOSER: "Response composer",
    NodeName.TONE_LAYER: "Presentation layer",
    NodeName.OUTPUT: "Final structured and human-readable output",
}


@dataclass(frozen=True, slots=True)
class Edge:
    source: NodeName
    target: NodeName
    label: str | None = None


GRAPH_EDGES: tuple[Edge, ...] = (
    Edge(NodeName.INPUT, NodeName.INPUT_GUARD),
    Edge(NodeName.INPUT_GUARD, NodeName.PLANNER),
    Edge(NodeName.PLANNER, NodeName.EVIDENCE_EXTRACTOR),
    Edge(NodeName.EVIDENCE_EXTRACTOR, NodeName.EVIDENCE_QUALITY),
    Edge(NodeName.EVIDENCE_QUALITY, NodeName.PHOTO_PLANNER, "insufficient"),
    Edge(NodeName.PHOTO_PLANNER, NodeName.RESPONSE_COMPOSER),
    Edge(NodeName.EVIDENCE_QUALITY, NodeName.CANDIDATE_GENERATOR, "usable"),
    Edge(NodeName.CANDIDATE_GENERATOR, NodeName.BOTANICAL_REVIEWER),
    Edge(NodeName.CANDIDATE_GENERATOR, NodeName.CONFUSION_REVIEWER),
    Edge(NodeName.CANDIDATE_GENERATOR, NodeName.CONFIDENCE_REVIEWER),
    Edge(NodeName.BOTANICAL_REVIEWER, NodeName.REVIEW_SYNTHESIZER),
    Edge(NodeName.CONFUSION_REVIEWER, NodeName.REVIEW_SYNTHESIZER),
    Edge(NodeName.CONFIDENCE_REVIEWER, NodeName.REVIEW_SYNTHESIZER),
    Edge(NodeName.REVIEW_SYNTHESIZER, NodeName.INTERNAL_GATE),
    Edge(NodeName.INTERNAL_GATE, NodeName.CORRECTION_WORKER, "correctable failure"),
    Edge(NodeName.CORRECTION_WORKER, NodeName.EVIDENCE_EXTRACTOR),
    Edge(NodeName.INTERNAL_GATE, NodeName.ABSTAIN, "unresolvable"),
    Edge(NodeName.INTERNAL_GATE, NodeName.ESCALATION_GATE, "pass"),
    Edge(NodeName.ESCALATION_GATE, NodeName.FINAL_DECISION, "no"),
    Edge(NodeName.ESCALATION_GATE, NodeName.ARBITER, "yes"),
    Edge(NodeName.ARBITER, NodeName.ARBITER_SYNTHESIZER),
    Edge(NodeName.ARBITER_SYNTHESIZER, NodeName.FINAL_DECISION),
    Edge(NodeName.ABSTAIN, NodeName.FINAL_DECISION),
    Edge(NodeName.FINAL_DECISION, NodeName.RESPONSE_COMPOSER),
    Edge(NodeName.RESPONSE_COMPOSER, NodeName.TONE_LAYER),
    Edge(NodeName.TONE_LAYER, NodeName.OUTPUT),
)

#: Reviewers are independent and run concurrently as one logical step.
REVIEW_FANOUT: tuple[NodeName, ...] = (
    NodeName.BOTANICAL_REVIEWER,
    NodeName.CONFUSION_REVIEWER,
    NodeName.CONFIDENCE_REVIEWER,
)

ENTRY_NODE = NodeName.INPUT_GUARD
TERMINAL_NODE = NodeName.OUTPUT

#: Pseudo-nodes the executor never runs.
NON_EXECUTABLE: frozenset[NodeName] = frozenset(
    name
    for name, kind in NODE_KINDS.items()
    if kind in (NodeKind.BOUNDARY, NodeKind.ROUTING_DECISION)
)

EXECUTABLE_NODES: tuple[NodeName, ...] = tuple(
    name for name, kind in NODE_KINDS.items() if kind in (NodeKind.EXECUTABLE, NodeKind.DECISION)
)


def edges_from(source: NodeName) -> tuple[Edge, ...]:
    return tuple(edge for edge in GRAPH_EDGES if edge.source is source)


def reachable_targets(source: NodeName) -> frozenset[NodeName]:
    """Targets of ``source``, seeing through routing-decision pseudo-nodes."""
    targets: set[NodeName] = set()
    for edge in edges_from(source):
        if NODE_KINDS[edge.target] is NodeKind.ROUTING_DECISION:
            targets.update(reachable_targets(edge.target))
        else:
            targets.add(edge.target)
    return frozenset(targets)


def _shape(name: NodeName) -> tuple[str, str]:
    kind = NODE_KINDS[name]
    if kind in (NodeKind.DECISION, NodeKind.ROUTING_DECISION):
        return "{", "}"
    return "[", "]"


def render_mermaid() -> str:
    """Render the declared graph as a Mermaid flowchart."""
    lines = ["flowchart TD"]
    for name in NodeName:
        open_bracket, close_bracket = _shape(name)
        lines.append(f"    {name.value.upper()}{open_bracket}{DISPLAY_LABELS[name]}{close_bracket}")
    lines.append("")
    for edge in GRAPH_EDGES:
        arrow = f"-->|{edge.label}|" if edge.label else "-->"
        lines.append(f"    {edge.source.value.upper()} {arrow} {edge.target.value.upper()}")
    return "\n".join(lines)


def validate_definition() -> None:
    """Fail loudly if the declaration is internally inconsistent."""
    named = set(NodeName)
    if set(NODE_KINDS) != named:
        missing = sorted(node.value for node in named - set(NODE_KINDS))
        msg = f"NODE_KINDS is missing entries: {missing}"
        raise ValueError(msg)
    if set(DISPLAY_LABELS) != named:
        missing = sorted(node.value for node in named - set(DISPLAY_LABELS))
        msg = f"DISPLAY_LABELS is missing entries: {missing}"
        raise ValueError(msg)

    referenced = {edge.source for edge in GRAPH_EDGES} | {edge.target for edge in GRAPH_EDGES}
    orphans = sorted(node.value for node in named - referenced)
    if orphans:
        msg = f"nodes declared but not wired into GRAPH_EDGES: {orphans}"
        raise ValueError(msg)
