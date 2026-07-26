"""Shared deterministic admission boundary for model-proposed taxon candidates."""

from __future__ import annotations

from dataclasses import dataclass

from evil_duck_dendro.knowledge.evidence_hierarchy import (
    EvidenceTier,
    project_evidence,
    resolve_evidence_observations,
)
from evil_duck_dendro.knowledge.loader import KnowledgeBase
from evil_duck_dendro.knowledge.taxon_cards import missing_decisive_features
from evil_duck_dendro.schemas.candidates import Candidate, CandidateSet
from evil_duck_dendro.schemas.evidence import EvidencePacket, Observation
from evil_duck_dendro.schemas.taxon import FeatureExpectation


@dataclass(frozen=True, slots=True)
class CandidateValidationResult:
    """Validated ranking plus deterministic diagnostics for observability."""

    candidate_set: CandidateSet
    rejected_taxa: tuple[str, ...]
    dropped_evidence_ids: tuple[str, ...]


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

        if not supporting:
            rejected.append(candidate.taxon)
            continue

        survivors.append(
            candidate.model_copy(
                update={
                    "supporting_evidence_ids": supporting,
                    "contradicting_evidence_ids": contradicting,
                    "missing_decisive_features": missing_decisive_features(
                        card, evidence, candidate_set.subject_id
                    ),
                    "rank": len(survivors) + 1,
                }
            )
        )

    validated = candidate_set.model_copy(update={"candidates": tuple(survivors)})
    return CandidateValidationResult(
        candidate_set=validated,
        rejected_taxa=_deduplicate(tuple(rejected)),
        dropped_evidence_ids=_deduplicate(tuple(dropped)),
    )


def validate_candidate_set(
    candidate_set: CandidateSet,
    evidence: EvidencePacket,
    knowledge: KnowledgeBase,
) -> CandidateSet:
    """Return only the admitted candidate ranking."""
    return validate_candidate_set_with_report(candidate_set, evidence, knowledge).candidate_set


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
