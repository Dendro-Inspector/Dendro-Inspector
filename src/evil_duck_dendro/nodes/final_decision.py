"""Final decision engine.

Deterministic. Everything a model said has already been turned into structured findings and
adjudicated; this node applies them arithmetically, so the same inputs always produce the
same verdict and the derivation is inspectable in the trace.

Three rules are absolute here:

* **the claim is capped by the card.** A taxon card that supports only ``genus`` cannot
  yield a species answer no matter what any model proposed;
* **downgrades compose, upgrades do not.** Every accepted finding can lower confidence or
  broaden resolution. Nothing in this node ever raises either;
* **species is never forced.** Falling back to genus, species group, family or ``unknown``
  is a valid terminal outcome.
"""

from __future__ import annotations

from evil_duck_dendro.graph.executor import NodeContext
from evil_duck_dendro.graph.state import GraphState
from evil_duck_dendro.knowledge.comparison_cards import recommended_photos
from evil_duck_dendro.knowledge.evidence_hierarchy import (
    EvidenceTier,
    bark_only,
    best_tier,
    confidence_band,
    confidence_ceiling,
    resolution_ceiling,
)
from evil_duck_dendro.knowledge.taxon_cards import match_card
from evil_duck_dendro.schemas.candidates import Candidate, CandidateSet, SupportStrength
from evil_duck_dendro.schemas.decisions import (
    DecisionStatus,
    FinalDecision,
    PhotoRequest,
    UserClaimVerdict,
)
from evil_duck_dendro.schemas.evidence import EvidencePacket
from evil_duck_dendro.schemas.input import DeclaredObjectType
from evil_duck_dendro.schemas.reviews import (
    FindingCategory,
    RequiredAction,
    ReviewSynthesis,
    Severity,
)
from evil_duck_dendro.schemas.taxon import (
    Confidence,
    Resolution,
    TaxonCard,
    confidence_rank,
    lower_confidence,
    lower_resolution,
    resolution_rank,
)

NODE = "final_decision"

#: Candidate support strength maps to a confidence ceiling, never to certainty.
_SCORE_TO_CONFIDENCE: dict[SupportStrength, Confidence] = {
    SupportStrength.STRONG: Confidence.HIGH,
    SupportStrength.MODERATE: Confidence.MEDIUM,
    SupportStrength.WEAK: Confidence.LOW,
}

#: Which feature family backs a user's declared object type.
_DECLARED_FEATURE_FAMILY: dict[DeclaredObjectType, str] = {
    DeclaredObjectType.CONE: "cone",
    DeclaredObjectType.LEAF: "leaf",
    DeclaredObjectType.NEEDLE: "needles",
    DeclaredObjectType.FRUIT: "fruit",
    DeclaredObjectType.SEED: "seed",
    DeclaredObjectType.BARK: "bark",
    DeclaredObjectType.WOOD: "wood",
}


def _syntheses(state: GraphState) -> tuple[ReviewSynthesis, ...]:
    return tuple(s for s in (state.synthesis, state.arbiter_synthesis) if s is not None)


def _accepted_for(state: GraphState, subject_id: str) -> tuple[FindingCategory, ...]:
    return tuple(
        finding.category
        for synthesis in _syntheses(state)
        for finding in synthesis.accepted_findings
        if finding.subject_id in (None, subject_id)
    )


def _actions_for(state: GraphState, subject_id: str) -> tuple[RequiredAction, ...]:
    return tuple(
        finding.required_action
        for synthesis in _syntheses(state)
        for finding in synthesis.accepted_findings
        if finding.subject_id in (None, subject_id)
    )


def apply_reranking(state: GraphState, candidate_set: CandidateSet) -> CandidateSet:
    """Adopt a reviewer's recommended ranking when a rerank finding was accepted.

    The arbiter changes an answer *only* through this path: an accepted finding plus a
    concrete recommended ordering. A recommendation without an accepted finding is noise.
    """
    rerank_accepted = any(
        finding.required_action is RequiredAction.RERANK_CANDIDATES
        and finding.subject_id in (None, candidate_set.subject_id)
        for synthesis in _syntheses(state)
        for finding in synthesis.accepted_findings
    )
    if not rerank_accepted:
        return candidate_set

    for review in (*state.arbiter_reviews, *state.reviews):
        recommended = review.recommended_candidates
        if not recommended:
            continue
        if review.subject_id not in (None, candidate_set.subject_id):
            continue
        renumbered = tuple(
            candidate.model_copy(update={"rank": index})
            for index, candidate in enumerate(
                sorted(recommended, key=lambda item: item.rank), start=1
            )
        )
        return candidate_set.model_copy(update={"candidates": renumbered})
    return candidate_set


