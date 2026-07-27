"""Confusion reviewer.

Asks the four questions that separate an identification from a guess:

1. What evidence contradicts the leading candidate?
2. Which alternative explains the same observations?
3. What decisive feature is missing?
4. What is the highest defensible taxonomic level?

The deterministic layer owns the colour trap. Bark colour varies with age, aspect,
moisture, lighting and white balance; a conclusion resting on it is not an identification,
it is a description of the photograph's exposure.
"""

from __future__ import annotations

from evil_duck_dendro.graph.executor import NodeContext
from evil_duck_dendro.graph.state import GraphState
from evil_duck_dendro.knowledge.comparison_cards import decisive_features_between
from evil_duck_dendro.knowledge.evidence_hierarchy import (
    bark_only,
    contextual_observations_for,
    is_colour_feature,
    positive_observations_for,
    unattached_observations,
)
from evil_duck_dendro.knowledge.regional_packs import region_assumption_risk
from evil_duck_dendro.nodes._support import merge_findings, review_call
from evil_duck_dendro.schemas.evidence import AttachmentStatus
from evil_duck_dendro.schemas.reviews import (
    FindingCategory,
    FindingOrigin,
    Impact,
    RequiredAction,
    Reviewer,
    ReviewFinding,
    Severity,
)

NODE = "confusion_reviewer"


def colour_findings(state: GraphState) -> tuple[ReviewFinding, ...]:
    """Flag every subject whose visible evidence leans on colour."""
    evidence = state.evidence
    quality = state.quality
    if evidence is None or quality is None or not quality.colour_dependence_detected:
        return ()

    findings: list[ReviewFinding] = []
    for subject in evidence.subjects:
        colour_ids = tuple(
            observation.observation_id
            for observation in contextual_observations_for(evidence, subject.subject_id)
            if is_colour_feature(observation.feature)
        )
        if not colour_ids:
            continue
        findings.append(
            ReviewFinding(
                origin=FindingOrigin.DETERMINISTIC,
                finding_id=f"auto-colour-{subject.subject_id}",
                category=FindingCategory.COLOUR_OVERWEIGHTING,
                severity=Severity.MAJOR,
                summary=(
                    "Identification leans on bark or wood colour, which varies with age, "
                    "aspect, moisture and white balance. It cannot be decisive on its own."
                ),
                evidence_ids=colour_ids,
                subject_id=subject.subject_id,
                required_action=RequiredAction.LOWER_CONFIDENCE,
                impact=Impact.CONFIDENCE_CHANGE,
            )
        )
    return tuple(findings)


def unattached_evidence_findings(state: GraphState) -> tuple[ReviewFinding, ...]:
    """FAILURE 6 — foliage in the corner of the frame belongs to whoever grew it.

    A leaf that cannot be traced along a branch to this trunk may belong to the neighbouring
    tree, and it must not be allowed to turn an oak into an ash. The evidence hierarchy
    already demotes it to context; this finding makes the demotion visible and asks for the
    photograph that would settle it.
    """
    evidence = state.evidence
    if evidence is None:
        return ()

    findings: list[ReviewFinding] = []
    for subject in evidence.subjects:
        loose = unattached_observations(evidence, subject.subject_id)
        if not loose:
            continue

        # The two states call for different photographs, so they are reported differently.
        detached = [o for o in loose if o.attachment is AttachmentStatus.CONFIRMED_DETACHED]
        summary = (
            "Foliage or fruit in frame is detached from this trunk, so it cannot support "
            "the identification — material on the ground belongs to whoever dropped it."
            if detached
            else "Foliage or fruit in frame could not be traced to this trunk, so it cannot "
            "support the identification. It may belong to a neighbouring tree."
        )
        findings.append(
            ReviewFinding(
                origin=FindingOrigin.DETERMINISTIC,
                finding_id=f"auto-unattached-{subject.subject_id}",
                category=FindingCategory.UNSUPPORTED_CLAIM,
                severity=Severity.MAJOR,
                summary=summary,
                evidence_ids=tuple(o.observation_id for o in loose),
                subject_id=subject.subject_id,
                required_action=RequiredAction.REQUEST_ADDITIONAL_PHOTO,
                impact=Impact.CONFIDENCE_CHANGE,
            )
        )
    return tuple(findings)


