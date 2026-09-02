"""Escalation gate.

Decides whether a second, independent model is worth calling. Deterministic and fully
configurable (:class:`~dendro_inspector.config.EscalationPolicy`), because escalation is a
cost/risk tradeoff an operator owns, not a judgement call to delegate to the model whose
work is under review.

Suppressors are evaluated first and win outright. Arbitrating a case the system has already
decided to abstain on buys nothing: a second opinion on "I cannot tell" is still "I cannot
tell", at twice the price.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dendro_inspector.config import EscalationPolicy
from dendro_inspector.graph.executor import NodeContext
from dendro_inspector.graph.state import EscalationDecision, GraphState
from dendro_inspector.schemas.taxon import Confidence, Resolution, resolution_rank

if TYPE_CHECKING:
    from dendro_inspector.schemas.decisions import FinalDecision

NODE = "escalation_gate"


#: Triggers that a cost-saving suppressor may never override. Escalation exists for exactly
#: these cases; suppressing one to save a call is how the gate quietly stops working.
HARD_TRIGGERS: frozenset[str] = frozenset(
    {
        "species_level_proposed",
        "possible_multiple_taxa",
        "user_challenged_result",
        "instruction_like_content_detected",
        "unresolved_contradiction",
        "forced_by_configuration",
    }
)


def _blocking_suppressors(state: GraphState, policy: EscalationPolicy) -> tuple[str, ...]:
    """Suppressors that apply unconditionally — a second opinion could not help here."""
    reasons: list[str] = []
    quality = state.quality
    if (
        policy.suppress_when_insufficient_evidence
        and quality is not None
        and not quality.sufficient
    ):
        reasons.append("evidence_insufficient")
    if policy.suppress_when_abstaining and state.abstained:
        reasons.append("already_abstaining")
    return tuple(reasons)


def _cost_suppressors(
    state: GraphState,
    policy: EscalationPolicy,
    provisional: tuple[FinalDecision, ...],
) -> tuple[str, ...]:
    """Suppressors that trade risk for cost. Overridden by any hard trigger."""
    reasons: list[str] = []
    synthesis = state.synthesis

    broad_only = bool(provisional) and all(
        resolution_rank(decision.resolution) <= resolution_rank(Resolution.GENUS)
        for decision in provisional
    )
    clean = synthesis is not None and not synthesis.accepted_findings
    modest_confidence = not any(decision.confidence is Confidence.HIGH for decision in provisional)

    if policy.suppress_when_broad_and_low_risk and broad_only and clean and modest_confidence:
        reasons.append("broad_and_low_risk")
    if policy.suppress_when_clean_and_medium_confidence and clean and modest_confidence:
        reasons.append("clean_review_and_modest_confidence")
    return tuple(dict.fromkeys(reasons))


def _triggers(
    state: GraphState,
    policy: EscalationPolicy,
    provisional: tuple[FinalDecision, ...],
) -> tuple[str, ...]:
    reasons: list[str] = []
    guard = state.guard
    plan = state.plan
    evidence = state.evidence
    quality = state.quality
    synthesis = state.synthesis

    for candidate_set in state.candidate_sets:
        leader = candidate_set.leader
        if leader is None:
            continue
        if policy.on_species_resolution and leader.resolution is Resolution.SPECIES:
            reasons.append("species_level_proposed")
        if policy.on_close_leading_candidates and candidate_set.leaders_are_close():
            reasons.append("leading_candidates_close")

    if policy.on_high_confidence and (
        (synthesis is not None and synthesis.confidence_delta is Confidence.HIGH)
        or any(decision.confidence is Confidence.HIGH for decision in provisional)
    ):
        reasons.append("high_confidence_proposed")
    if policy.on_reviewer_disagreement and synthesis is not None:
        if synthesis.reviewer_disagreement:
            reasons.append("reviewer_disagreement")
        if synthesis.has_critical:
            reasons.append("critical_finding")
        if (
            synthesis.escalation_recommended
            and not synthesis.reviewer_disagreement
            and not synthesis.has_critical
        ):
            # Compatibility for a synthesis produced by older/custom code that only set
            # the combined flag. New Dendro synthesis always records the exact provenance.
            reasons.append("escalation_provenance_unknown")
    if policy.on_unresolved_contradiction and synthesis is not None and synthesis.has_critical:
        reasons.append("unresolved_contradiction")
    if (
        policy.on_bark_colour_dependence
        and quality is not None
        and quality.colour_dependence_detected
    ):
        reasons.append("bark_colour_dependence")
    if policy.on_multiple_taxa and evidence is not None and evidence.possible_multiple_taxa:
        reasons.append("possible_multiple_taxa")
    if policy.on_user_challenge and guard is not None and guard.user_challenges_previous_result:
        reasons.append("user_challenged_result")
    if policy.on_bark_only_input and plan is not None and plan.bark_only_input:
        reasons.append("bark_only_input")
    if (
        policy.on_instruction_like_content
        and evidence is not None
        and evidence.instruction_like_content_detected
    ):
        reasons.append("instruction_like_content_detected")
    if policy.forced_by_eval_case:
        reasons.append("forced_by_configuration")

    return tuple(dict.fromkeys(reasons))


def decide(
    state: GraphState,
    policy: EscalationPolicy,
    provisional: tuple[FinalDecision, ...],
) -> EscalationDecision:
    """Pure escalation decision.

    Precedence: blocking suppressors, then hard triggers, then cost suppressors, then any
    remaining soft trigger. A cost suppressor can never talk the gate out of a hard trigger.
    """
    if not policy.enabled:
        return EscalationDecision(required=False, suppressed_by=("policy_disabled",))

    triggers = _triggers(state, policy, provisional)

    blocking = _blocking_suppressors(state, policy)
    if blocking:
        return EscalationDecision(required=False, reasons=triggers, suppressed_by=blocking)

    if set(triggers) & HARD_TRIGGERS:
        return EscalationDecision(required=True, reasons=triggers)

    cost = _cost_suppressors(state, policy, provisional)
    if cost:
        return EscalationDecision(required=False, reasons=triggers, suppressed_by=cost)

    return EscalationDecision(required=bool(triggers), reasons=triggers)


async def run(state: GraphState, ctx: NodeContext) -> GraphState:
    from dendro_inspector.nodes.final_decision import decide_subject

    provisional = tuple(
        decide_subject(state, ctx, candidate_set) for candidate_set in state.candidate_sets
    )
    updated = state.evolve(provisional_decisions=provisional)
    decision = decide(updated, ctx.config.escalation, provisional)
    ctx.recorder.record_escalation(triggered=decision.required, reasons=decision.reasons)
    return updated.evolve(escalation=decision)
