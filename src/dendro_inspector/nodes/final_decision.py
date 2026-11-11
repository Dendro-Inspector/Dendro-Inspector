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
writing up one overclaim charge it three times. The recommendation is therefore a floor
only for its own accepted model findings on the same subject. It cannot waive another
review's findings or deterministic checks. A bare recommendation supplies only a cap.
"""

from __future__ import annotations

import re

from dendro_inspector.graph.executor import NodeContext
from dendro_inspector.graph.state import GraphState
from dendro_inspector.knowledge.candidate_validation import (
    candidate_ranking_signature,
    candidate_support_tier,
)
from dendro_inspector.knowledge.comparison_cards import (
    deprioritise_saturated_photos,
    drop_resolved_photos,
    follow_up_photos,
    photo_bindings,
)
from dendro_inspector.knowledge.evidence_hierarchy import (
    EvidenceTier,
    bark_only,
    confidence_band,
    confidence_ceiling,
    decisive_observations_for,
    family_of,
    project_evidence,
    resolution_ceiling,
    resolve_evidence_observations,
    tier_of_feature,
)
from dendro_inspector.knowledge.loader import KnowledgeBase
from dendro_inspector.knowledge.taxon_cards import (
    card_value_vocabulary,
    confidence_exception_for,
    match_card,
)
from dendro_inspector.nodes.abstain import settle_unverified_abstention
from dendro_inspector.nodes.photo_planner import (
    attachment_request,
    choose_request,
    coverage_feature_text,
    effective_object_type,
    planned_attachment_request,
)
from dendro_inspector.schemas.candidates import (
    STRENGTH_CONFIDENCE_CEILING,
    Candidate,
    CandidateSet,
    OpenWorldCandidate,
    SupportStrength,
)
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
from dendro_inspector.schemas.evidence import EvidencePacket, is_positive_reading
from dendro_inspector.schemas.input import DeclaredObjectType
from dendro_inspector.schemas.reference import (
    ReferenceStatus,
    local_reference_status,
    scientific_display_name,
)
from dendro_inspector.schemas.reviews import (
    AdmittedRecommendation,
    FindingCategory,
    FindingOrigin,
    RequiredAction,
    ReviewFinding,
    ReviewSynthesis,
    Severity,
)
from dendro_inspector.schemas.taxon import (
    Confidence,
    ExceptionCeiling,
    Resolution,
    TaxonCard,
    TaxonIdentity,
    confidence_rank,
    lower_confidence,
    lower_resolution,
    resolution_rank,
)

NODE = "final_decision"

#: How an unmet decisive requirement is described to the reader.
#:
#: Deliberately not "not visible". That phrasing is a claim about the photograph, and it was
#: wrong whenever the feature was in frame but failed a trust gate — the same answer would
#: quote that observation as its support two lines earlier. "Not established" describes what
#: the gate actually decided, and does not send the user to re-shoot a photograph that
#: already showed the thing.
MISSING_DECISIVE_PHRASE = "Decisive feature not established"

#: Candidate support strength maps to a confidence ceiling, never to certainty.
_SCORE_TO_CONFIDENCE: dict[SupportStrength, Confidence] = STRENGTH_CONFIDENCE_CEILING

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


def open_world_text(candidates: tuple[OpenWorldCandidate, ...]) -> tuple[str, ...]:
    """One reader-facing line per hypothesis outside the admitted set.

    Each line says what the hypothesis is *not* as plainly as what it is — not the verdict,
    and capped by what the photograph showed whatever any reference said — and whether the
    name is missing from this knowledge base or merely not a card id.
    """
    lines = []
    for candidate in sorted(candidates, key=lambda item: item.proposed_rank):
        name = scientific_display_name(candidate.taxon)
        where = (
            f"Hypothesis outside this knowledge base: {name}"
            if candidate.uncovered
            else (
                f"Hypothesis named {name}, which this knowledge base declares only within "
                f"{', '.join(candidate.covered_by)}"
            )
        )
        lines.append(
            f"{where} (not identified; this evidence supports at most "
            f"{candidate.resolution.value}, image confidence at most "
            f"{candidate.image_confidence_ceiling.value}; reference status: "
            f"{candidate.reference_status.value})"
        )
    return tuple(lines)


def _competing_text(candidates: tuple[OpenWorldCandidate, ...]) -> tuple[str, ...]:
    return tuple(
        f"Competing hypothesis: the model ranked {scientific_display_name(candidate.taxon)} "
        "above this verdict. No card describes it, so it could not be evaluated, and this "
        "verdict stands on card-validated evidence alone."
        for candidate in candidates
    )


def rests_on_uncovered_evidence(state: GraphState, candidate: OpenWorldCandidate) -> bool:
    """Whether this hypothesis is supported by evidence no card can represent.

    The test that keeps "missing taxonomy" an evidence-side finding. A model can name an
    off-card taxon for any photograph; if naming one were enough, a weak frame plus an
    inventive model would read as a knowledge-base limit — the reverse of the error the
    coverage status exists to prevent. What decides is the coverage measurement: some of
    the observations this hypothesis rests on are ones no card describes.
    """
    quality, evidence = state.quality, state.evidence
    coverage = quality.knowledge_coverage if quality is not None else None
    if coverage is None or evidence is None or not candidate.uncovered:
        return False
    uncovered = set(coverage.potential_gap_evidence_ids)
    return any(
        source.observation_id in uncovered
        for evidence_id in candidate.supporting_evidence_ids
        for source in resolve_evidence_observations(evidence, evidence_id, candidate.subject_id)
    )


def _proposed_rank(state: GraphState, subject_id: str, taxon: str) -> int | None:
    proposal = state.proposed_candidates_for(subject_id)
    if proposal is None:
        return None
    return next(
        (candidate.rank for candidate in proposal.candidates if candidate.taxon == taxon), None
    )


def _genus_of(card: TaxonCard) -> str | None:
    identity = card.identity_at_or_broader(Resolution.GENUS)
    return identity.taxon_id if identity and identity.resolution is Resolution.GENUS else None


def _observations_behind(
    evidence: EvidencePacket, subject_id: str, evidence_ids: tuple[str, ...]
) -> frozenset[str]:
    return frozenset(
        source.observation_id
        for evidence_id in evidence_ids
        for source in resolve_evidence_observations(evidence, evidence_id, subject_id)
    )


def open_world_contest(
    state: GraphState,
    subject_id: str,
    leader: Candidate,
    card: TaxonCard | None,
    tier: EvidenceTier,
) -> tuple[tuple[OpenWorldCandidate, ...], tuple[OpenWorldCandidate, ...]]:
    """Split uncovered hypotheses the model ranked above the leader: substituting, competing.

    A model's rank on its own never changes a validated verdict, and neither does a name
    nobody has checked. An outranking hypothesis *substitutes* — caps the verdict at genus —
    only when three further things hold, none of them the model's say-so:

    * it is congeneric with the leader: the leader card's declared genus identity is the
      first word of its binomial. Species of one genus are the look-alikes the leader's
      species claim could silently stand in for;
    * it cites the same observation the leader's claim rests on at the leader's strongest
      tier. Then that evidence does not discriminate between the two, and the card cannot
      tell, because only one of them has a card;
    * it is ``externally_verified``: an authoritative source this system queried itself
      accepts the name and documents a feature observed here. A shared observation proves
      both hypotheses *cited* it, not that both *fit* it — a model can invent
      ``juglans_fictitia`` and cite the walnut's own leaf.

    Every other outranking hypothesis *competes*: reported as such, and flagged on the
    verdict, but without moving it. Abstaining would hand the model's ranking exactly the
    power it is not trusted with; keeping the leader's genus while the model led with
    another genus is what card-validated evidence supports, stated beside the alternative.

    Ranks are the model's own, before admission rebuilt them. A leader the proposal never
    named can only arrive by an admitted rerank, and is treated as ranked last.
    """
    evidence = state.evidence
    proposal = state.proposed_candidates_for(subject_id)
    leader_rank = next(
        (c.rank for c in (proposal.candidates if proposal else ()) if c.taxon == leader.taxon),
        None,
    )
    outranking = tuple(
        candidate
        for candidate in state.open_world_for(subject_id)
        if candidate.uncovered and (leader_rank is None or candidate.proposed_rank < leader_rank)
    )
    if not outranking or evidence is None:
        return (), outranking

    genus = _genus_of(card) if card is not None else None
    decisive = _observations_behind(
        evidence,
        subject_id,
        tuple(
            evidence_id
            for evidence_id in leader.supporting_evidence_ids
            if project_evidence(evidence, evidence_id, subject_id).tier == tier
        ),
    )
    substituting = tuple(
        candidate
        for candidate in outranking
        if genus is not None
        and candidate.reference_status is ReferenceStatus.EXTERNALLY_VERIFIED
        and candidate.taxon.split("_")[0] == genus
        and decisive & _observations_behind(evidence, subject_id, candidate.supporting_evidence_ids)
    )
    competing = tuple(candidate for candidate in outranking if candidate not in substituting)
    return substituting, competing


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


#: Words that turn the taxon token following them into a denial rather than a claim. Full
#: negation semantics — "it is *not* an oak" as a testable statement the system could go on
#: to check — are a separate decision; this only stops the denial being read as the claim.
_NEGATIONS: frozenset[str] = frozenset({"не", "ні", "not", "no"})

#: Below this length a name matches only as a whole token. "a" is inside twenty-two of the
#: twenty-five cards and "дуб" is inside "дубок"; "quercus" is inside nothing by accident.
_SUBSTRING_MIN = 4


def _claim_words(claim: str) -> tuple[str, ...]:
    """Split a free-text claim into normalised word tokens, in the order they were written."""
    words = (normalise_claim(word) for word in re.findall(r"\w+", claim))
    return tuple(word for word in words if word)


def _asserted_words(claim: str) -> tuple[str, ...]:
    """The words the user asserted, with each negation and the word it denies removed."""
    kept: list[str] = []
    denied = False
    for word in _claim_words(claim):
        if word in _NEGATIONS:
            denied = True
            continue
        if denied:
            denied = False
            continue
        kept.append(word)
    return tuple(kept)


def claim_is_wholly_negated(claim: str) -> bool:
    """Whether the user named something and then took every name back."""
    return bool(_claim_words(claim)) and not _asserted_words(claim)


def _card_names(taxon_id: str, card: TaxonCard) -> tuple[tuple[str, bool], ...]:
    """Each comparable form of a card's name, with whether it may match as a substring.

    The display name is a composite label: "Quercus (дуб)" reduces to a string nobody types,
    and letting it match loosely makes it the longest match for any claim naming either half.
    It is matched whole or not at all.
    """
    forms = (
        (normalise_claim(taxon_id), True),
        (normalise_claim(card.display_name), False),
        *((normalise_claim(alias), True) for alias in card.aliases),
    )
    seen: dict[str, bool] = {}
    for form, loose in forms:
        if form:
            seen[form] = seen.get(form, False) or loose
    return tuple(seen.items())


def _match_weight(word: str, names: tuple[tuple[str, bool], ...]) -> int:
    """Length of the longest card name this word matches, or zero for no match.

    Substring matching survives for long names, because people write "сосни" and "quercus
    robur" and mean the card; it is withdrawn when either side is short, because that is how
    a single letter came to match most of the pack.
    """
    best = 0
    for name, loose in names:
        if word == name or (
            loose
            and len(name) >= _SUBSTRING_MIN
            and len(word) >= _SUBSTRING_MIN
            and (name in word or word in name)
        ):
            best = max(best, len(name))
    return best


def resolve_user_claim(claim: str, knowledge: KnowledgeBase) -> tuple[str, ...]:
    """Every taxon the user's claim names, longest matching name first.

    A claim is a disjunction, not a lookup. "дуб або ясен" names two taxa and the user is
    right if either is the answer; resolving it to whichever card the catalogue happens to
    list first makes the verdict an accident of iteration order.
    """
    weights: dict[str, int] = {}
    for word in _asserted_words(claim):
        for taxon_id in knowledge.available_taxon_ids():
            card = knowledge.try_taxon(taxon_id)
            if card is None:
                continue
            weight = _match_weight(word, _card_names(taxon_id, card))
            if weight > weights.get(taxon_id, 0):
                weights[taxon_id] = weight
    return tuple(sorted(weights, key=lambda taxon_id: (-weights[taxon_id], taxon_id)))


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

    matched = resolve_user_claim(claim, ctx.knowledge)
    if not matched:
        if claim_is_wholly_negated(claim):
            # They said what it is not. That is not a version this system can rule on, and
            # reading the denial as the claim would rule against the taxon they excluded.
            ctx.recorder.record_negated_claim()
        # We do not have a card for what they said. That is our gap, not their error.
        return UserClaimVerdict.POSSIBLE

    # A hedge is not a weaker claim, it is several claims. Ruling on the most favourable
    # member is the only reading that does not punish a user for being careful.
    return max(
        (
            _rule_on_one_taxon(
                state, ctx, subject_id, candidate_set, evidence, selected_taxon, taxon_id
            )
            for taxon_id in matched
        ),
        key=_claim_favourability,
    )


#: Most favourable first. A disjunction is ruled on by its best member.
_CLAIM_FAVOURABILITY: dict[UserClaimVerdict, int] = {
    UserClaimVerdict.ACCEPTED: 3,
    UserClaimVerdict.POSSIBLE: 2,
    UserClaimVerdict.DOUBTFUL: 1,
    UserClaimVerdict.REJECTED: 0,
}


def _claim_favourability(verdict: UserClaimVerdict) -> int:
    return _CLAIM_FAVOURABILITY.get(verdict, 0)


def _rule_on_one_taxon(
    state: GraphState,
    ctx: NodeContext,
    subject_id: str,
    candidate_set: CandidateSet,
    evidence: EvidencePacket,
    selected_taxon: str | None,
    matched: str,
) -> UserClaimVerdict:
    """Rule on one taxon the claim named. The restraint clauses, in the prompt's order."""
    if matched == selected_taxon:
        return UserClaimVerdict.ACCEPTED

    in_candidates = any(candidate.taxon == matched for candidate in candidate_set.ordered)
    card = ctx.knowledge.try_taxon(matched)
    contradicted = card is not None and match_card(card, evidence, subject_id).is_disqualified

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


