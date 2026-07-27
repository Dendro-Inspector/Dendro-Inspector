"""Correction worker.

The only node on a backward edge. It spends one unit of the retry budget, hands the
accepted corrections to the extractor, and clears the derived state from the failed pass so
the second attempt cannot quietly inherit the first attempt's conclusions.

The retry budget is checked in :mod:`dendro_inspector.graph.routing`, not here — routing
owns termination, this node owns the correction payload.
"""

from __future__ import annotations

from dendro_inspector.graph.executor import NodeContext
from dendro_inspector.graph.state import GraphState
from dendro_inspector.observability.logging import get_logger

NODE = "correction_worker"


async def run(state: GraphState, ctx: NodeContext) -> GraphState:
    synthesis = state.synthesis
    corrections = synthesis.required_corrections if synthesis else ()

    ctx.recorder.record_retry()
    get_logger(NODE).info(
        "retry_scheduled",
        extra={
            "case_id": state.case.case_id,
            "attempt": state.retries + 1,
            "corrections": [directive.action.value for directive in corrections],
        },
    )

    return state.evolve(
        retries=state.retries + 1,
        corrections=corrections,
        # Everything derived from the rejected extraction is dropped on purpose.
        candidate_sets=(),
        reviews=(),
        synthesis=None,
        quality=None,
    )
