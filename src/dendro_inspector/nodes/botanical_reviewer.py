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
from dendro_inspector.knowledge.evidence_hierarchy import EvidenceTier, tier_of_feature
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

        # FAILURE 3 — an old, weathered, damaged or urban trunk can look nothing like the
        # textbook bark for its species, and one patch of it looks nothing like another.
        # A bark-only contradiction is recorded, but it does not disqualify a candidate.
        by_id = {o.observation_id: o for o in evidence.observations}
        bark_only_contradiction = all(
            tier_of_feature(by_id[observation_id].feature) <= EvidenceTier.BARK
            for observation_id in match.contradiction_hits
            if observation_id in by_id
        )

        if bark_only_contradiction:
            findings.append(
                ReviewFinding(
                    origin=FindingOrigin.DETERMINISTIC,
                    finding_id=f"auto-bark-atypical-{candidate_set.subject_id}",
                    category=FindingCategory.MISSING_DECISIVE_FEATURE,
                    severity=Severity.MINOR,
                    summary=(
                        f"Bark here is atypical for {card.display_name}, but bark alone "
                        "cannot disqualify it: age, weathering, damage and site change the "
                        "pattern, and one patch does not represent the trunk."
                    ),
                    evidence_ids=match.contradiction_hits,
                    subject_id=candidate_set.subject_id,
                    required_action=RequiredAction.NONE,
                    impact=Impact.NO_MATERIAL_CHANGE,
                )
            )
            continue

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
                evidence_ids=match.contradiction_hits,
                subject_id=candidate_set.subject_id,
                required_action=RequiredAction.LOWER_CONFIDENCE,
                impact=Impact.CONFIDENCE_CHANGE,
            )
        )
    return tuple(findings)


async def run(state: GraphState, ctx: NodeContext) -> GraphState:
    result = await review_call(ctx, node=NODE, reviewer=Reviewer.BOTANICAL)
    result = merge_findings(result, card_contradiction_findings(state, ctx))
    return state.evolve(reviews=(*state.reviews, result))