def cap_resolution(claimed: Resolution, card: TaxonCard | None) -> Resolution:
    """Narrow a claim down to what the card actually supports."""
    if card is None:
        return Resolution.GENUS if claimed is Resolution.SPECIES else claimed
    if card.supports(claimed):
        return claimed
    supported = [
        resolution
        for resolution in card.supported_resolution
        if resolution_rank(resolution) <= resolution_rank(claimed)
    ]
    if supported:
        return max(supported, key=resolution_rank)
    return min(card.supported_resolution, key=resolution_rank)


def normalise_claim(text: str) -> str:
    """Reduce a free-text taxon name to a comparable token.

    People name trees in their own language and their own spelling. Matching is best-effort
    and deliberately generous — a claim we fail to recognise is treated as unrecognised, not
    as wrong.
    """
    return "".join(character for character in text.lower().strip() if character.isalnum())


def _claim_matches(claim: str, taxon_id: str, display_name: str, aliases: tuple[str, ...]) -> bool:
    normalised = normalise_claim(claim)
    if not normalised:
        return False
    candidates = {normalise_claim(taxon_id), normalise_claim(display_name)}
    candidates.update(normalise_claim(alias) for alias in aliases)
    return any(
        normalised == candidate or normalised in candidate or candidate in normalised
        for candidate in candidates
        if candidate
    )


def rule_on_user_claim(
    state: GraphState,
    ctx: NodeContext,
    subject_id: str,
    candidate_set: CandidateSet,
    evidence: EvidencePacket,
    selected_taxon: str | None,
) -> UserClaimVerdict:
    """Rule on the taxon the user proposed (domain prompt section 3).

    Rejection is deliberately hard to reach. It needs evidence above bark level and no
    field context from the user, because the user may be looking at foliage the photograph
    never captured — and because on one patch of bark nobody has the right to be certain.
    """
    claim = state.case.user_claim
    if not claim:
        return UserClaimVerdict.NOT_PROVIDED

    matched: str | None = None
    for taxon_id in ctx.knowledge.available_taxon_ids():
        card = ctx.knowledge.try_taxon(taxon_id)
        if card is not None and _claim_matches(claim, taxon_id, card.display_name, card.aliases):
            matched = taxon_id
            break

    if matched is None:
        # We do not have a card for what they said. That is our gap, not their error.
        return UserClaimVerdict.POSSIBLE

    if matched == selected_taxon:
        return UserClaimVerdict.ACCEPTED

    in_candidates = any(candidate.taxon == matched for candidate in candidate_set.ordered)
    card = ctx.knowledge.try_taxon(matched)
    contradicted = card is not None and match_card(card, evidence, subject_id).has_contradiction

    # Restraint clauses, in the order the prompt states them.
    if bark_only(evidence, subject_id) or state.case.user_has_field_context:
        return (
            UserClaimVerdict.POSSIBLE
            if in_candidates or not contradicted
            else UserClaimVerdict.DOUBTFUL
        )

    if contradicted:
        return UserClaimVerdict.REJECTED
    if in_candidates:
        return UserClaimVerdict.POSSIBLE
    return UserClaimVerdict.DOUBTFUL


def resolve_resolution(
    state: GraphState,
    subject_id: str,
    leader: Candidate,
    card: TaxonCard | None,
    tier: EvidenceTier,
) -> Resolution:
    resolution = cap_resolution(leader.resolution, card)
    capped_by_card = resolution is not leader.resolution

    # The evidence hierarchy caps the claim independently of the card: a genus card cannot
    # rescue a genus claim made from a silhouette.
    tier_ceiling = resolution_ceiling(tier)
    if resolution_rank(tier_ceiling) < resolution_rank(resolution):
        resolution = tier_ceiling
        capped_by_card = True

    for synthesis in _syntheses(state):
        if synthesis.resolution_delta is not None and resolution_rank(
            synthesis.resolution_delta
        ) < resolution_rank(resolution):
            resolution = synthesis.resolution_delta

    # A "lower the resolution" finding is usually raised *because* the model overclaimed
    # past what the card supports. Capping already applied that correction, so applying the
    # finding as well would punish the same mistake twice and bury a genus in family.
    if not capped_by_card and RequiredAction.LOWER_RESOLUTION in _actions_for(state, subject_id):
        resolution = lower_resolution(resolution)
    return resolution


