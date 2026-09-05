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
from dendro_inspector.graph.state import GraphState, SubjectAbstention
from dendro_inspector.schemas.reviews import RequiredAction, ReviewSynthesis, Severity
from dendro_inspector.schemas.taxon import (
    Confidence,
    Resolution,
    lower_resolution,
    resolution_rank,
)

NODE = "abstain"


def degraded_synthesis(synthesis: ReviewSynthesis | None, leading: Resolution) -> ReviewSynthesis:
    """Legacy single-subject aggregate helper; new graph runs store subject bounds."""
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

    synthesis = state.synthesis
    blocking = tuple(
        finding
        for finding in (synthesis.accepted_findings if synthesis else ())
        if finding.required_action is RequiredAction.RE_EXTRACT_EVIDENCE
        or (
            finding.required_action is RequiredAction.ABSTAIN
            and finding.severity is Severity.CRITICAL
        )
    )
    # An unscoped finding affects the case. Otherwise only the named subjects lose their
    # claims; another tree's weaker bound is never folded into this subject's result.
    whole_case = not blocking or any(finding.subject_id is None for finding in blocking)
    affected = {finding.subject_id for finding in blocking}
    bounds = []
    for subject_id in state.subject_ids:
        if not whole_case and subject_id not in affected:
            continue
        candidates = state.candidates_for(subject_id)
        resolution = (
            decide_subject(state, ctx, candidates).resolution
            if candidates is not None
            else Resolution.UNKNOWN
        )
        bounds.append(
            SubjectAbstention(subject_id=subject_id, resolution=lower_resolution(resolution))
        )

    return state.evolve(
        abstained=True,
        abstention_bounds=tuple(bounds),
    )