def _recommendations_for(state: GraphState, subject_id: str) -> tuple[AdmittedRecommendation, ...]:
    recommendations: list[AdmittedRecommendation] = []
    for synthesis in _syntheses(state):
        if synthesis.recommendations is not None:
            recommendations.extend(
                item for item in synthesis.recommendations if item.subject_id == subject_id
            )
        elif state.subject_ids == (subject_id,):
            # Released/custom synthesis can still supply conservative single-subject caps.
            # Without a finding binding, a legacy aggregate cannot waive any downgrade.
            recommendations.append(
                AdmittedRecommendation(
                    subject_id=subject_id,
                    resolution=synthesis.resolution_delta,
                    confidence=synthesis.confidence_delta,
                )
            )
    return tuple(recommendations)


def _broadest_recommendation(
    state: GraphState, subject_id: str, finding: ReviewFinding | None = None
) -> Resolution | None:
    """This subject's cap, or the floor bound to one exact model finding."""
    if state.is_abstained(subject_id):
        # The abstention bound already incorporates this subject's review recommendations.
        return None
    recommendations = [
        item.resolution
        for item in _recommendations_for(state, subject_id)
        if item.resolution is not None and (finding is None or item.finding == finding)
    ]
    return min(recommendations, key=resolution_rank) if recommendations else None


