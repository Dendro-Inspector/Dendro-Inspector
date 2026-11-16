"""Escalation gate.

Decides whether a second, independent model is worth calling. Deterministic and fully
configurable (:class:`~dendro_inspector.config.EscalationPolicy`), because escalation is a
cost/risk tradeoff an operator owns, not a judgement call to delegate to the model whose
work is under review.

When every subject has abstained, the blocking suppressor can skip arbitration. Partial
abstention leaves other subjects eligible for review. Hard triggers override cost-saving
suppressors. Insufficient evidence already takes the photo-planner branch of the graph;
this gate does not repeat that routing decision.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dendro_inspector.config import EscalationPolicy
from dendro_inspector.graph.budget import SPEND_LIMIT_REACHED, Stage, affords
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
        "review_every_decision",
    }
)


#: The triggers selective arbitration still calls the arbiter for: a possible overclaim, or a
#: case a second look exists for. Every hard trigger except the operator's every-decision
#: switch, plus the two soft triggers that guard a claim from being too strong.
#:
#: Left out, and why, measured over 1,450 arbitrated live cases from 2026-11-12 to 16 in
#: which the arbiter changed the provisional answer 20 times: `reviewer_disagreement`
#: fired in 72% of them and preceded a change 1.2% of the time; `critical_finding` is the
#: same signal as the hard `unresolved_contradiction`; `bark_only_input` and
#: `bark_colour_dependence` describe input the evidence hierarchy already caps. This set
#: would have arbitrated 61% of those cases and seen 17 of the 20 changes; the three it
#: would have missed were low-confidence answers the arbiter re-named, none an overclaim.
SELECTIVE_TRIGGERS: frozenset[str] = frozenset(
    {
        *(HARD_TRIGGERS - {"review_every_decision"}),
        "high_confidence_proposed",
        "leading_candidates_close",
    }
)


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
    return tuple(reasons)


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
    if policy.review_every_decision:
        reasons.append("review_every_decision")

    return tuple(dict.fromkeys(reasons))


def decide(
    state: GraphState,
    policy: EscalationPolicy,
    provisional: tuple[FinalDecision, ...],
) -> EscalationDecision:
    """Pure escalation decision.

    Precedence: blocking suppressors, then hard triggers, then cost suppressors, then any
    remaining soft trigger. A cost suppressor can never talk the gate out of a hard trigger.

    Under ``selective_arbitration`` only `SELECTIVE_TRIGGERS` take part in that precedence,
    and the every-decision switch is ignored. The reasons recorded are all that fired, so a
    trace shows what selective arbitration chose not to send.
    """
    if not policy.enabled:
        return EscalationDecision(required=False, suppressed_by=("policy_disabled",))

    triggers = _triggers(state, policy, provisional)
    every = policy.review_every_decision and not policy.selective_arbitration
    if policy.selective_arbitration:
        # Every trigger is still recorded; only the selective ones may call the arbiter.
        active = tuple(reason for reason in triggers if reason in SELECTIVE_TRIGGERS)
    else:
        active = triggers

    if (
        policy.suppress_when_abstaining
        and not every
        and state.abstained
        and all(state.is_abstained(subject_id) for subject_id in state.subject_ids)
    ):
        return EscalationDecision(
            required=False, reasons=triggers, suppressed_by=("already_abstaining",)
        )

    if triggers and not active:
        return EscalationDecision(
            required=False, reasons=triggers, suppressed_by=("selective_arbitration",)
        )

    if set(active) & HARD_TRIGGERS:
        return EscalationDecision(required=True, reasons=triggers)

    cost = _cost_suppressors(state, policy, provisional)
    if cost:
        return EscalationDecision(required=False, reasons=triggers, suppressed_by=cost)

    return EscalationDecision(required=bool(active), reasons=triggers)


async def run(state: GraphState, ctx: NodeContext) -> GraphState:
    from dendro_inspector.nodes.final_decision import decide_subject

    provisional = tuple(
        decide_subject(state, ctx, candidate_set) for candidate_set in state.candidate_sets
    )
    updated = state.evolve(provisional_decisions=provisional)
    decision = decide(updated, ctx.config.escalation, provisional)
    stops = updated.budget_stops
    if decision.required and not affords(ctx.config.graph, ctx.recorder.spend(), Stage.ARBITRATION):
        # The one suppressor that may stand against a hard trigger: an operator's limit,
        # not a judgement that the call is unnecessary. The reasons stay recorded, so the
        # trace shows a call that was due and not made. An abstention that was waiting
        # for the arbiter then fails closed, as for any arbitration that did not run.
        decision = EscalationDecision(
            required=False, reasons=decision.reasons, suppressed_by=(SPEND_LIMIT_REACHED,)
        )
        stops = (*stops, Stage.ARBITRATION.value)
    ctx.recorder.record_escalation(
        triggered=decision.required,
        reasons=decision.reasons,
        hard_reasons=tuple(
            reason
            for reason in decision.reasons
            if reason in HARD_TRIGGERS
            and not (
                ctx.config.escalation.selective_arbitration and reason == "review_every_decision"
            )
        ),
        suppressed_by=decision.suppressed_by,
    )
    return updated.evolve(escalation=decision, budget_stops=stops)
