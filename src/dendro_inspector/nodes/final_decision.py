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

Composition has one limit. A reviewer that names a level in ``recommended_resolution`` or
``recommended_confidence`` has stated where its own findings stop; applying those findings
again on top of the recommendation charges the same correction twice, and three reviewers
writing up one overclaim charge it three times. The recommendation is therefore a floor for
model-raised findings — never for deterministic ones, which a model must not be able to
waive by recommending a comfortable number.
"""

from __future__ import annotations

from dendro_inspector.graph.executor import NodeContext
from dendro_inspector.graph.state import GraphState
from dendro_inspector.knowledge.candidate_validation import (
    candidate_ranking_signature,
    candidate_support_tier,
)
from dendro_inspector.knowledge.comparison_cards import (
    drop_resolved_photos,
    follow_up_photos,
    photo_bindings,
)
from dendro_inspector.knowledge.evidence_hierarchy import (
    EvidenceTier,
    bark_only,
    confidence_band,
    confidence_ceiling,
    full_positive_observations_for,
    resolution_ceiling,
)
from dendro_inspector.knowledge.taxon_cards import card_value_vocabulary, match_card
from dendro_inspector.nodes.photo_planner import (
    attachment_request,
    choose_request,
    effective_object_type,
)
from dendro_inspector.schemas.candidates import Candidate, CandidateSet, SupportStrength
from dendro_inspector.schemas.decisions import (
    AuthorityCheckStatus,
    ConfidenceStep,
    ConfidenceStepSource,
    DecisionDerivation,
    DecisionStatus,
    FinalDecision,
    PhotoRequest,
    RerankSource,
    ResolutionBound,
    ResolutionBoundSource,
    UserClaimVerdict,
)
from dendro_inspector.schemas.evidence import EvidencePacket
from dendro_inspector.schemas.input import DeclaredObjectType
from dendro_inspector.schemas.reviews import (
    FindingCategory,
    FindingOrigin,
    RequiredAction,
    ReviewFinding,
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

#: Stated on a verdict whose evidence world was narrowed by the attachment authority gate.
_ATTACHMENT_UNRESOLVED = (
    "The verdict changes when detachable evidence is demoted; confirm that evidence is "
    "continuously attached to this subject before making a stronger claim."
)


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


def _findings_for(state: GraphState, subject_id: str) -> tuple[ReviewFinding, ...]:
    """Accepted findings in the order their confidence operations are composed."""
    return tuple(
        finding
        for synthesis in _syntheses(state)
        for finding in synthesis.accepted_findings
        if finding.subject_id in (None, subject_id)
    )


def _deterministic_actions_for(state: GraphState, subject_id: str) -> tuple[RequiredAction, ...]:
    """Actions the code raised against itself, which a model's recommendation cannot waive."""
    return tuple(
        finding.required_action
        for synthesis in _syntheses(state)
        for finding in synthesis.accepted_findings
        if finding.subject_id in (None, subject_id)
        and finding.origin is FindingOrigin.DETERMINISTIC
    )


def _single_admitted_rerank(
    synthesis: ReviewSynthesis,
    subject_id: str,
) -> tuple[CandidateSet, str] | None:
    admitted = tuple(
        rerank
        for rerank in synthesis.admitted_reranks
        if rerank.candidate_set.subject_id == subject_id
    )
    if not admitted:
        return None
    signatures = {candidate_ranking_signature(rerank.candidate_set) for rerank in admitted}
    if len(signatures) != 1:
        return None
    return admitted[0].candidate_set, admitted[0].finding_id