def resolve_confidence(
    state: GraphState,
    subject_id: str,
    leader: Candidate,
    card: TaxonCard | None,
    evidence: EvidencePacket,
    tier: EvidenceTier,
) -> Confidence:
    confidence = _SCORE_TO_CONFIDENCE[leader.score]

    # The evidence hierarchy ceiling comes first and is not negotiable. Bark caps at low
    # however characteristic it looks — FAILURE 8.
    tier_cap = confidence_ceiling(tier)
    if confidence_rank(tier_cap) < confidence_rank(confidence):
        confidence = tier_cap

    if card is not None:
        match = match_card(card, evidence, subject_id)
        if match.missing_for_high_confidence and confidence is Confidence.HIGH:
            confidence = Confidence.MEDIUM

    for synthesis in _syntheses(state):
        if synthesis.confidence_delta is not None and confidence_rank(
            synthesis.confidence_delta
        ) < confidence_rank(confidence):
            confidence = synthesis.confidence_delta

    downgrades = sum(
        1 for action in _actions_for(state, subject_id) if action is RequiredAction.LOWER_CONFIDENCE
    )
    for _ in range(downgrades):
        confidence = lower_confidence(confidence)

    if state.abstained:
        confidence = Confidence.LOW
    return confidence


def _unsupported_user_claim(state: GraphState, evidence: EvidencePacket, subject_id: str) -> bool:
    """The user said it is a cone; no cone feature is resolvable anywhere in the frame."""
    family = _DECLARED_FEATURE_FAMILY.get(state.case.declared_object_type)
    if family is None:
        return False
    return not evidence.has_feature(subject_id, family)


def decide_status(
    state: GraphState,
    ctx: NodeContext,
    subject_id: str,
    evidence: EvidencePacket,
    resolution: Resolution,
    confidence: Confidence,
    taxon: str | None,
) -> DecisionStatus:
    """Status describes the answer being returned, not the history that produced it.

    The contradiction check is recomputed against the *selected* taxon rather than read off
    the accepted findings. After a rerank, a finding raised against the candidate that lost
    says nothing about the one that won — reporting the winner as "conflicting evidence"
    because its predecessor was contradicted is simply wrong.
    """
    if taxon is None or resolution is Resolution.UNKNOWN:
        return DecisionStatus.INSUFFICIENT_EVIDENCE

    card = ctx.knowledge.try_taxon(taxon)
    if card is not None and match_card(card, evidence, subject_id).has_contradiction:
        return DecisionStatus.CONFLICTING_EVIDENCE
    if _unsupported_user_claim(state, evidence, subject_id):
        return DecisionStatus.UNSUPPORTED_USER_CLAIM
    if confidence is Confidence.HIGH:
        return DecisionStatus.IDENTIFIED
    return DecisionStatus.PROBABLE


def _support_summary(evidence: EvidencePacket, candidate: Candidate) -> str | None:
    by_id = {o.observation_id: o for o in evidence.observations}
    for evidence_id in candidate.supporting_evidence_ids:
        observation = by_id.get(evidence_id)
        if observation is not None:
            return (
                f"{observation.feature} = {observation.value} "
                f"({observation.reliability.value} reliability)"
            )
    return None


def _contradiction_summary(
    state: GraphState, subject_id: str, evidence: EvidencePacket, candidate: Candidate
) -> str | None:
    by_id = {o.observation_id: o for o in evidence.observations}
    for evidence_id in candidate.contradicting_evidence_ids:
        observation = by_id.get(evidence_id)
        if observation is not None:
            return f"{observation.feature} = {observation.value}"
    for synthesis in _syntheses(state):
        for finding in synthesis.accepted_findings:
            if finding.subject_id in (None, subject_id) and finding.severity in (
                Severity.CRITICAL,
                Severity.MAJOR,
            ):
                return finding.summary
    return None


