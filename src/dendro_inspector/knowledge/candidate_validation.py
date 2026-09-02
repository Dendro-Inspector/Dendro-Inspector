"""Shared deterministic admission boundary for model-proposed taxon candidates."""

from __future__ import annotations

from dataclasses import dataclass

from dendro_inspector.knowledge.evidence_hierarchy import (
    EvidenceTier,
    is_colour_feature,
    project_evidence,
    resolve_evidence_observations,
)
from dendro_inspector.knowledge.loader import KnowledgeBase
from dendro_inspector.knowledge.taxon_cards import match_card, missing_decisive_features
from dendro_inspector.schemas.candidates import (
    Candidate,
    CandidateSet,
    SupportStrength,
    strength_rank,
)
from dendro_inspector.schemas.evidence import EvidencePacket, Observation
from dendro_inspector.schemas.taxon import FeatureExpectation, TaxonCard


@dataclass(frozen=True, slots=True)
class CandidateValidationResult:
    """Validated ranking plus deterministic diagnostics for observability."""

    candidate_set: CandidateSet
    rejected_taxa: tuple[str, ...]
    dropped_evidence_ids: tuple[str, ...]
    demoted_scores: tuple[tuple[str, SupportStrength, SupportStrength], ...] = ()
    """Taxon, the strength the model proposed, and the strength its evidence earned."""


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


def _support_is_colour_only(
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


def _validated_support_ids(
    candidate: Candidate,
    evidence: EvidencePacket,
    subject_id: str,
    expectations: tuple[FeatureExpectation, ...],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    kept: list[str] = []
    dropped: list[str] = []
    for evidence_id in _deduplicate(candidate.supporting_evidence_ids):
        projection = project_evidence(evidence, evidence_id, subject_id)
        if projection.supports_identification and all(
            _matches_expectation(source, expectations) for source in projection.source_observations
        ):
            kept.append(evidence_id)
        else:
            dropped.append(evidence_id)
    return tuple(kept), tuple(dropped)


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
    demoted: list[tuple[str, SupportStrength, SupportStrength]] = []

    for candidate in candidate_set.ordered:
        card = knowledge.try_taxon(candidate.taxon)
        if card is None:
            rejected.append(candidate.taxon)
            dropped.extend(
                (*candidate.supporting_evidence_ids, *candidate.contradicting_evidence_ids)
            )
            continue

        support_expectations = (*card.strong_positive_features, *card.supporting_features)
        supporting, dropped_supporting = _validated_support_ids(
            candidate,
            evidence,
            candidate_set.subject_id,
            support_expectations,
        )
        contradicting, dropped_contradicting = _validated_contradiction_ids(
            candidate,
            evidence,
            candidate_set.subject_id,
            card.contradictions,
        )
        dropped.extend((*dropped_supporting, *dropped_contradicting))

        if not supporting or _support_is_colour_only(
            supporting, evidence, candidate_set.subject_id
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
    return CandidateValidationResult(
        candidate_set=validated,
        rejected_taxa=_deduplicate(tuple(rejected)),
        dropped_evidence_ids=_deduplicate(tuple(dropped)),
        demoted_scores=tuple(demoted),
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

    if full_strong and not match.missing_for_high_confidence:
        return SupportStrength.STRONG
    if strong or len(supporting) >= 2:
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
