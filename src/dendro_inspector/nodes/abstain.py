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
from dendro_inspector.nodes.review_synthesizer import is_grounded, requests_abstention
from dendro_inspector.schemas.reviews import (
    RequiredAction,
    ReviewFinding,
    ReviewSynthesis,
)
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


def _covered(state: GraphState, findings: tuple[ReviewFinding, ...]) -> tuple[str, ...]:
    """Subjects named by the findings. An unscoped finding affects the whole case."""
    if any(finding.subject_id is None for finding in findings):
        return state.subject_ids
    named = {finding.subject_id for finding in findings}
    return tuple(subject_id for subject_id in state.subject_ids if subject_id in named)


def _bounds(
    state: GraphState, ctx: NodeContext, subject_ids: tuple[str, ...]
) -> tuple[SubjectAbstention, ...]:
    """One conservative bound per subject; another tree's weaker bound is never folded in."""
    from dendro_inspector.nodes.final_decision import decide_subject

    bounds = []
    for subject_id in subject_ids:
        candidates = state.candidates_for(subject_id)
        resolution = (
            decide_subject(state, ctx, candidates).resolution
            if candidates is not None
            else Resolution.UNKNOWN
        )
        bounds.append(
            SubjectAbstention(subject_id=subject_id, resolution=lower_resolution(resolution))
        )
    return tuple(bounds)


def settle_unverified_abstention(state: GraphState, ctx: NodeContext) -> GraphState:
    """Apply the abstentions a model asserted without evidence, once arbitration has had its say.

    Such a finding never routes to this node directly: abstaining there would suppress the
    arbiter, and a single reviewer could then withdraw a claim with no independent look. It is
    held instead, and settled here, per subject it names:

    * arbitration ran and the arbiter's own accepted findings request abstention for the
      subject — confirmed independently, so the subject abstains;
    * arbitration ran and the arbiter requested nothing of the kind — the claim stands, still
      bounded by the reviewer's admitted recommendations like any other accepted finding;
    * arbitration did not run (disabled, switched off, or suppressed) — fail closed: the
      subject abstains, exactly as it would have before the finding needed confirmation.
    """
    synthesis = state.synthesis
    if synthesis is None or not synthesis.unverified_abstention:
        return state
    pending = tuple(
        finding
        for finding in synthesis.accepted_findings
        if requests_abstention(finding) and not is_grounded(finding)
    )
    subjects = tuple(
        subject_id for subject_id in _covered(state, pending) if not state.is_abstained(subject_id)
    )
    if state.arbiter_synthesis is not None:
        confirming = tuple(
            finding
            for finding in state.arbiter_synthesis.accepted_findings
            if requests_abstention(finding)
        )
        confirmed = set(_covered(state, confirming))
        subjects = tuple(subject_id for subject_id in subjects if subject_id in confirmed)
    if not subjects:
        return state
    return state.evolve(
        abstained=True,
        abstention_bounds=(*state.abstention_bounds, *_bounds(state, ctx, subjects)),
    )


async def run(state: GraphState, ctx: NodeContext) -> GraphState:
    synthesis = state.synthesis
    # A critical abstain finding needs a verifiable basis to withdraw a claim here; one that
    # rests on a model's word alone waits for arbitration (`settle_unverified_abstention`).
    blocking = tuple(
        finding
        for finding in (synthesis.accepted_findings if synthesis else ())
        if finding.required_action is RequiredAction.RE_EXTRACT_EVIDENCE
        or (requests_abstention(finding) and is_grounded(finding))
    )
    # An unscoped finding affects the case. Otherwise only the named subjects lose their
    # claims; another tree's weaker bound is never folded into this subject's result.
    affected = _covered(state, blocking) if blocking else state.subject_ids
    return state.evolve(
        abstained=True,
        abstention_bounds=_bounds(state, ctx, affected),
    )