def _next_photo(
    ctx: NodeContext,
    candidate_set: CandidateSet,
    leader: Candidate,
    tier: EvidenceTier,
    confidence: Confidence,
) -> PhotoRequest | None:
    """The one photograph that would most improve the result — or none, honestly.

    At the decisive tier with high confidence there is nothing left to ask for. Requesting
    a fruit photograph when the fruit is already in the frame is the kind of reflexive
    hedging that teaches people to ignore the request entirely.
    """
    if tier is EvidenceTier.FRUIT_SEED and confidence is Confidence.HIGH:
        return None

    taxa = frozenset(candidate.taxon for candidate in candidate_set.ordered)
    photos = recommended_photos(ctx.knowledge.comparisons_for(taxa))
    if not photos:
        photos = ctx.knowledge.follow_up_for((leader.taxon,))
    if not photos:
        return None
    return PhotoRequest(
        target=photos[0],
        reason="Would separate the leading candidate from its nearest alternative.",
        subject_id=candidate_set.subject_id,
    )


def _unresolved(state: GraphState, subject_id: str, leader: Candidate) -> tuple[str, ...]:
    questions = [
        f"Decisive feature not visible: {item}" for item in leader.missing_decisive_features
    ]
    questions.extend(
        finding.summary
        for synthesis in _syntheses(state)
        for finding in synthesis.accepted_findings
        if finding.subject_id in (None, subject_id)
    )
    return tuple(dict.fromkeys(questions))[:8]


def decide_subject(
    state: GraphState, ctx: NodeContext, candidate_set: CandidateSet
) -> FinalDecision:
    evidence = state.evidence
    if evidence is None:  # pragma: no cover - routing guarantees this; loud if it ever breaks
        msg = "final decision reached without an evidence packet"
        raise RuntimeError(msg)

    reranked = apply_reranking(state, candidate_set)
    leader = reranked.leader
    subject_id = reranked.subject_id
    if leader is None:
        return FinalDecision(
            subject_id=subject_id,
            status=DecisionStatus.INSUFFICIENT_EVIDENCE,
            arbiter_used=state.arbiter_used,
        )

    card = ctx.knowledge.try_taxon(leader.taxon)
    tier = best_tier(evidence, subject_id)
    resolution = resolve_resolution(state, subject_id, leader, card, tier)
    confidence = resolve_confidence(state, subject_id, leader, card, evidence, tier)
    runner_up = reranked.runner_up

    taxon = leader.taxon if resolution is not Resolution.UNKNOWN else None
    verdict = rule_on_user_claim(state, ctx, subject_id, reranked, evidence, taxon)
    return FinalDecision(
        subject_id=subject_id,
        selected_taxon=taxon,
        resolution=resolution,
        confidence=confidence,
        status=decide_status(state, ctx, subject_id, evidence, resolution, confidence, taxon),
        strongest_support=_support_summary(evidence, leader),
        strongest_contradiction=_contradiction_summary(state, subject_id, evidence, leader),
        nearest_alternative=runner_up.taxon if runner_up else None,
        unresolved_questions=_unresolved(state, subject_id, leader),
        best_next_photo=_next_photo(ctx, reranked, leader, tier, confidence),
        arbiter_used=state.arbiter_used,
        user_claim_verdict=verdict,
        evidence_tier=int(tier),
        confidence_band=confidence_band(confidence, tier),
    )


async def run(state: GraphState, ctx: NodeContext) -> GraphState:
    if state.decisions:
        # The photo-planner path already produced terminal decisions.
        return state
    if state.evidence is None or not state.candidate_sets:
        return state.evolve(
            decisions=(
                FinalDecision(
                    subject_id="case",
                    status=DecisionStatus.INSUFFICIENT_EVIDENCE,
                    arbiter_used=state.arbiter_used,
                    user_claim_verdict=(
                        UserClaimVerdict.NOT_EVALUABLE
                        if state.case.user_claim
                        else UserClaimVerdict.NOT_PROVIDED
                    ),
                ),
            )
        )
    return state.evolve(
        decisions=tuple(
            decide_subject(state, ctx, candidate_set) for candidate_set in state.candidate_sets
        )
    )