def _lowest_recommendation(
    state: GraphState, subject_id: str, finding: ReviewFinding | None = None
) -> Confidence | None:
    """This subject's cap, or the floor bound to one exact model finding."""
    if state.is_abstained(subject_id):
        return None
    recommendations = [
        item.confidence
        for item in _recommendations_for(state, subject_id)
        if item.confidence is not None and (finding is None or item.finding == finding)
    ]
    return min(recommendations, key=confidence_rank) if recommendations else None


def resolve_resolution(
    state: GraphState,
    subject_id: str,
    leader: Candidate,
    card: TaxonCard | None,
    tier: EvidenceTier,
    *,
    require_discriminating_support: bool = True,
) -> tuple[Resolution, tuple[ResolutionBound, ...], ResolutionBoundSource, bool]:
    """Compose every upper bound first, then apply at most one explicit downgrade.

    ``require_discriminating_support`` is ``GraphConfig.require_discriminating_support``:
    on (the default), a leader admitted on supporting features alone names nothing; off, the
    legacy policy names it at low confidence. Kept switchable for comparative evaluation.
    """
    recommended = _broadest_recommendation(state, subject_id)
    bounds = [
        ResolutionBound(source="proposed", value=leader.resolution),
        ResolutionBound(source="card_cap", value=cap_resolution(leader.resolution, card)),
        ResolutionBound(source="tier_ceiling", value=resolution_ceiling(tier)),
    ]
    if recommended is not None:
        bounds.append(ResolutionBound(source="reviewer_recommendation", value=recommended))
    substituting, _ = open_world_contest(state, subject_id, leader, card, tier)
    if substituting:
        bounds.append(ResolutionBound(source="open_world_outranked", value=Resolution.GENUS))
    evidence = state.evidence
    if (
        require_discriminating_support
        and card is not None
        and evidence is not None
        and not discriminating_support(card, evidence, leader, subject_id)
    ):
        bounds.append(ResolutionBound(source="no_discriminating_support", value=Resolution.UNKNOWN))
    if state.is_abstained(subject_id):
        abstention = state.abstention_for(subject_id)
        abstention_bound = (
            abstention.resolution
            if abstention is not None
            else state.synthesis.resolution_delta
            if state.synthesis is not None and state.subject_ids == (subject_id,)
            else None
        )
        if abstention_bound is not None:
            bounds.append(ResolutionBound(source="abstention", value=abstention_bound))

    binding = min(bounds, key=lambda bound: resolution_rank(bound.value))
    resolution = binding.value
    already_broadened = resolution_rank(resolution) < resolution_rank(leader.resolution)

    # A reviewer that names a level has said where to stop. Reviewers who write "species
    # overreaches, genus is the highest defensible level" file a lower-resolution finding to
    # say so, and applying that finding on top of the genus they asked for lands on family —
    # one step below the answer every reviewer recommended. The recommendation is therefore a
    # floor as well as a ceiling, for that review's own accepted model findings.
    actions = list(_deterministic_actions_for(state, subject_id))
    for finding in _findings_for(state, subject_id):
        if finding.origin is not FindingOrigin.MODEL:
            continue
        own_bound = _broadest_recommendation(state, subject_id, finding)
        if own_bound is None or resolution_rank(resolution) > resolution_rank(own_bound):
            actions.append(finding.required_action)

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


