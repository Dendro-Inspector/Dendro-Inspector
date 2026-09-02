"""Abstention node.

Reached when review found something a retry cannot fix, or when the retry budget is spent.
It does not erase the work — it lowers the claim to whatever level the evidence still
supports and marks the run as abstained, so the final decision engine and the response
composer both know they are presenting a deliberately weakened answer rather than a
confident one.

Returning "I do not know, and here is the photograph that would tell us" is a successful
outcome of this system, not a failure of it.
"""

from __future__ import annotations

from dendro_inspector.graph.executor import NodeContext
from dendro_inspector.graph.state import GraphState
from dendro_inspector.schemas.reviews import ReviewSynthesis
from dendro_inspector.schemas.taxon import (
    Confidence,
    Resolution,
    lower_resolution,
    resolution_rank,
)

NODE = "abstain"


def degraded_synthesis(synthesis: ReviewSynthesis | None, leading: Resolution) -> ReviewSynthesis:
    """Lower the recorded resolution one step below whatever was being claimed."""
    target = lower_resolution(leading)
    base = synthesis or ReviewSynthesis()
    existing = base.resolution_delta
    if existing is not None and resolution_rank(existing) < resolution_rank(target):
        target = existing
    return base.model_copy(
        update={
            "resolution_delta": target,
            "confidence_delta": Confidence.LOW,
        }
    )


async def run(state: GraphState, ctx: NodeContext) -> GraphState:
    from dendro_inspector.nodes.final_decision import decide_subject

    provisional = tuple(
        decide_subject(state, ctx, candidate_set) for candidate_set in state.candidate_sets
    )
    synthesis = state.synthesis
    if provisional:
        for decision in provisional:
            synthesis = degraded_synthesis(synthesis, decision.resolution)
    else:
        synthesis = degraded_synthesis(synthesis, Resolution.UNKNOWN)

    return state.evolve(
        abstained=True,
        synthesis=synthesis,
    )
