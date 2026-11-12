"""Shared deterministic admission boundary for model-proposed taxon candidates."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from dendro_inspector.knowledge.evidence_hierarchy import (
    EvidenceTier,
    confidence_ceiling,
    family_of,
    is_colour_feature,
    positive_observations_for,
    project_evidence,
    project_observation,
    resolution_ceiling,
    resolve_evidence_observations,
)
from dendro_inspector.knowledge.loader import KnowledgeBase
from dendro_inspector.knowledge.taxon_cards import (
    match_card,
    missing_decisive_features,
    self_contradiction_hits,
)
from dendro_inspector.schemas.base import IDENTIFIER_PATTERN
from dendro_inspector.schemas.candidates import (
    STRENGTH_CONFIDENCE_CEILING,
    Candidate,
    CandidateSet,
    OpenWorldCandidate,
    SupportStrength,
    strength_rank,
)
from dendro_inspector.schemas.evidence import EvidencePacket, Observation, is_positive_reading
from dendro_inspector.schemas.reference import ReferenceStatus, local_reference_status
from dendro_inspector.schemas.taxon import (
    Confidence,
    FeatureExpectation,
    Resolution,
    TaxonCard,
    confidence_rank,
    resolution_rank,
)

#: Compiled once: every discarded reference is tested against it.
_IDENTIFIER = re.compile(IDENTIFIER_PATTERN)

#: The shape of a scientific name written as an identifier: a genus, optionally followed by
#: an epithet and at most two infraspecific parts (``platanus_x_hispanica``). Anything else
#: — ``tree_1``, ``oak-like``, a value token — is not a taxon a reference could answer for.
_SCIENTIFIC_NAME = re.compile(r"^[a-z]{2,}(?:_[a-z]+){0,3}$")

#: No card means no high-confidence requirement can be checked. The final decision applies
#: the same cap to a card whose requirement is unmet (``requirement_cap``).
_OPEN_WORLD_CONFIDENCE_CAP = Confidence.MEDIUM
_OPEN_WORLD_STRENGTH_CAP = SupportStrength.MODERATE


@dataclass(frozen=True, slots=True)
class DroppedSupportDetail:
    """A reason for rejecting one proposed support ID; never changes admission."""

    taxon: str
    evidence_id: str
    reason: Literal[
        "unresolved_or_foreign_reference",
        "untrusted_evidence",
        "feature_not_declared",
        "value_not_declared",
    ]
    feature: str | None = None
    value: str | None = None
    expected_values: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CandidateValidationResult:
    """Validated ranking plus deterministic diagnostics for observability."""

    candidate_set: CandidateSet
    rejected_taxa: tuple[str, ...]
    dropped_evidence_ids: tuple[str, ...]
    dropped_support_details: tuple[DroppedSupportDetail, ...] = ()
    """Candidate-specific reasons, preserving the model's raw feature/value."""
    malformed_evidence_ids: tuple[str, ...] = ()
    """The subset of ``dropped_evidence_ids`` that is not even shaped like an identifier.

    Same outcome, different cause. A well-formed reference to nothing is a model that
    misremembered an id; a malformed one is a provider whose structured output leaked.
    Both are discarded here, and separating them keeps a decoding defect legible instead
    of filed under ordinary disagreement.
    """
    demoted_scores: tuple[tuple[str, SupportStrength, SupportStrength], ...] = ()
    """Taxon, the strength the model proposed, and the strength its evidence earned."""
    open_world: tuple[OpenWorldCandidate, ...] = ()
    """The subset of ``rejected_taxa`` that had no card but did have trusted evidence.

    Still rejected — admission is unchanged — but retained instead of discarded, so a taxon
    missing from the knowledge base is reported as missing taxonomy rather than vanishing.
    """


def candidate_ranking_signature(candidate_set: CandidateSet) -> tuple[tuple[object, ...], ...]:
    """Material signature for deciding whether two validated rankings conflict."""
    return tuple(
        (
            candidate.taxon,
            candidate.resolution,
            candidate.score,
            candidate.supporting_evidence_ids,
            candidate.contradicting_evidence_ids,
        )
        for candidate in candidate_set.ordered
    )