def _apply_reranking_with_source(
    state: GraphState,
    candidate_set: CandidateSet,
) -> tuple[CandidateSet, RerankSource, str | None]:
    """Return the effective ranking and the exact finding that supplied it."""
    passes: tuple[tuple[RerankSource, ReviewSynthesis | None], ...] = (
        ("arbiter", state.arbiter_synthesis),
        ("internal", state.synthesis),
    )
    for source, synthesis in passes:
        if synthesis is None:
            continue
        for_subject = tuple(
            rerank
            for rerank in synthesis.admitted_reranks
            if rerank.candidate_set.subject_id == candidate_set.subject_id
        )
        if not for_subject:
            continue
        selected = _single_admitted_rerank(synthesis, candidate_set.subject_id)
        if selected is None:
            return candidate_set, "none", None
        ranking, finding_id = selected
        return ranking, source, finding_id
    return candidate_set, "none", None


def apply_reranking(state: GraphState, candidate_set: CandidateSet) -> CandidateSet:
    """Consume finding-bound validated reranks only, preferring an unambiguous arbiter."""
    return _apply_reranking_with_source(state, candidate_set)[0]


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
    contradicted = card is not None and match_card(card, evidence, subject_id).is_disqualified

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


def _broadest_recommendation(state: GraphState) -> Resolution | None:
    """The broadest level any reviewer explicitly recommended, across both passes."""
    if state.abstained:
        # The abstain node stores its composed bound in ``resolution_delta``. Once that
        # happens the value is no longer attributable to a reviewer recommendation.
        return None
    recommendations = [
        synthesis.resolution_delta
        for synthesis in _syntheses(state)
        if synthesis.resolution_delta is not None
    ]
    return min(recommendations, key=resolution_rank) if recommendations else None


def _lowest_recommendation(state: GraphState) -> Confidence | None:
    """The lowest confidence any reviewer explicitly recommended, across both passes."""
    if state.abstained:
        return None
    recommendations = [
        synthesis.confidence_delta
        for synthesis in _syntheses(state)
        if synthesis.confidence_delta is not None
    ]
    return min(recommendations, key=confidence_rank) if recommendations else None


def resolve_resolution(
    state: GraphState,
    subject_id: str,
    leader: Candidate,
    card: TaxonCard | None,
    tier: EvidenceTier,
) -> tuple[Resolution, tuple[ResolutionBound, ...], ResolutionBoundSource, bool]:
    """Compose every upper bound first, then apply at most one explicit downgrade."""
    recommended = _broadest_recommendation(state)
    bounds = [
        ResolutionBound(source="proposed", value=leader.resolution),
        ResolutionBound(source="card_cap", value=cap_resolution(leader.resolution, card)),
        ResolutionBound(source="tier_ceiling", value=resolution_ceiling(tier)),
    ]
    if recommended is not None:
        bounds.append(ResolutionBound(source="reviewer_recommendation", value=recommended))
    if state.abstained and state.synthesis is not None:
        abstention_bound = state.synthesis.resolution_delta
        if abstention_bound is not None:
            bounds.append(ResolutionBound(source="abstention", value=abstention_bound))

    binding = min(bounds, key=lambda bound: resolution_rank(bound.value))
    resolution = binding.value
    already_broadened = resolution_rank(resolution) < resolution_rank(leader.resolution)

    # A reviewer that names a level has said where to stop. Reviewers who write "species
    # overreaches, genus is the highest defensible level" file a lower-resolution finding to
    # say so, and applying that finding on top of the genus they asked for lands on family —
    # one step below the answer every reviewer recommended. The recommendation is therefore a
    # floor as well as a ceiling, for findings the models raised.
    honoured = recommended is not None and resolution_rank(resolution) <= resolution_rank(
        recommended
    )
    actions = (
        _deterministic_actions_for(state, subject_id)
        if honoured
        else _actions_for(state, subject_id)
    )

    # A lower-resolution finding commonly records the same overclaim already represented by
    # a card, evidence, or synthesis bound. Do not apply the same correction twice.
    action_applied = not already_broadened and RequiredAction.LOWER_RESOLUTION in actions
    if action_applied:
        resolution = lower_resolution(resolution)
    return resolution, tuple(bounds), binding.source, action_applied


def resolve_identity(card: TaxonCard | None, resolution: Resolution) -> TaxonIdentity | None:
    """Select a declared identity at or broader than the composed resolution bound."""
    if card is None:
        return None
    return card.identity_at_or_broader(resolution)


