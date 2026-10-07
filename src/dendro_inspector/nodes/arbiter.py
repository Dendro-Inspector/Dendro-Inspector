"""Independent arbiter review.

The arbiter sees the same *material* as the primary model — images, user context, evidence
packet, candidates, the stored deterministic provisional verdict, and the relevant cards — and
nothing else. In particular it never receives the primary model's private reasoning,
which is guaranteed structurally rather than by policy: this system never stores hidden
chain-of-thought anywhere, so there is nothing to pass on.

It returns structured findings only. It cannot rewrite the answer directly; it can only
make claims that must survive the same admissibility rules the internal reviewers face.
"""

from __future__ import annotations

from dendro_inspector.config import Role
from dendro_inspector.graph.executor import NodeContext
from dendro_inspector.graph.state import GraphState
from dendro_inspector.nodes._support import review_call
from dendro_inspector.schemas.reviews import Reviewer

NODE = "arbiter"


async def run(state: GraphState, ctx: NodeContext) -> GraphState:
    result = await review_call(
        ctx,
        node=NODE,
        reviewer=Reviewer.ARBITER,
        role=Role.ARBITER,
    )
    ctx.recorder.record_arbiter_used()
    return state.evolve(arbiter_reviews=(*state.arbiter_reviews, result))
