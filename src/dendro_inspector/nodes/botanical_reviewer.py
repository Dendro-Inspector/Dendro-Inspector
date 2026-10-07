"""Botanical reviewer.

Checks the botany: leaf arrangement, leaf shape, venation, buds, fruit, cones, needles,
branch arrangement, and internal contradictions.

The deterministic layer catches one class the model reliably misses — a candidate whose
own taxon card lists a contradicting feature that is nonetheless visible in the evidence.
That is a card-versus-evidence conflict, checkable without judgement.
"""

from __future__ import annotations

from dendro_inspector.graph.executor import NodeContext
from dendro_inspector.graph.state import GraphState
from dendro_inspector.knowledge.taxon_cards import match_card
from dendro_inspector.nodes._support import merge_findings, review_call
from dendro_inspector.schemas.reviews import (
    FindingCategory,
    FindingOrigin,
    Impact,
    RequiredAction,
    Reviewer,
    ReviewFinding,
    Severity,
)

NODE = "botanical_reviewer"


def card_contradiction_findings(state: GraphState, ctx: NodeContext) -> tuple[ReviewFinding, ...]:
    """Flag a **leading** candidate whose card-declared contradictions are actually visible.

    Only the leader is checked. A visible contradiction against a rank-2 alternative is not
    a defect in the answer — it is part of why that alternative lost. Raising a finding for
    it would mark a perfectly sound result as conflicted, which is how a useful signal turns
    into noise that reviewers learn to ignore.
    """
    evidence = state.evidence
    if evidence is None:
        return ()

    findings: list[ReviewFinding] = []
    for candidate_set in state.candidate_sets:
        leader = candidate_set.leader
        if leader is None:
            continue
        card = ctx.knowledge.try_taxon(leader.taxon)
        if card is None:
            continue
        match = match_card(card, evidence, candidate_set.subject_id)
        if not match.has_contradiction:
            continue

        disqualifying = frozenset(match.disqualifying_hits)
        weak_hits = tuple(
            evidence_id
            for evidence_id in match.contradiction_hits
            if evidence_id not in disqualifying
        )

        if match.is_disqualified:
            findings.append(
                ReviewFinding(
                    origin=FindingOrigin.DETERMINISTIC,
                    finding_id=f"auto-botanical-{candidate_set.subject_id}",
                    category=FindingCategory.BOTANICAL_CONTRADICTION,
                    severity=Severity.MAJOR,
                    summary=(
                        f"Leading candidate {card.display_name} is contradicted by visible "
                        "evidence its own card declares disqualifying."
                    ),
                    evidence_ids=match.disqualifying_hits,
                    subject_id=candidate_set.subject_id,
                    required_action=RequiredAction.LOWER_CONFIDENCE,
                    impact=Impact.CONFIDENCE_CHANGE,
                )
            )

        if weak_hits:
            findings.append(
                ReviewFinding(
                    origin=FindingOrigin.DETERMINISTIC,
                    finding_id=f"auto-nondisqualifying-{candidate_set.subject_id}",
                    category=FindingCategory.MISSING_DECISIVE_FEATURE,
                    severity=Severity.MINOR,
                    summary=(
                        f"Observed evidence is atypical for {card.display_name}, but it "
                        "cannot disqualify the candidate because it lacks identification "
                        "authority or is no stronger than bark."
                    ),
                    evidence_ids=weak_hits,
                    subject_id=candidate_set.subject_id,
                    required_action=RequiredAction.NONE,
                    impact=Impact.NO_MATERIAL_CHANGE,
                )
            )
    return tuple(findings)


async def run(state: GraphState, ctx: NodeContext) -> GraphState:
    result = await review_call(ctx, node=NODE, reviewer=Reviewer.BOTANICAL)
    result = merge_findings(result, card_contradiction_findings(state, ctx))
    return state.evolve(reviews=(*state.reviews, result))
