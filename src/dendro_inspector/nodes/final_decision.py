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

from dendro_inspector.graph.executor import NodeContext
from dendro_inspector.graph.state import GraphState
from dendro_inspector.knowledge.candidate_validation import (
    candidate_ranking_signature,
    candidate_support_tier,
)
from dendro_inspector.knowledge.comparison_cards import recommended_photos
from dendro_inspector.knowledge.evidence_hierarchy import (
    EvidenceTier,
    bark_only,
    confidence_band,
    confidence_ceiling,
    resolution_ceiling,
)
from dendro_inspector.knowledge.taxon_cards import match_card
from dendro_inspector.nodes.photo_planner import choose_request, effective_object_type
from dendro_inspector.schemas.candidates import Candidate, CandidateSet, SupportStrength
from dendro_inspector.schemas.decisions import (
    DecisionStatus,
    FinalDecision,
    PhotoRequest,
    UserClaimVerdict,
)
from dendro_inspector.schemas.evidence import EvidencePacket
from dendro_inspector.schemas.input import DeclaredObjectType
from dendro_inspector.schemas.reviews import (
    FindingCategory,
    RequiredAction,
    ReviewSynthesis,
    Severity,
)
from dendro_inspector.schemas.taxon import (
    Confidence,
    Resolution,
    TaxonCard,
    TaxonIdentity,
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


def _single_admitted_rerank(
    synthesis: ReviewSynthesis,
    subject_id: str,
) -> CandidateSet | None:
    rankings = tuple(
        rerank.candidate_set
        for rerank in synthesis.admitted_reranks
        if rerank.candidate_set.subject_id == subject_id
    )
    if not rankings:
        return None
    signatures = {candidate_ranking_signature(ranking) for ranking in rankings}
    return rankings[0] if len(signatures) == 1 else None


def apply_reranking(state: GraphState, candidate_set: CandidateSet) -> CandidateSet:
    """Consume finding-bound validated reranks only, preferring an unambiguous arbiter."""
    arbiter = state.arbiter_synthesis
    if arbiter is not None:
        arbiter_for_subject = tuple(
            rerank
            for rerank in arbiter.admitted_reranks
            if rerank.candidate_set.subject_id == candidate_set.subject_id
        )
        if arbiter_for_subject:
            return _single_admitted_rerank(arbiter, candidate_set.subject_id) or candidate_set

    internal = state.synthesis
    if internal is not None:
        internal_for_subject = tuple(
            rerank
            for rerank in internal.admitted_reranks
            if rerank.candidate_set.subject_id == candidate_set.subject_id
        )
        if internal_for_subject:
            return _single_admitted_rerank(internal, candidate_set.subject_id) or candidate_set
    return candidate_set


def cap_resolution(claimed: Resolution, card: TaxonCard | None) -> Resolution:
    """Apply the card's authority ceiling without ever narrowing the model's claim.

    A species-capable card also permits a broader genus or family answer. The old exact-list
    fallback could turn a family proposal into a species result when the card listed only
    species, which was an upgrade disguised as validation.
    """
    if claimed is Resolution.UNKNOWN or card is None:
        return Resolution.UNKNOWN
    card_ceiling = max(card.supported_resolution, key=resolution_rank)
    if resolution_rank(claimed) <= resolution_rank(card_ceiling):
        return claimed
    return card_ceiling


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
    """Compose every upper bound first, then apply at most one explicit downgrade."""
    bounds = [
        leader.resolution,
        cap_resolution(leader.resolution, card),
        resolution_ceiling(tier),
    ]
    bounds.extend(
        synthesis.resolution_delta
        for synthesis in _syntheses(state)
        if synthesis.resolution_delta is not None
    )
    resolution = min(bounds, key=resolution_rank)
    already_broadened = resolution_rank(resolution) < resolution_rank(leader.resolution)

    # A lower-resolution finding commonly records the same overclaim already represented by
    # a card, evidence, or synthesis bound. Do not apply the same correction twice.
    if not already_broadened and RequiredAction.LOWER_RESOLUTION in _actions_for(state, subject_id):
        resolution = lower_resolution(resolution)
    return resolution


def resolve_identity(card: TaxonCard | None, resolution: Resolution) -> TaxonIdentity | None:
    """Select a declared identity at or broader than the composed resolution bound."""
    if card is None:
        return None
    return card.identity_at_or_broader(resolution)


def _nearest_alternative(
    ctx: NodeContext,
    runner_up: Candidate | None,
    resolution: Resolution,
    selected: TaxonIdentity,
) -> str | None:
    if runner_up is None:
        return None
    alternative = resolve_identity(ctx.knowledge.try_taxon(runner_up.taxon), resolution)
    if alternative is None or alternative.resolution is not resolution:
        return None
    if alternative.taxon_id == selected.taxon_id:
        return None
    return alternative.taxon_id


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
    state: GraphState,
    ctx: NodeContext,
    subject_id: str,
    evidence: EvidencePacket,
    selected: TaxonIdentity,
    source_taxon: str,
) -> str | None:
    """Summarise contradictions against the selected identity, not its narrower source card."""
    card = ctx.knowledge.try_taxon(selected.taxon_id)
    if card is not None:
        by_id = {observation.observation_id: observation for observation in evidence.observations}
        for evidence_id in match_card(card, evidence, subject_id).contradiction_hits:
            observation = by_id.get(evidence_id)
            if observation is not None:
                return f"{observation.feature} = {observation.value}"

    for synthesis in _syntheses(state):
        for finding in synthesis.accepted_findings:
            if (
                selected.taxon_id != source_taxon
                and finding.category is FindingCategory.BOTANICAL_CONTRADICTION
            ):
                continue
            if finding.subject_id in (None, subject_id) and finding.severity in (
                Severity.CRITICAL,
                Severity.MAJOR,
            ):
                return finding.summary
    return None