def _matches_expectation(
    observation: Observation,
    expectations: tuple[FeatureExpectation, ...],
) -> bool:
    return any(
        observation.feature == expectation.feature and observation.value in expectation.values
        for expectation in expectations
    )


def _deduplicate(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def cards_in_play(
    evidence: EvidencePacket,
    knowledge: KnowledgeBase,
    subject_ids: tuple[str, ...],
) -> tuple[str, ...]:
    """Retrieve cards any eligible subject could support before a model ranks them.

    Admission requires at least one exact, trusted, non-colour observation matching the
    candidate's card, and no trusted observation disagreeing with that card on a feature
    path the card itself declares strong-positive. Inferences inherit their observations, so
    they cannot introduce a surviving taxon outside this set. No proposal, expected answer
    or top-k limit is used.

    The second condition is why ``fagus`` is not retrieved for a scaly-barked trunk it was
    previously shown for: see :func:`taxon_cards.self_contradiction_hits`.

    Both conditions are evaluated **per subject**, then combined. Pooling every subject's
    observations first would let one tree's bark veto a card another tree in the same frame
    positively supports, which is the multi-subject bleed the packet's subject scoping
    exists to prevent.
    """
    eligible: dict[str, tuple[Observation, ...]] = {
        subject_id: tuple(
            observation
            for observation in evidence.observations
            if observation.subject_id == subject_id
            and not is_colour_feature(observation.feature)
            and project_observation(observation).supports_identification
        )
        for subject_id in subject_ids
    }
    return tuple(
        taxon_id
        for taxon_id in knowledge.available_taxon_ids()
        if (card := knowledge.try_taxon(taxon_id)) is not None
        and any(
            not self_contradiction_hits(card, observations)
            and any(
                _matches_expectation(
                    observation, (*card.strong_positive_features, *card.supporting_features)
                )
                for observation in observations
            )
            for observations in eligible.values()
        )
    )


def _covered_reference_status(
    name: str, covered_by: tuple[str, ...], knowledge: KnowledgeBase
) -> ReferenceStatus:
    """How reviewed the local declarations of ``name`` are, most conservative first."""
    states = []
    for card_id in covered_by:
        card = knowledge.taxon(card_id)
        identity = next((item for item in card.broader_identities if item.taxon_id == name), None)
        provenance = identity.provenance if identity and identity.provenance else card.provenance
        states.append(provenance.review_state)
    return local_reference_status(tuple(states))


def open_world_candidate(
    candidate: Candidate,
    evidence: EvidencePacket,
    subject_id: str,
    knowledge: KnowledgeBase,
) -> OpenWorldCandidate | None:
    """Retain a no-card hypothesis, or return ``None`` to discard it.

    Retained only when all of these hold, each of which is a reason the hypothesis is about
    *this photograph* rather than about the model's prior:

    * the id is a name some card declares as a broader identity or alias (retained as
      *covered*, with those cards' local reference status), or it is shaped like a
      scientific name (retained as *uncovered* — missing taxonomy);
    * the model proposed a taxonomic level, not ``unknown``;
    * at least one supporting id survives as trusted, same-subject, readable evidence that
      can support identification, under the same trust projection admission uses — so an
      unattached fallen fruit or another subject's leaf carries nothing here either;
    * the surviving support is not colour alone.

    Everything retained is bounded by that surviving evidence, never by the model's own
    estimate and never by any reference source: resolution by the tier ceiling, score below
    strong, and image confidence below high and below the tier ceiling.

    A covered name is kept out of admission deliberately. Resolving ``juglans`` to a
    walnut card would be a choice between two cards that admission may not make. The one
    exception is made before this function is reached: a broader identity exactly one card
    declares is read as that card (:func:`_resolved_to_sole_card`).
    """
    covered_by = knowledge.declared_names().get(candidate.taxon, ())
    if (
        not covered_by and not _SCIENTIFIC_NAME.fullmatch(candidate.taxon)
    ) or candidate.resolution is Resolution.UNKNOWN:
        return None

    surviving: list[str] = []
    tiers: list[EvidenceTier] = []
    for evidence_id in _deduplicate(candidate.supporting_evidence_ids):
        projection = project_evidence(evidence, evidence_id, subject_id)
        if (
            projection.supports_identification
            and projection.source_observations
            and all(is_positive_reading(source.value) for source in projection.source_observations)
        ):
            surviving.append(evidence_id)
            tiers.append(projection.tier)
    if not surviving or support_is_colour_only(tuple(surviving), evidence, subject_id):
        return None

    tier = max(tiers)
    resolution = min(candidate.resolution, resolution_ceiling(tier), key=resolution_rank)
    score = min(candidate.score, _OPEN_WORLD_STRENGTH_CAP, key=strength_rank)
    return OpenWorldCandidate(
        subject_id=subject_id,
        taxon=candidate.taxon,
        proposed_rank=candidate.rank,
        proposed_resolution=candidate.resolution,
        resolution=resolution,
        proposed_score=candidate.score,
        score=score,
        supporting_evidence_ids=tuple(surviving),
        evidence_tier=int(tier),
        image_confidence_ceiling=min(
            STRENGTH_CONFIDENCE_CEILING[score],
            confidence_ceiling(tier),
            _OPEN_WORLD_CONFIDENCE_CAP,
            key=confidence_rank,
        ),
        covered_by=covered_by,
        reference_status=(
            _covered_reference_status(candidate.taxon, covered_by, knowledge)
            if covered_by
            else ReferenceStatus.PROVISIONAL
        ),
    )


def support_is_colour_only(
    support_ids: tuple[str, ...],
    evidence: EvidencePacket,
    subject_id: str,
) -> bool:
    sources = tuple(
        source
        for evidence_id in support_ids
        for source in resolve_evidence_observations(evidence, evidence_id, subject_id)
    )
    return bool(sources) and all(is_colour_feature(source.feature) for source in sources)


def _dropped_support_detail(
    taxon: str,
    evidence_id: str,
    evidence: EvidencePacket,
    subject_id: str,
    expectations: tuple[FeatureExpectation, ...],
) -> DroppedSupportDetail:
    projection = project_evidence(evidence, evidence_id, subject_id)
    if not projection.source_observations:
        return DroppedSupportDetail(taxon, evidence_id, "unresolved_or_foreign_reference")
    if not projection.supports_identification:
        return DroppedSupportDetail(taxon, evidence_id, "untrusted_evidence")
    for source in projection.source_observations:
        allowed = tuple(
            sorted(
                {
                    value
                    for expectation in expectations
                    if expectation.feature == source.feature
                    for value in expectation.values
                }
            )
        )
        if not allowed:
            return DroppedSupportDetail(
                taxon, evidence_id, "feature_not_declared", source.feature, source.value
            )
        if source.value not in allowed:
            return DroppedSupportDetail(
                taxon, evidence_id, "value_not_declared", source.feature, source.value, allowed
            )
    # These are precisely the failure conditions of _validated_support_ids.
    raise AssertionError("dropped support has no failed admission condition")


def _validated_support_ids(
    candidate: Candidate,
    evidence: EvidencePacket,
    subject_id: str,
    expectations: tuple[FeatureExpectation, ...],
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[DroppedSupportDetail, ...]]:
    kept: list[str] = []
    dropped: list[str] = []
    details: list[DroppedSupportDetail] = []
    for evidence_id in _deduplicate(candidate.supporting_evidence_ids):
        projection = project_evidence(evidence, evidence_id, subject_id)
        if projection.supports_identification and all(
            _matches_expectation(source, expectations) for source in projection.source_observations
        ):
            kept.append(evidence_id)
        else:
            dropped.append(evidence_id)
            details.append(
                _dropped_support_detail(
                    candidate.taxon, evidence_id, evidence, subject_id, expectations
                )
            )
    return tuple(kept), tuple(dropped), tuple(details)


def _validated_contradiction_ids(
    candidate: Candidate,
    evidence: EvidencePacket,
    subject_id: str,
    expectations: tuple[FeatureExpectation, ...],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    kept: list[str] = []
    dropped: list[str] = []
    for evidence_id in _deduplicate(candidate.contradicting_evidence_ids):
        sources = resolve_evidence_observations(evidence, evidence_id, subject_id)
        if sources and all(_matches_expectation(source, expectations) for source in sources):
            kept.append(evidence_id)
        else:
            dropped.append(evidence_id)
    return tuple(kept), tuple(dropped)


def _resolved_to_sole_card(candidate: Candidate, knowledge: KnowledgeBase) -> Candidate:
    """Read a broader name that exactly one card declares as that card, at that level.

    Live case photo-090: a model proposed ``robinia`` for a black locust. The only card is
    ``robinia_pseudoacacia``, which declares ``robinia`` as its genus, so the hypothesis was
    turned away at the door and the tree ended as insufficient evidence with the right card
    never consulted. The name is unambiguous — one card, one declared identity — so it is
    read as that card, with the claim capped at the level the model actually named. The
    card's own rules then admit or reject it exactly as if its id had been proposed.

    Deliberately narrow. A name two cards share (``juglans``) is not a choice this function
    may make and stays in the open-world channel, as does an alias: admission still matches
    card ids, and this adds only the ids a single card itself declares as its broader self.
    """
    if knowledge.try_taxon(candidate.taxon) is not None:
        return candidate
    covering = knowledge.declared_names().get(candidate.taxon, ())
    if len(covering) != 1:
        return candidate
    card = knowledge.taxon(covering[0])
    identity = next(
        (item for item in card.broader_identities if item.taxon_id == candidate.taxon), None
    )
    if identity is None:
        return candidate
    return candidate.model_copy(
        update={
            "taxon": card.taxon_id,
            "resolution": min(candidate.resolution, identity.resolution, key=resolution_rank),
        }
    )


def validate_candidate_set_with_report(
    candidate_set: CandidateSet,
    evidence: EvidencePacket,
    knowledge: KnowledgeBase,
) -> CandidateValidationResult:
    """Admit only known candidates with exact, trusted, candidate-specific support.

    Candidate order is preserved. Evidence ids are de-duplicated, unsupported ids are
    removed, candidates without surviving positive support are rejected, and survivor ranks
    are rebuilt densely from one. An all-rejected proposal returns an explicit empty set.
    """
    survivors: list[Candidate] = []
    rejected: list[str] = []
    dropped: list[str] = []
    dropped_support_details: list[DroppedSupportDetail] = []
    demoted: list[tuple[str, SupportStrength, SupportStrength]] = []
    open_world: list[OpenWorldCandidate] = []

    for proposed in candidate_set.ordered:
        candidate = _resolved_to_sole_card(proposed, knowledge)
        if candidate is not proposed and any(
            survivor.taxon == candidate.taxon for survivor in survivors
        ):
            # The card this name resolves to is already in play under its own id.
            dropped.extend(
                (*proposed.supporting_evidence_ids, *proposed.contradicting_evidence_ids)
            )
            continue
        card = knowledge.try_taxon(candidate.taxon)
        if card is None:
            rejected.append(candidate.taxon)
            dropped.extend(
                (*candidate.supporting_evidence_ids, *candidate.contradicting_evidence_ids)
            )
            retained = open_world_candidate(
                candidate, evidence, candidate_set.subject_id, knowledge
            )
            if retained is not None:
                open_world.append(retained)
            continue

        support_expectations = (*card.strong_positive_features, *card.supporting_features)
        supporting, dropped_supporting, support_details = _validated_support_ids(
            candidate,
            evidence,
            candidate_set.subject_id,
            support_expectations,
        )
        dropped_support_details.extend(support_details)
        contradicting, dropped_contradicting = _validated_contradiction_ids(
            candidate,
            evidence,
            candidate_set.subject_id,
            card.contradictions,
        )
        dropped.extend((*dropped_supporting, *dropped_contradicting))

        if (
            not supporting
            or support_is_colour_only(supporting, evidence, candidate_set.subject_id)
            # Kept in step with `cards_in_play` deliberately. The retrieval filter is safe
            # only while admission cannot keep a taxon retrieval would not have shown, and
            # the contract test asserts exactly that containment.
            or self_contradiction_hits(
                card, positive_observations_for(evidence, candidate_set.subject_id)
            )
        ):
            rejected.append(candidate.taxon)
            dropped.extend(supporting)
            continue

        adjudicated = candidate.model_copy(
            update={
                "supporting_evidence_ids": supporting,
                "contradicting_evidence_ids": contradicting,
                "missing_decisive_features": missing_decisive_features(
                    card, evidence, candidate_set.subject_id
                ),
                "rank": len(survivors) + 1,
            }
        )
        effective = adjudicate_score(card, evidence, candidate_set.subject_id, adjudicated)
        if effective is not candidate.score:
            demoted.append((candidate.taxon, candidate.score, effective))
        survivors.append(adjudicated.model_copy(update={"score": effective}))

    validated = candidate_set.model_copy(update={"candidates": tuple(survivors)})
    discarded = _deduplicate(tuple(dropped))
    return CandidateValidationResult(
        candidate_set=validated,
        rejected_taxa=_deduplicate(tuple(rejected)),
        dropped_evidence_ids=discarded,
        dropped_support_details=tuple(dropped_support_details),
        malformed_evidence_ids=tuple(
            reference for reference in discarded if not _IDENTIFIER.fullmatch(reference)
        ),
        demoted_scores=tuple(demoted),
        open_world=tuple(open_world),
    )


def validate_candidate_set(
    candidate_set: CandidateSet,
    evidence: EvidencePacket,
    knowledge: KnowledgeBase,
) -> CandidateSet:
    """Return only the admitted candidate ranking."""
    return validate_candidate_set_with_report(candidate_set, evidence, knowledge).candidate_set


def derive_support_strength(
    card: TaxonCard,
    evidence: EvidencePacket,
    subject_id: str,
    support_ids: tuple[str, ...],
) -> SupportStrength:
    """The strength this candidate's own card grants its surviving support.

    A model's ``score`` is a self-assessment, and self-assessment is exactly what the
    determinism boundary exists to keep out of a verdict: the same photograph returned low,
    medium or high confidence depending on how bold the primary model felt. This reads the
    card instead — what was hit, at what trust, and whether the card's own high-confidence
    requirement is satisfied.
    """
    match = match_card(card, evidence, subject_id)
    reachable = {
        source.observation_id
        for evidence_id in support_ids
        for source in resolve_evidence_observations(evidence, evidence_id, subject_id)
    }
    full_strong = set(match.full_strong_hits) & reachable
    strong = set(match.strong_hits) & reachable
    supporting = set(match.supporting_hits) & reachable
    # Supporting hits are counted once per organ. Three readings of one bark surface —
    # plates, mosaic, peeling — are one observation of one organ described three ways, and
    # counting them as three is how a sycamore card outranked pine on a photograph of pine
    # bark. Two organs agreeing is corroboration; one organ restated is not.
    features = {
        observation.observation_id: observation.feature for observation in evidence.observations
    }
    supporting_organs = {
        family_of(features[observation_id])
        for observation_id in supporting
        if observation_id in features
    }

    if full_strong and not match.missing_for_high_confidence:
        return SupportStrength.STRONG
    if strong or len(supporting_organs) >= 2:
        return SupportStrength.MODERATE
    return SupportStrength.WEAK


def adjudicate_score(
    card: TaxonCard,
    evidence: EvidencePacket,
    subject_id: str,
    candidate: Candidate,
) -> SupportStrength:
    """The lower of what the model claimed and what its evidence earned.

    Only ever downward. A model that has looked at the photograph may have seen a reason to
    doubt its own support that the card cannot express, and that judgement is kept; the
    reverse — a card-thin candidate labelled ``strong`` — is the failure this closes.
    """
    derived = derive_support_strength(card, evidence, subject_id, candidate.supporting_evidence_ids)
    return min(candidate.score, derived, key=strength_rank)


def candidate_support_tier(
    candidate: Candidate,
    evidence: EvidencePacket,
    subject_id: str,
) -> EvidenceTier:
    """Strongest tier carried by this candidate's already-validated support ids."""
    return max(
        (
            project_evidence(evidence, evidence_id, subject_id).tier
            for evidence_id in candidate.supporting_evidence_ids
        ),
        default=EvidenceTier.CONTEXT,
    )
