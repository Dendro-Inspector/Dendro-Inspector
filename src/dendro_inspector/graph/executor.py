"""A small, explicit graph executor.

Deliberately not a framework. It does four things: walk the routing function, run nodes
(concurrently where the graph declares a fan-out), record an event per node, and refuse
to run forever. Nodes know nothing about it — they are ``(state, ctx) -> state`` coroutines,
which is why they are testable on their own.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Protocol

from dendro_inspector.config import AppConfig
from dendro_inspector.graph.definition import (
    ENTRY_NODE,
    REVIEW_FANOUT,
    TERMINAL_NODE,
    NodeName,
)
from dendro_inspector.graph.projections import REVIEWER_NODES, build_review_projection
from dendro_inspector.graph.routing import next_step
from dendro_inspector.graph.state import GraphState
from dendro_inspector.knowledge.loader import KnowledgeBase
from dendro_inspector.observability.events import NodeStatus, RunTrace
from dendro_inspector.observability.logging import get_logger
from dendro_inspector.observability.trace import TraceRecorder
from dendro_inspector.prompts.library import PromptLibrary
from dendro_inspector.providers.registry import ProviderRegistry
from dendro_inspector.schemas.input import CaseInput
from dendro_inspector.schemas.review_context import ReviewProjection


class GraphExecutionError(RuntimeError):
    """Raised when the graph cannot continue safely."""


@dataclass(frozen=True, slots=True)
class NodeContext:
    """Everything a node is allowed to reach for. Passed in, never imported globally."""

    config: AppConfig
    providers: ProviderRegistry
    knowledge: KnowledgeBase
    prompts: PromptLibrary
    recorder: TraceRecorder
    review_projection: ReviewProjection | None = None


class NodeRunner(Protocol):
    """A graph node: typed state in, typed state out, no side channel."""

    async def __call__(self, state: GraphState, ctx: NodeContext) -> GraphState: ...


@dataclass(frozen=True, slots=True)
class GraphRunResult:
    state: GraphState
    trace: RunTrace


def _context_for_node(node: NodeName, state: GraphState, ctx: NodeContext) -> NodeContext:
    """Attach a bounded reviewer projection at the orchestration boundary."""
    if node not in REVIEWER_NODES:
        return ctx
    projection = build_review_projection(node, state, ctx)
    ctx.recorder.record_review_projection(node.value, projection)
    return replace(ctx, review_projection=projection)


async def _run_fanout(
    members: tuple[NodeName, ...],
    registry: Mapping[NodeName, NodeRunner],
    state: GraphState,
    ctx: NodeContext,
) -> GraphState:
    """Run independent reviewers concurrently and merge only what each of them added.

    Merging by "new reviews appended by this member" keeps the concurrency honest: a
    reviewer that tried to change anything else would have its change discarded here,
    which is the intended contract rather than a silent race.
    """
    baseline = len(state.reviews)

    async def _run(member: NodeName) -> tuple[NodeName, GraphState, float]:
        started = time.perf_counter()
        member_ctx = _context_for_node(member, state, ctx)
        produced = await registry[member](state, member_ctx)
        return member, produced, (time.perf_counter() - started) * 1000.0

    outcomes = await asyncio.gather(*(_run(member) for member in members))

    # Events are recorded after the gather so trace order follows the declared fan-out
    # order rather than whichever coroutine happened to finish first.
    merged = state.reviews
    for member, produced, duration_ms in outcomes:
        ctx.recorder.record_node(member.value, duration_ms=duration_ms)
        merged = merged + produced.reviews[baseline:]
    return state.evolve(reviews=merged)


async def run_graph(
    case: CaseInput,
    ctx: NodeContext,
    registry: Mapping[NodeName, NodeRunner],
) -> GraphRunResult:
    """Execute the graph for one case."""
    logger = get_logger("graph")
    state = GraphState(case=case)
    current = ENTRY_NODE
    steps = 0
    max_steps = ctx.config.graph.max_steps

    while current is not TERMINAL_NODE:
        if steps >= max_steps:
            msg = (
                f"graph exceeded max_steps={max_steps} at node {current.value!r}; "
                "this indicates a routing bug, not a slow model"
            )
            raise GraphExecutionError(msg)
        steps += 1

        if current in REVIEW_FANOUT:
            state = await _run_fanout(REVIEW_FANOUT, registry, state, ctx)
            current = next_step(REVIEW_FANOUT[0], state, ctx.config.graph)
            continue

        runner = registry.get(current)
        if runner is None:
            msg = f"no implementation registered for node {current.value!r}"
            raise GraphExecutionError(msg)

        started = time.perf_counter()
        try:
            node_ctx = _context_for_node(current, state, ctx)
            state = await runner(state, node_ctx)
        except Exception:
            ctx.recorder.record_node(current.value, status=NodeStatus.FAILED)
            logger.exception("node_failed", extra={"node": current.value, "case_id": case.case_id})
            raise
        ctx.recorder.record_node(
            current.value,
            duration_ms=(time.perf_counter() - started) * 1000.0,
        )
        current = next_step(current, state, ctx.config.graph)

    decision = state.decisions[0] if state.decisions else None
    trace = ctx.recorder.build(
        final_resolution=decision.resolution if decision else None,
        final_confidence=decision.confidence if decision else None,
        pre_correction_decisions=state.pre_correction_decisions,
        final_decisions=state.decisions,
        authority_checks=state.authority_checks,
    )
    logger.info(
        "graph_complete",
        extra={
            "case_id": case.case_id,
            "nodes": len(trace.events),
            "retries": trace.retries,
            "arbiter_used": trace.arbiter_used,
        },
    )
    return GraphRunResult(state=state, trace=trace)