def _nearest_alternative(
    ctx: NodeContext,
    contenders: tuple[Candidate, ...],
    resolution: Resolution,
    selected: TaxonIdentity,
) -> str | None:
    """The best-ranked contender that is still a different answer at the selected level.

    Stopping at the runner-up loses the alternative whenever the top two candidates are two
    species of one genus: at genus they resolve to the same identity, so the answer becomes
    "no alternative recorded" while the look-alike findings are, at that very moment, naming
    one. Rank order is preserved — this reaches past a candidate that collapsed into the
    verdict, not past one that lost.
    """
    for contender in contenders:
        alternative = resolve_identity(ctx.knowledge.try_taxon(contender.taxon), resolution)
        if alternative is None or alternative.resolution is not resolution:
            continue
        if alternative.taxon_id == selected.taxon_id:
            continue
        return alternative.taxon_id
    return None


def resolve_confidence(
    state: GraphState,
    subject_id: str,
    leader: Candidate,
    card: TaxonCard | None,
    evidence: EvidencePacket,
    tier: EvidenceTier,
) -> tuple[Confidence, tuple[ConfidenceStep, ...]]:
    """Compose the confidence band and the ordered ledger of every step considered.

    The ledger records the steps that were skipped as well as the ones that bit. A verdict
    that arrives at ``low`` because one guardrail fired reads identically to one that arrived
    there because four reviewers each charged a step, and telling those apart afterwards is
    the whole reason the record exists.
    """
    confidence = _SCORE_TO_CONFIDENCE[leader.score]
    steps: list[ConfidenceStep] = [
        ConfidenceStep(source="seed", before=confidence, after=confidence, applied=True)
    ]

    def step(
        source: ConfidenceStepSource,
        before: Confidence,
        after: Confidence,
        *,
        applied: bool,
        finding_id: str | None = None,
    ) -> None:
        steps.append(
            ConfidenceStep(
                source=source,
                finding_id=finding_id,
                before=before,
                after=after,
                applied=applied,
            )
        )

    # The evidence hierarchy ceiling comes first and is not negotiable. Bark caps at low
    # however characteristic it looks — FAILURE 8.
    tier_cap = confidence_ceiling(tier)
    capped = confidence_rank(tier_cap) < confidence_rank(confidence)
    before = confidence
    if capped:
        confidence = tier_cap
    step("tier_cap", before, confidence, applied=capped)

    if card is not None:
        match = match_card(card, evidence, subject_id)
        short = bool(match.missing_for_high_confidence) and confidence is Confidence.HIGH
        before = confidence
        if short:
            confidence = Confidence.MEDIUM
        step("requirement_cap", before, confidence, applied=short)

    recommended = _lowest_recommendation(state)
    if recommended is not None:
        lowers = confidence_rank(recommended) < confidence_rank(confidence)
        before = confidence
        if lowers:
            confidence = recommended
        step("reviewer_recommendation", before, confidence, applied=lowers)

    # Each accepted finding costs a full step, and three reviewers writing up the same
    # overclaim cost three — which is how a claim the reviewers themselves called `high`
    # arrives as `low`. A model's own recommendation is the floor for the findings that
    # model raised; the deterministic guardrails keep biting past it, because a model must
    # never be able to waive them by recommending a comfortable number.
    findings = _findings_for(state, subject_id)
    for finding in findings:
        if finding.required_action is not RequiredAction.LOWER_CONFIDENCE:
            continue
        if finding.origin is FindingOrigin.DETERMINISTIC:
            continue
        floored = recommended is not None and confidence_rank(confidence) <= confidence_rank(
            recommended
        )
        before = confidence
        if not floored:
            confidence = lower_confidence(confidence)
        step(
            "model_finding",
            before,
            confidence,
            applied=not floored,
            finding_id=finding.finding_id,
        )

    for finding in findings:
        if finding.required_action is not RequiredAction.LOWER_CONFIDENCE:
            continue
        if finding.origin is not FindingOrigin.DETERMINISTIC:
            continue
        before = confidence
        confidence = lower_confidence(confidence)
        step(
            "deterministic_finding",
            before,
            confidence,
            applied=True,
            finding_id=finding.finding_id,
        )

    if state.abstained:
        before = confidence
        confidence = Confidence.LOW
        step("abstention", before, confidence, applied=True)
    return confidence, tuple(steps)


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
    if card is not None and match_card(card, evidence, subject_id).is_disqualified:
        return DecisionStatus.CONFLICTING_EVIDENCE
    if _unsupported_user_claim(state, evidence, subject_id):
        return DecisionStatus.UNSUPPORTED_USER_CLAIM
    if confidence is Confidence.HIGH:
        return DecisionStatus.IDENTIFIED
    return DecisionStatus.PROBABLE