#: What each declared ceiling means on this project's three-valued confidence scale.
#: `VERY_HIGH` is `HIGH` plus the top display band — see `confidence_band`.
_EXCEPTION_CONFIDENCE: dict[ExceptionCeiling, Confidence] = {
    ExceptionCeiling.MEDIUM: Confidence.MEDIUM,
    ExceptionCeiling.HIGH: Confidence.HIGH,
    ExceptionCeiling.VERY_HIGH: Confidence.HIGH,
}


def _reaches_the_top_band(
    card: TaxonCard | None,
    evidence: EvidencePacket,
    subject_id: str,
    resolution: Resolution,
) -> bool:
    """Whether a card-declared exception earns this claim the 95-100 band.

    Recomputed from the same pure function that raised the ceiling rather than carried out
    of `resolve_confidence`, so the band can never disagree with the step that produced it.
    """
    if card is None:
        return False
    hit = confidence_exception_for(card, evidence, subject_id, resolution)
    return hit is not None and hit.exception.ceiling is ExceptionCeiling.VERY_HIGH


#: Distinct, reliably read supporting features a high-confidence claim needs (section 6).
MIN_INDEPENDENT_FEATURES_FOR_HIGH = 2


def independent_support_features(
    leader: Candidate, evidence: EvidencePacket, subject_id: str
) -> frozenset[str]:
    """The distinct features behind a candidate's admitted support, counted once each.

    Independence is by feature, not by observation or by inference: the same feature read
    twice, or an inference restating the observations it rests on, is one piece of evidence
    described more than once. Only readings reliable enough to settle a decisive requirement
    count, and a "could not read it" value counts as nothing.
    """
    inferences = {inference.inference_id: inference for inference in evidence.inferences}
    cited: set[str] = set()
    for evidence_id in leader.supporting_evidence_ids:
        inference = inferences.get(evidence_id)
        if inference is not None:
            cited.update(inference.derived_from)
        else:
            cited.add(evidence_id)
    return frozenset(
        observation.feature
        for observation in decisive_observations_for(evidence, subject_id)
        if observation.observation_id in cited and is_positive_reading(observation.value)
    )


