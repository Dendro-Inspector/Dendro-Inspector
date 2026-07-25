"""Node registry.

The registry is built explicitly rather than by scanning the package. An import-time
side effect that silently picks up whatever happens to be on disk is how a graph acquires
a node nobody remembers adding.
"""

from __future__ import annotations

from collections.abc import Mapping

from evil_duck_dendro.graph.definition import EXECUTABLE_NODES, NodeName
from evil_duck_dendro.graph.executor import NodeRunner
from evil_duck_dendro.nodes import (
    abstain,
    arbiter,
    arbiter_synthesizer,
    botanical_reviewer,
    candidate_generator,
    confidence_reviewer,
    confusion_reviewer,
    correction_worker,
    escalation_gate,
    evidence_extractor,
    evidence_quality,
    final_decision,
    input_guard,
    photo_planner,
    planner,
    response_composer,
    review_synthesizer,
    tone_layer,
)


def build_registry() -> Mapping[NodeName, NodeRunner]:
    """Map every executable node name to its implementation."""
    registry: dict[NodeName, NodeRunner] = {
        NodeName.INPUT_GUARD: input_guard.run,
        NodeName.PLANNER: planner.run,
        NodeName.EVIDENCE_EXTRACTOR: evidence_extractor.run,
        NodeName.EVIDENCE_QUALITY: evidence_quality.run,
        NodeName.PHOTO_PLANNER: photo_planner.run,
        NodeName.CANDIDATE_GENERATOR: candidate_generator.run,
        NodeName.BOTANICAL_REVIEWER: botanical_reviewer.run,
        NodeName.CONFUSION_REVIEWER: confusion_reviewer.run,
        NodeName.CONFIDENCE_REVIEWER: confidence_reviewer.run,
        NodeName.REVIEW_SYNTHESIZER: review_synthesizer.run,
        NodeName.CORRECTION_WORKER: correction_worker.run,
        NodeName.ABSTAIN: abstain.run,
        NodeName.ESCALATION_GATE: escalation_gate.run,
        NodeName.ARBITER: arbiter.run,
        NodeName.ARBITER_SYNTHESIZER: arbiter_synthesizer.run,
        NodeName.FINAL_DECISION: final_decision.run,
        NodeName.RESPONSE_COMPOSER: response_composer.run,
        NodeName.TONE_LAYER: tone_layer.run,
    }
    missing = [node.value for node in EXECUTABLE_NODES if node not in registry]
    if missing:
        msg = f"executable nodes without an implementation: {missing}"
        raise RuntimeError(msg)
    return registry


__all__ = ["build_registry"]