def _support_summary(evidence: EvidencePacket, candidate: Candidate) -> tuple[str, ...]:
    """Every surviving supporting observation, in the order the candidate cited them.

    Validation has already removed the ids that do not match this candidate's card, so what
    reaches here is the evidence the verdict actually rests on — all of it, not its first
    entry.
    """
    by_id = {o.observation_id: o for o in evidence.observations}
    return tuple(
        f"{observation.feature} = {observation.value} ({observation.reliability.value} reliability)"
        for evidence_id in candidate.supporting_evidence_ids
        if (observation := by_id.get(evidence_id)) is not None
    )


def _contradiction_summary(
    state: GraphState,
    ctx: NodeContext,
    subject_id: str,
    evidence: EvidencePacket,
    selected: TaxonIdentity,
    source_taxon: str,
) -> str | None:
    """Summarise contradictions against the selected identity, not its narrower source card.

    A contradiction is evidence that argues *against* this identity. Missing decisive
    features, regional assumptions and generic uncertainty are none of those — they are
    reasons the answer is not stronger, not reasons the alternative was ruled out. The
    response composer prints this line under "why not the nearest alternative", so any
    accepted finding that reached here became a fabricated rebuttal of a taxon nobody
    had argued against.
    """
    card = ctx.knowledge.try_taxon(selected.taxon_id)
    if card is not None:
        by_id = {observation.observation_id: observation for observation in evidence.observations}
        for evidence_id in match_card(card, evidence, subject_id).disqualifying_hits:
            observation = by_id.get(evidence_id)
            if observation is not None:
                return f"{observation.feature} = {observation.value}"

    # A contradiction raised against a narrower source card says nothing about the broader
    # identity actually selected, so it is not carried across a collapse.
    if selected.taxon_id != source_taxon:
        return None
    for synthesis in _syntheses(state):
        for finding in synthesis.accepted_findings:
            if finding.category is not FindingCategory.BOTANICAL_CONTRADICTION:
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
    evidence: EvidencePacket,
    candidate_set: CandidateSet,
    leader: Candidate,
    tier: EvidenceTier,
    confidence: Confidence,
) -> PhotoRequest | None:
    """The one photograph that would most improve the result — or none, honestly.

    At the decisive tier with high confidence there is nothing left to ask for. Requesting
    a fruit photograph when the fruit is already in the frame is the kind of reflexive
    hedging that teaches people to ignore the request entirely — and so is asking for a
    second bark macro of bark this run has already read, which is why the comparison's
    declared discriminators are filtered by what the subject already resolves.
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

    authority_first = attachment_request(state, candidate_set.subject_id)
    if authority_first is not None:
        return authority_first

    taxa = frozenset(candidate.taxon for candidate in candidate_set.ordered)
    candidate_cards = tuple(
        card for taxon in taxa if (card := ctx.knowledge.try_taxon(taxon)) is not None
    )
    vocabulary = card_value_vocabulary(candidate_cards)
    resolved = frozenset(
        observation.feature
        for observation in full_positive_observations_for(evidence, candidate_set.subject_id)
        if observation.value in vocabulary.get(observation.feature, frozenset())
    )
    comparison_cards = ctx.knowledge.comparisons_for(taxa)
    photos = follow_up_photos(comparison_cards, taxa, resolved)
    comparison_request = bool(photos)
    if not photos:
        # No look-alike group applies, so the leader's own follow-up list is all there is.
        # It is a flat list of targets, and the only declared statement of what each target
        # resolves lives on the comparison cards' discriminators — enough to drop a request
        # whose every usable feature this subject has already answered.
        card = ctx.knowledge.try_taxon(leader.taxon)
        usable = frozenset(card_value_vocabulary((card,))) if card is not None else frozenset()
        photos = drop_resolved_photos(
            ctx.knowledge.follow_up_for((leader.taxon,)),
            photo_bindings(ctx.knowledge.comparisons(), usable),
            resolved,
        )
    if not photos:
        return None
    return PhotoRequest(
        target=photos[0],
        reason=(
            "Would resolve an unresolved discriminator among the leading candidates."
            if comparison_request
            else "Would add missing organ-level evidence for the leading candidate."
        ),
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


def decide_subject_base(
    state: GraphState,
    ctx: NodeContext,
    candidate_set: CandidateSet,
    *,
    already_reranked: bool = False,
    record: bool = True,
) -> FinalDecision:
    """Compose one subject's verdict, recording how it was composed unless it is a probe.

    ``record`` is off for the attachment counterfactual, which asks what a different evidence
    world would have said. That world's arithmetic is real but it is not this verdict's, and
    a trace that carried it would answer "how was this composed?" with someone else's answer.
    """
    evidence = state.evidence
    if evidence is None:  # pragma: no cover - routing guarantees this; loud if it ever breaks
        msg = "final decision reached without an evidence packet"
        raise RuntimeError(msg)

    reranked, rerank_source, rerank_finding_id = (
        (candidate_set, "none", None)
        if already_reranked
        else _apply_reranking_with_source(state, candidate_set)
    )
    leader = reranked.leader
    subject_id = reranked.subject_id

    def keep(derivation: DecisionDerivation) -> None:
        if record:
            ctx.recorder.record_derivation(derivation)

    if leader is None:
        keep(
            DecisionDerivation.terminal(subject_id).model_copy(
                update={
                    "rerank_source": rerank_source,
                    "rerank_finding_id": rerank_finding_id,
                }
            )
        )
        return FinalDecision(
            subject_id=subject_id,
            status=DecisionStatus.INSUFFICIENT_EVIDENCE,
            best_next_photo=choose_request(state, ctx, subject_id),
            arbiter_used=state.arbiter_used,
        )

    card = ctx.knowledge.try_taxon(leader.taxon)
    tier = candidate_support_tier(leader, evidence, subject_id)
    resolution_bound, bounds, binding_source, action_applied = resolve_resolution(
        state, subject_id, leader, card, tier
    )
    derivation = DecisionDerivation(
        subject_id=subject_id,
        proposed_strength=leader.score,
        effective_strength=leader.score,
        resolution_bounds=bounds,
        resolution_binding_source=binding_source,
        resolution_action_applied=action_applied,
        confidence_steps=(
            ConfidenceStep(
                source="seed",
                before=_SCORE_TO_CONFIDENCE[leader.score],
                after=_SCORE_TO_CONFIDENCE[leader.score],
                applied=True,
            ),
        ),
        rerank_source=rerank_source,
        rerank_finding_id=rerank_finding_id,
    )

    selected = resolve_identity(card, resolution_bound)
    if selected is None:
        # The bound was composed; no declared identity exists at or broader than it. The
        # confidence ledger stops there, and the verdict is floored rather than composed.
        seed = _SCORE_TO_CONFIDENCE[leader.score]
        keep(
            derivation.model_copy(
                update={
                    "confidence_steps": (
                        *derivation.confidence_steps,
                        ConfidenceStep(
                            source="no_identity",
                            before=seed,
                            after=Confidence.LOW,
                            applied=True,
                        ),
                    )
                }
            )
        )
        verdict = rule_on_user_claim(state, ctx, subject_id, reranked, evidence, None)
        return FinalDecision(
            subject_id=subject_id,
            status=DecisionStatus.INSUFFICIENT_EVIDENCE,
            supporting_evidence=_support_summary(evidence, leader),
            unresolved_questions=_unresolved(state, evidence, subject_id, leader),
            best_next_photo=_next_photo(
                state, ctx, evidence, reranked, leader, tier, Confidence.LOW
            ),
            arbiter_used=state.arbiter_used,
            user_claim_verdict=verdict,
            evidence_tier=int(tier),
        )

    resolution = selected.resolution
    confidence, confidence_steps = resolve_confidence(
        state, subject_id, leader, card, evidence, tier
    )
    keep(derivation.model_copy(update={"confidence_steps": confidence_steps}))
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
        supporting_evidence=_support_summary(evidence, leader),
        strongest_contradiction=_contradiction_summary(
            state, ctx, subject_id, evidence, selected, leader.taxon
        ),
        nearest_alternative=_nearest_alternative(ctx, reranked.ordered[1:], resolution, selected),
        unresolved_questions=_unresolved(state, evidence, subject_id, leader),
        best_next_photo=_next_photo(state, ctx, evidence, reranked, leader, tier, confidence),
        arbiter_used=state.arbiter_used,
        user_claim_verdict=verdict,
        evidence_tier=int(tier),
        confidence_band=confidence_band(confidence, tier),
    )


def decide_subject(
    state: GraphState, ctx: NodeContext, candidate_set: CandidateSet
) -> FinalDecision:
    """Return the deterministic verdict, carrying this subject's authority record with it.

    The attachment counterfactual itself already ran, before the reviewers, in
    :mod:`dendro_inspector.nodes.attachment_authority_gate`. What is left here is to state
    the finding on the verdict: which observations were the hinge, whether the conservative
    evidence world is the one this answer was computed from, and what the alternate world
    would have said. The claim is not recomputed from that record — it cannot be, because
    the record describes the world every model downstream has already been reasoning in.
    """
    decision = decide_subject_base(state, ctx, candidate_set)
    decision = decision.model_copy(update={"abstained": state.abstained})
    check = state.authority_check_for(candidate_set.subject_id)
    if check is None or check.status is not AuthorityCheckStatus.SENSITIVE:
        return decision.model_copy(
            update={
                "authority_check_status": (
                    check.status if check is not None else AuthorityCheckStatus.NOT_APPLICABLE
                )
            }
        )

    counterfactual = check.counterfactual_outcome
    updates: dict[str, object] = {
        "authority_check_status": check.status,
        "critical_evidence_ids": check.critical_evidence_ids,
        "authority_policy_applied": check.policy_applied,
        "counterfactual_status": counterfactual.status if counterfactual else None,
        "counterfactual_taxon": counterfactual.taxon if counterfactual else None,
        "counterfactual_resolution": counterfactual.resolution if counterfactual else None,
        "counterfactual_confidence": counterfactual.confidence if counterfactual else None,
        "counterfactual_attachment": check.counterfactual_attachment,
    }
    if not check.policy_applied:
        return decision.model_copy(update=updates)

    updates["unresolved_questions"] = tuple(
        dict.fromkeys((*decision.unresolved_questions, _ATTACHMENT_UNRESOLVED))
    )[:8]
    authority_first = attachment_request(
        state,
        candidate_set.subject_id,
        critical_evidence_ids=check.risk_evidence_ids or check.critical_evidence_ids,
    )
    if authority_first is not None:
        updates["best_next_photo"] = authority_first
    return decision.model_copy(update=updates)


async def run(state: GraphState, ctx: NodeContext) -> GraphState:
    if state.decisions:
        # The photo-planner path already produced terminal decisions.
        return state
    if state.evidence is None or not state.candidate_sets:
        ctx.recorder.record_derivation(DecisionDerivation.terminal("case"))
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