def discriminating_support(
    card: TaxonCard, evidence: EvidencePacket, leader: Candidate, subject_id: str
) -> tuple[str, ...]:
    """The leader's admitted observations its own card calls diagnostic.

    Admission asks whether evidence is *compatible* with a card: one trusted reading of any
    feature the card lists, strong or supporting, opens it. That is the right question for
    keeping a candidate in play and the wrong one for naming it. A card's supporting
    features are, by the card's own declaration, things the taxon shares with others —
    coarse furrowed bark, a crooked trunk, plated bark — and five recorded runs named a
    genus on exactly one of those and nothing else: poplar on three photographs of birch,
    spruce on two of pine bark, sycamore on a third, each at "probable" (see
    ``docs/reviews/EVIDENCE-COVERAGE-2026-11-11.md``).

    The card already draws the line this needs: ``strong_positive_features`` is the card's
    statement of what *distinguishes* the taxon. Evidence that hits one of those
    discriminates; evidence that hits only supporting features is compatible and no more.
    A card-declared confidence exception counts as well, because it names a reading the card
    has already said is diagnostic on its own (white papery birch bark). No weight, score or
    threshold is introduced: the split is the one each card's author already made.

    Returns the discriminating observation ids, in the leader's citation order. Empty means
    the leader may stay a candidate but may not be the answer.
    """
    strong = set(match_card(card, evidence, subject_id).strong_hits)
    cited = tuple(
        dict.fromkeys(
            source.observation_id
            for evidence_id in leader.supporting_evidence_ids
            for source in resolve_evidence_observations(evidence, evidence_id, subject_id)
        )
    )
    hits = tuple(observation_id for observation_id in cited if observation_id in strong)
    if hits:
        return hits
    exception = confidence_exception_for(card, evidence, subject_id, Resolution.FAMILY)
    return exception.evidence_ids if exception is not None else ()


#: Opens the line that discloses a verdict resting on supporting features alone.
NO_DISCRIMINATING_SUPPORT_PHRASE = "Matched only supporting features"


def _no_discriminating_support_text(card: TaxonCard, *, named: bool) -> str:
    diagnostic = sorted({expectation.feature for expectation in card.strong_positive_features})
    wanted = ", ".join(diagnostic) if diagnostic else "a feature its card calls diagnostic"
    outcome = (
        "so this is a low-confidence candidate, not an identification"
        if named
        else "so no taxon is named"
    )
    return (
        f"{NO_DISCRIMINATING_SUPPORT_PHRASE}: {card.display_name} matched only features its "
        f"card lists as supporting. Those are shared with other trees and do not distinguish "
        f"it, {outcome}. Evidence that would: {wanted}."
    )