def bark_only_findings(state: GraphState) -> tuple[ReviewFinding, ...]:
    """FAILURE 2 and FAILURE 8 — bark alone decides nothing.

    Rough bark is shared by old poplar, robinia, alder, maple, ash, walnut and old apple.
    The claim is capped by the evidence hierarchy; this records why.
    """
    evidence = state.evidence
    if evidence is None or not state.candidate_sets:
        return ()

    findings: list[ReviewFinding] = []
    for candidate_set in state.candidate_sets:
        subject_id = candidate_set.subject_id
        if not bark_only(evidence, subject_id):
            continue
        findings.append(
            ReviewFinding(
                origin=FindingOrigin.DETERMINISTIC,
                finding_id=f"auto-bark-only-{subject_id}",
                category=FindingCategory.UNSUPPORTED_CLAIM,
                severity=Severity.MAJOR,
                summary=(
                    "Nothing above bark level is resolvable. Bark texture is shared across "
                    "many old broadleaves and varies with age, damage and site; it supports "
                    "a candidate but cannot confirm one."
                ),
                subject_id=subject_id,
                required_action=RequiredAction.REQUEST_ADDITIONAL_PHOTO,
                impact=Impact.CONFIDENCE_CHANGE,
            )
        )
    return tuple(findings)


def look_alike_findings(state: GraphState, ctx: NodeContext) -> tuple[ReviewFinding, ...]:
    """FAILURE 1, 4, 5 and 7 — name the look-alike the cards say shares this evidence.

    Driven by comparison cards rather than model recall: if the leading candidate shares an
    `insufficient_features` entry with a declared look-alike and nothing decisive separates
    them in the visible evidence, that look-alike is a live alternative and must be said out
    loud.
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
        if card is None or not card.common_confusions:
            continue

        proposed = {candidate.taxon for candidate in candidate_set.ordered}
        unnamed = [taxon for taxon in card.common_confusions if taxon not in proposed]
        if not unnamed:
            continue

        cards = ctx.knowledge.comparisons_for(frozenset({leader.taxon, *unnamed}))
        if not cards:
            # No comparison card links these taxa, so the project has no declared basis for
            # claiming they share this evidence. Silence here is honest; raising a finding
            # would be inventing a confusion from a name in a list.
            continue

        decisive = decisive_features_between(cards, frozenset({leader.taxon, *unnamed}))
        # Match the exact discriminator, not its family. `bark.pattern` is not `bark.peeling`,
        # and treating them as equivalent silently dismisses a live alternative.
        trusted_features = {
            observation.feature
            for observation in positive_observations_for(evidence, candidate_set.subject_id)
        }
        resolved = [feature for feature in decisive if feature in trusted_features]
        if resolved:
            continue

        findings.append(
            ReviewFinding(
                origin=FindingOrigin.DETERMINISTIC,
                finding_id=f"auto-lookalike-{candidate_set.subject_id}",
                category=FindingCategory.OVERLOOKED_ALTERNATIVE,
                severity=Severity.MAJOR,
                summary=(
                    f"{', '.join(unnamed)} share this evidence with {card.display_name} and "
                    f"none of the separating features ({', '.join(decisive) or 'unrecorded'}) "
                    "is resolvable here."
                ),
                subject_id=candidate_set.subject_id,
                proposed_taxon=unnamed[0],
                required_action=RequiredAction.REQUEST_ADDITIONAL_PHOTO,
                impact=Impact.CONFIDENCE_CHANGE,
            )
        )
    return tuple(findings)


def region_findings(state: GraphState, ctx: NodeContext) -> tuple[ReviewFinding, ...]:
    """Flag the case where a regional prior is available but no location was supplied."""
    if not region_assumption_risk(ctx.knowledge.region(), state.case.location):
        return ()
    return (
        ReviewFinding(
            origin=FindingOrigin.DETERMINISTIC,
            finding_id="auto-region-unknown",
            category=FindingCategory.REGION_ASSUMPTION,
            severity=Severity.MINOR,
            summary=(
                "A regional pack is loaded but the case has no location. Regional "
                "plausibility must not be applied to this result."
            ),
            required_action=RequiredAction.NONE,
            impact=Impact.NO_MATERIAL_CHANGE,
        ),
    )


async def run(state: GraphState, ctx: NodeContext) -> GraphState:
    result = await review_call(state, ctx, node=NODE, reviewer=Reviewer.CONFUSION)
    result = merge_findings(
        result,
        colour_findings(state)
        + unattached_evidence_findings(state)
        + bark_only_findings(state)
        + look_alike_findings(state, ctx)
        + region_findings(state, ctx),
    )
    return state.evolve(reviews=(*state.reviews, result))
