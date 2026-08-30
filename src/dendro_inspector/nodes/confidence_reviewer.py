"""Confidence reviewer.

Asks whether the claim is as strong as the evidence, whether species level is earned,
whether negative evidence is valid, and whether abstention would be the better answer.

The deterministic layer enforces the one rule this project will not negotiate: a
species-level claim requires a card that supports species resolution *and* the features
that card says are required. "The model sounded sure" is not a qualifying feature.
"""

from __future__ import annotations

from dendro_inspector.graph.executor import NodeContext
from dendro_inspector.graph.state import GraphState
from dendro_inspector.knowledge.evidence_hierarchy import best_tier, resolution_ceiling
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
from dendro_inspector.schemas.taxon import Resolution, resolution_rank

NODE = "confidence_reviewer"


def unsupported_resolution_findings(
    state: GraphState, ctx: NodeContext
) -> tuple[ReviewFinding, ...]:
    """Flag leading candidates claiming a resolution their card does not support."""
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
            if leader.resolution is Resolution.SPECIES:
                findings.append(
                    ReviewFinding(
                        origin=FindingOrigin.DETERMINISTIC,
                        finding_id=f"auto-resolution-unknown-card-{candidate_set.subject_id}",
                        category=FindingCategory.RESOLUTION_TOO_SPECIFIC,
                        severity=Severity.CRITICAL,
                        summary=(
                            f"Species-level claim for {leader.taxon!r}, which has no taxon card "
                            "in this project. Nothing here can justify that resolution."
                        ),
                        subject_id=candidate_set.subject_id,
                        required_action=RequiredAction.LOWER_RESOLUTION,
                        impact=Impact.RESOLUTION_CHANGE,
                    )
                )
            continue

        if not card.supports(leader.resolution):
            # Recorded, but with no required action: the final decision engine caps the
            # claim to what the card supports regardless. Asking for a downgrade as well
            # would apply the same correction twice and bury a genus in family.
            findings.append(
                ReviewFinding(
                    origin=FindingOrigin.DETERMINISTIC,
                    finding_id=f"auto-resolution-{candidate_set.subject_id}",
                    category=FindingCategory.RESOLUTION_TOO_SPECIFIC,
                    severity=Severity.CRITICAL,
                    summary=(
                        f"{card.display_name} card supports "
                        f"{', '.join(r.value for r in card.supported_resolution)}, "
                        f"but the candidate claims {leader.resolution.value}. "
                        "The decision engine caps this to the supported level."
                    ),
                    subject_id=candidate_set.subject_id,
                    required_action=RequiredAction.NONE,
                    impact=Impact.RESOLUTION_CHANGE,
                )
            )
            continue

        match = match_card(card, evidence, candidate_set.subject_id)
        if match.missing_for_high_confidence:
            findings.append(
                ReviewFinding(
                    origin=FindingOrigin.DETERMINISTIC,
                    finding_id=f"auto-missing-decisive-{candidate_set.subject_id}",
                    category=FindingCategory.MISSING_DECISIVE_FEATURE,
                    severity=Severity.MAJOR,
                    summary=(
                        f"{card.display_name} requires "
                        f"{', '.join(match.missing_for_high_confidence)} before high "
                        "confidence is defensible; not visible here."
                    ),
                    subject_id=candidate_set.subject_id,
                    required_action=RequiredAction.LOWER_CONFIDENCE,
                    impact=Impact.CONFIDENCE_CHANGE,
                )
            )
    return tuple(findings)


def evidence_tier_findings(state: GraphState) -> tuple[ReviewFinding, ...]:
    """Flag a claim that outruns the strongest evidence available for its subject.

    Implements the domain prompt's confidence scale: bark alone sits in the 50-69 band, a
    cut face or leaf arrangement in 70-84, clear foliage in 85-94, and the top band is
    reserved for a fruit, seed, cone or acorn in the frame.

    Recorded with `required_action: none` — the decision engine applies the ceiling itself,
    and asking for a downgrade as well would apply the same correction twice.
    """
    evidence = state.evidence
    if evidence is None:
        return ()

    findings: list[ReviewFinding] = []
    for candidate_set in state.candidate_sets:
        leader = candidate_set.leader
        if leader is None:
            continue
        subject_id = candidate_set.subject_id
        tier = best_tier(evidence, subject_id)
        ceiling = resolution_ceiling(tier)
        if resolution_rank(leader.resolution) <= resolution_rank(ceiling):
            continue
        findings.append(
            ReviewFinding(
                origin=FindingOrigin.DETERMINISTIC,
                finding_id=f"auto-tier-{subject_id}",
                category=FindingCategory.RESOLUTION_TOO_SPECIFIC,
                severity=Severity.MAJOR,
                summary=(
                    f"Strongest evidence here is {tier.name.lower().replace('_', ' ')}, which "
                    f"supports {ceiling.value.replace('_', ' ')} at best; the candidate claims "
                    f"{leader.resolution.value.replace('_', ' ')}."
                ),
                subject_id=subject_id,
                required_action=RequiredAction.NONE,
                impact=Impact.RESOLUTION_CHANGE,
            )
        )
    return tuple(findings)


def invalid_negative_evidence_findings(state: GraphState) -> tuple[ReviewFinding, ...]:
    """Flag "absent" claims about features that were merely not resolvable in the frame.

    ``not visible`` is not ``not present``. Conflating them turns a shaded trunk into a
    confident negative.
    """
    evidence = state.evidence
    if evidence is None or not evidence.absent_features:
        return ()

    unresolvable = {
        observation.feature
        for observation in evidence.observations
        if observation.visibility.value == "not_visible"
    }
    overlap = sorted(set(evidence.absent_features) & unresolvable)
    if not overlap:
        return ()
    return (
        ReviewFinding(
            origin=FindingOrigin.DETERMINISTIC,
            finding_id="auto-invalid-negative",
            category=FindingCategory.INVALID_NEGATIVE_EVIDENCE,
            severity=Severity.MAJOR,
            summary=(
                f"Features recorded as absent but also recorded as not visible: "
                f"{', '.join(overlap)}. Not visible is not evidence of absence."
            ),
            required_action=RequiredAction.RE_EXTRACT_EVIDENCE,
            impact=Impact.OBSERVATION_CHANGE,
        ),
    )


async def run(state: GraphState, ctx: NodeContext) -> GraphState:
    result = await review_call(ctx, node=NODE, reviewer=Reviewer.CONFIDENCE)
    result = merge_findings(
        result,
        unsupported_resolution_findings(state, ctx)
        + evidence_tier_findings(state)
        + invalid_negative_evidence_findings(state),
    )
    return state.evolve(reviews=(*state.reviews, result))