def _undistinguished_rivals(
    ctx: NodeContext,
    contenders: tuple[Candidate, ...],
    evidence: EvidencePacket,
    subject_id: str,
    resolution: Resolution,
    selected: TaxonIdentity,
) -> tuple[str, ...]:
    """Admitted alternatives at the claimed level that the evidence does not rule out.

    Only asked when the leader itself has no discriminating support: then nothing in the
    photograph prefers it over these, and model rank order is all that separates them.
    """
    rivals = []
    for contender in contenders:
        rival_card = ctx.knowledge.try_taxon(contender.taxon)
        identity = resolve_identity(rival_card, resolution)
        if rival_card is None or identity is None or identity.taxon_id == selected.taxon_id:
            continue
        match = match_card(rival_card, evidence, subject_id)
        if match.has_contradiction or match.contradicts_own_card:
            continue
        rivals.append(identity.display_name)
    return tuple(dict.fromkeys(rivals))


#: Prefix of the unresolved question that explains a withheld top band.
TOP_BAND_WITHHELD_PHRASE = "95-100 withheld"


def _declared_confusion_left_standing(
    ctx: NodeContext,
    card: TaxonCard | None,
    contenders: tuple[Candidate, ...],
    evidence: EvidencePacket,
    subject_id: str,
    resolution: Resolution,
) -> str | None:
    """A card-declared look-alike that survives among this subject's alternatives.

    Section 6 reserves 95-100 for "no serious alternatives", and FAILURE 1 names the one a
    birch-like bark must be checked against: white poplar. A card that declares a common
    confusion has said which alternative is serious. When that taxon is still a supported
    alternative at the claimed level and trusted evidence has not ruled it out, the
    confusion was never resolved, and the top band would claim it was. The claim keeps its
    confidence; only the 95-100 rendering is withheld. Returns the alternative's id, or
    ``None`` when the band may stand.
    """
    if card is None:
        return None
    alternatives = {
        identity.taxon_id
        for contender in contenders
        if (identity := resolve_identity(ctx.knowledge.try_taxon(contender.taxon), resolution))
        is not None
        and identity.resolution is resolution
    }
    decisive_evidence = evidence.model_copy(
        update={"observations": decisive_observations_for(evidence, subject_id)}
    )
    for confusion in card.common_confusions:
        confusion_card = ctx.knowledge.try_taxon(confusion)
        if confusion_card is None:
            continue
        identity = confusion_card.identity_at_or_broader(resolution)
        if identity is None or identity.taxon_id not in alternatives:
            continue
        match = match_card(confusion_card, decisive_evidence, subject_id)
        # Context can suggest a contradiction; only trusted evidence may dismiss
        # an alternative and thereby restore the most confident display band.
        if match.has_contradiction or match.contradicts_own_card:
            continue
        return identity.taxon_id
    return None


