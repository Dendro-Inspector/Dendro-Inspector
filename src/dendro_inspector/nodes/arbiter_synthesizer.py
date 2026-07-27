"""Arbiter synthesis.

Runs the arbiter's findings through :func:`adjudicate` — the *same* function the internal
reviewers face. A second model does not get a lower bar because it is expensive or because
it disagreed confidently.

What the arbiter can change, through accepted findings: candidate ranking, confidence,
and taxonomic resolution. What it cannot do: hand back an answer directly.
"""

from __future__ import annotations

from dendro_inspector.graph.executor import NodeContext
from dendro_inspector.graph.state import GraphState
from dendro_inspector.nodes.review_synthesizer import adjudicate

NODE = "arbiter_synthesizer"


async def run(state: GraphState, ctx: NodeContext) -> GraphState:
    synthesis = adjudicate(
        state.arbiter_reviews,
        evidence=state.evidence,
        knowledge=ctx.knowledge,
    )
    return state.evolve(arbiter_synthesis=synthesis)