def _next_photo(
    state: GraphState,
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

    if effective_object_type(state, candidate_set.subject_id) is DeclaredObjectType.SPLIT_FIREWOOD:
        return PhotoRequest(
            target="prepared_end_grain_and_bark_circumference",
            reason=(
                "Label one piece, then photograph a clean perpendicular end grain and the "
                "bark around that same piece; other pieces may be different taxa."
            ),
            subject_id=candidate_set.subject_id,
        )

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


def _unresolved(
    state: GraphState,
    evidence: EvidencePacket,
    subject_id: str,
    leader: Candidate,
) -> tuple[str, ...]:
    questions = []
    if evidence.possible_multiple_taxa:
        questions.append(
            "This verdict is scoped to this subject; other pieces or trees in the frame may "
            "be different taxa."
        )
    questions.extend(
        f"Decisive feature not visible: {item}" for item in leader.missing_decisive_features
    )
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
            best_next_photo=choose_request(state, ctx, subject_id),
            arbiter_used=state.arbiter_used,
        )

    card = ctx.knowledge.try_taxon(leader.taxon)
    tier = candidate_support_tier(leader, evidence, subject_id)
    resolution_bound = resolve_resolution(state, subject_id, leader, card, tier)
    selected = resolve_identity(card, resolution_bound)
    if selected is None:
        verdict = rule_on_user_claim(state, ctx, subject_id, reranked, evidence, None)
        return FinalDecision(
            subject_id=subject_id,
            status=DecisionStatus.INSUFFICIENT_EVIDENCE,
            strongest_support=_support_summary(evidence, leader),
            unresolved_questions=_unresolved(state, evidence, subject_id, leader),
            best_next_photo=_next_photo(state, ctx, reranked, leader, tier, Confidence.LOW),
            arbiter_used=state.arbiter_used,
            user_claim_verdict=verdict,
            evidence_tier=int(tier),
        )

    resolution = selected.resolution
    confidence = resolve_confidence(state, subject_id, leader, card, evidence, tier)
    verdict = rule_on_user_claim(state, ctx, subject_id, reranked, evidence, selected.taxon_id)
    return FinalDecision(
        subject_id=subject_id,
        selected_taxon=selected.taxon_id,
        selected_taxon_display_name=selected.display_name,
        resolution=resolution,
        confidence=confidence,
        status=decide_status(
            state,
            ctx,
            subject_id,
            evidence,
            resolution,
            confidence,
            selected.taxon_id,
        ),
        strongest_support=_support_summary(evidence, leader),
        strongest_contradiction=_contradiction_summary(
            state, ctx, subject_id, evidence, selected, leader.taxon
        ),
        nearest_alternative=_nearest_alternative(ctx, reranked.runner_up, resolution, selected),
        unresolved_questions=_unresolved(state, evidence, subject_id, leader),
        best_next_photo=_next_photo(state, ctx, reranked, leader, tier, confidence),
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