def resolve_confidence(
    state: GraphState,
    subject_id: str,
    leader: Candidate,
    card: TaxonCard | None,
    evidence: EvidencePacket,
    tier: EvidenceTier,
    resolution: Resolution = Resolution.GENUS,
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
    # however characteristic it looks — FAILURE 8 — with one narrow, card-declared escape
    # below.
    tier_cap = confidence_ceiling(tier)
    capped = confidence_rank(tier_cap) < confidence_rank(confidence)
    before = confidence
    if capped:
        confidence = tier_cap
    step("tier_cap", before, confidence, applied=capped)

    # The card-declared exception. Everything narrowing it lives on the card and in
    # `confidence_exception_for`; what happens here is only the arithmetic, so the policy
    # cannot be read one way by this node and another way by anything else that asks.
    # It raises a ceiling and never lowers one.
    exception = (
        confidence_exception_for(card, evidence, subject_id, resolution)
        if card is not None
        else None
    )
    if exception is not None:
        raised = _EXCEPTION_CONFIDENCE[exception.exception.ceiling]
        before = confidence
        lifts = confidence_rank(raised) > confidence_rank(confidence)
        if lifts:
            confidence = raised
        step("diagnostic_exception", before, confidence, applied=lifts)

    if card is not None:
        match = match_card(card, evidence, subject_id)
        short = bool(match.missing_for_high_confidence) and confidence is Confidence.HIGH
        before = confidence
        if short:
            confidence = Confidence.MEDIUM
        step("requirement_cap", before, confidence, applied=short)

    # One feature does not make a high-confidence identification, however clearly it was
    # read: section 6 puts 85-94 at "leaves and bark visible" and "only part of the features"
    # at 70-84, and FAILURE 5 is a single lobed leaf taken for a genus. Before this step the
    # only thing between one admitted `leaf.shape` and `identified / high` was a reviewer
    # recommending otherwise (golden-060). The two declared exceptions keep their reach: a
    # fruit, seed, cone or acorn closes the question on its own, and a card-declared
    # diagnostic reading says so in the card.
    if confidence is Confidence.HIGH and exception is None and tier < EvidenceTier.FRUIT_SEED:
        independent = independent_support_features(leader, evidence, subject_id)
        # Counted by organ, not by feature path: bark.pattern and bark.peeling read off one
        # trunk are one look at one organ described twice. Section 6 puts 85-94 at "leaves
        # and bark visible" — two organs — and that is the corroboration this step asks for.
        short = (
            len({family_of(feature) for feature in independent}) < MIN_INDEPENDENT_FEATURES_FOR_HIGH
        )
        before = confidence
        if short:
            confidence = Confidence.MEDIUM
        step("corroboration_cap", before, confidence, applied=short)

    recommended = _lowest_recommendation(state, subject_id)
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
        own_bound = _lowest_recommendation(state, subject_id, finding)
        floored = own_bound is not None and confidence_rank(confidence) <= confidence_rank(
            own_bound
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

    if state.is_abstained(subject_id):
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
        for observation in decisive_observations_for(evidence, candidate_set.subject_id)
        if observation.value in vocabulary.get(observation.feature, frozenset())
    )
    # The tier this subject has already reached at decisive trust. A photograph resolving
    # only features at or below it cannot raise the claim, however well it is shot.
    reached_tier = max(
        (
            tier_of_feature(observation.feature)
            for observation in decisive_observations_for(evidence, candidate_set.subject_id)
        ),
        default=EvidenceTier.CONTEXT,
    )
    comparison_cards = ctx.knowledge.comparisons_for(taxa)
    photos = deprioritise_saturated_photos(
        follow_up_photos(comparison_cards, taxa, resolved),
        photo_bindings(ctx.knowledge.comparisons(), frozenset(vocabulary)),
        reached_tier,
    )
    comparison_request = bool(photos)
    if not photos:
        # No look-alike group applies, so the leader's own follow-up list is all there is.
        # It is a flat list of targets, and the only declared statement of what each target
        # resolves lives on the comparison cards' discriminators — enough to drop a request
        # whose every usable feature this subject has already answered.
        card = ctx.knowledge.try_taxon(leader.taxon)
        usable = frozenset(card_value_vocabulary((card,))) if card is not None else frozenset()
        own_bindings = photo_bindings(ctx.knowledge.comparisons(), usable)
        photos = deprioritise_saturated_photos(
            drop_resolved_photos(
                ctx.knowledge.follow_up_for((leader.taxon,)),
                own_bindings,
                resolved,
            ),
            own_bindings,
            reached_tier,
        )
    ownership_first = planned_attachment_request(state, candidate_set.subject_id, photos)
    if ownership_first is not None:
        return ownership_first
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
    competing: tuple[OpenWorldCandidate, ...] = (),
    disclosure: tuple[str, ...] = (),
) -> tuple[str, ...]:
    questions = []
    if evidence.possible_multiple_taxa:
        questions.append(
            "This verdict is scoped to this subject; other pieces or trees in the frame may "
            "be different taxa."
        )
    # Next, what the verdict rests on when nothing in it is diagnostic. It qualifies the
    # answer itself, so it must survive the cap ahead of every later line.
    questions.extend(disclosure)
    # Ahead of the decisive-feature lines: the eight-item cap drops late entries, and this
    # is the one a reader cannot reconstruct from the verdict — the model's own hypothesis
    # that the knowledge base had no card for.
    questions.extend(_competing_text(competing))
    questions.extend(open_world_text(state.open_world_for(subject_id)))
    questions.extend(
        f"{MISSING_DECISIVE_PHRASE}: {item}" for item in leader.missing_decisive_features
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
        open_world = state.open_world_for(subject_id)
        # Open-world only. Closed-world, a coverage-gap subject that shares a frame with a
        # usable one keeps the status it always had here, so the flag stays the only switch.
        coverage_gap = ctx.config.graph.open_world_discovery and (
            (state.quality is not None and subject_id in state.quality.coverage_gap_subject_ids)
            or any(rests_on_uncovered_evidence(state, candidate) for candidate in open_world)
        )
        # Missing taxonomy is not a missing photograph. A subject whose evidence fell outside
        # every card, or whose surviving no-card hypothesis rests on such evidence, stopped at
        # the reference data — and saying "insufficient evidence" would send the reader to
        # re-shoot a frame that was never the problem. An open-world hypothesis on evidence
        # the cards *can* represent is still reported, but does not move the blame.
        return FinalDecision(
            subject_id=subject_id,
            status=(
                DecisionStatus.KNOWLEDGE_COVERAGE_GAP
                if coverage_gap
                else DecisionStatus.INSUFFICIENT_EVIDENCE
            ),
            unresolved_questions=(
                *open_world_text(open_world),
                *(coverage_feature_text(state) if coverage_gap else ()),
            ),
            best_next_photo=choose_request(state, ctx, subject_id),
            arbiter_used=state.arbiter_used,
            open_world_candidates=open_world,
        )

    card = ctx.knowledge.try_taxon(leader.taxon)
    tier = candidate_support_tier(leader, evidence, subject_id)
    resolution_bound, bounds, binding_source, action_applied = resolve_resolution(
        state,
        subject_id,
        leader,
        card,
        tier,
        require_discriminating_support=ctx.config.graph.require_discriminating_support,
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
        why_not: tuple[str, ...] = ()
        if card is not None and binding_source == "no_discriminating_support":
            # Abstaining is not forgetting: say which genera this evidence still fits, so
            # the reader keeps the hypotheses and knows what the next photograph must split.
            genus = card.identity_at_or_broader(Resolution.GENUS)
            plausible = (
                (
                    genus.display_name,
                    *_undistinguished_rivals(
                        ctx, reranked.ordered[1:], evidence, subject_id, Resolution.GENUS, genus
                    ),
                )
                if genus is not None
                else ()
            )
            why_not = (_no_discriminating_support_text(card, named=False),)
            if plausible:
                why_not = (
                    *why_not,
                    f"Still consistent with this evidence: {', '.join(plausible)}.",
                )
        return FinalDecision(
            subject_id=subject_id,
            status=DecisionStatus.INSUFFICIENT_EVIDENCE,
            supporting_evidence=_support_summary(evidence, leader),
            unresolved_questions=_unresolved(
                state, evidence, subject_id, leader, disclosure=why_not
            ),
            best_next_photo=_next_photo(
                state, ctx, evidence, reranked, leader, tier, Confidence.LOW
            ),
            arbiter_used=state.arbiter_used,
            user_claim_verdict=verdict,
            evidence_tier=int(tier),
            open_world_candidates=state.open_world_for(subject_id),
        )

    resolution = selected.resolution
    _, competing = open_world_contest(state, subject_id, leader, card, tier)
    confidence, confidence_steps = resolve_confidence(
        state, subject_id, leader, card, evidence, tier, resolution
    )
    keep(derivation.model_copy(update={"confidence_steps": confidence_steps}))
    verdict = rule_on_user_claim(state, ctx, subject_id, reranked, evidence, selected.taxon_id)
    alternative = _nearest_alternative(ctx, reranked.ordered[1:], resolution, selected)
    disclosure: list[str] = []
    if card is not None and not discriminating_support(card, evidence, leader, subject_id):
        disclosure.append(_no_discriminating_support_text(card, named=True))
        rivals = _undistinguished_rivals(
            ctx, reranked.ordered[1:], evidence, subject_id, resolution, selected
        )
        if rivals:
            disclosure.append(
                f"Not ruled out on this evidence: {', '.join(rivals)}. Nothing in the "
                "photograph separates it from the answer above; only the ranking does."
            )
    unresolved = _unresolved(
        state, evidence, subject_id, leader, competing, disclosure=tuple(disclosure)
    )
    top_band = _reaches_the_top_band(card, evidence, subject_id, resolution)
    standing = (
        _declared_confusion_left_standing(
            ctx, card, reranked.ordered[1:], evidence, subject_id, resolution
        )
        if top_band and confidence is Confidence.HIGH
        else None
    )
    if standing is not None:
        top_band = False
        unresolved = (
            *unresolved,
            f"{TOP_BAND_WITHHELD_PHRASE}: {standing} is this card's declared look-alike and "
            "trusted evidence has not ruled it out (section 6 reserves 95-100 for no serious "
            "alternatives). Photograph the features that separate these candidates.",
        )
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
        nearest_alternative=alternative,
        unresolved_questions=unresolved,
        best_next_photo=_next_photo(state, ctx, evidence, reranked, leader, tier, confidence),
        arbiter_used=state.arbiter_used,
        user_claim_verdict=verdict,
        evidence_tier=int(tier),
        confidence_band=confidence_band(confidence, tier, decisive_reading=top_band),
        reference_status=(
            local_reference_status(((selected.provenance or card.provenance).review_state,))
            if card is not None
            else None
        ),
        open_world_candidates=state.open_world_for(subject_id),
        open_world_conflict=bool(competing),
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
    decision = decision.model_copy(
        update={"abstained": state.is_abstained(candidate_set.subject_id)}
    )
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
    if state.evidence is None or not state.evidence.subjects:
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
    # Every route to this node has passed the escalation gate, so arbitration has either run
    # or will not: an abstention a model asserted without evidence can be settled now.
    state = settle_unverified_abstention(state, ctx)
    decisions = []
    for subject_id in state.subject_ids:
        candidate_set = state.candidates_for(subject_id)
        if candidate_set is None:
            # A detected subject with insufficient evidence still receives its own explicit
            # terminal result. A reviewer cannot resurrect a subject excluded by quality.
            decisions.append(
                decide_subject_base(
                    state, ctx, CandidateSet(subject_id=subject_id), already_reranked=True
                ).model_copy(update={"abstained": state.is_abstained(subject_id)})
            )
        else:
            decisions.append(decide_subject(state, ctx, candidate_set))
    return state.evolve(decisions=tuple(decisions))
