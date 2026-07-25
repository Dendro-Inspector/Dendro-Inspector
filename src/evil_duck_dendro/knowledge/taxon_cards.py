"""Pure matching helpers over taxon cards.

These functions are deterministic and model-free. They exist so that a reviewer can say
"the card requires needles or cones for high confidence and neither is visible" as a
checkable fact rather than a model opinion.
"""

from __future__ import annotations

from dataclasses import dataclass

from evil_duck_dendro.schemas.evidence import EvidencePacket, Observation
from evil_duck_dendro.schemas.taxon import FeatureExpectation, TaxonCard


@dataclass(frozen=True, slots=True)
class CardMatch:
    """How one subject's evidence lines up against one taxon card."""

    taxon_id: str
    strong_hits: tuple[str, ...]
    supporting_hits: tuple[str, ...]
    contradiction_hits: tuple[str, ...]
    missing_for_high_confidence: tuple[str, ...]

    @property
    def has_contradiction(self) -> bool:
        return bool(self.contradiction_hits)

    @property
    def high_confidence_supported(self) -> bool:
        return not self.missing_for_high_confidence and bool(self.strong_hits)


def _matches(
    expectations: tuple[FeatureExpectation, ...], observations: tuple[Observation, ...]
) -> tuple[str, ...]:
    hits: list[str] = []
    for expectation in expectations:
        for observation in observations:
            if (
                observation.feature == expectation.feature
                and observation.value in expectation.values
            ):
                hits.append(observation.observation_id)
    return tuple(hits)


def match_card(
    card: TaxonCard,
    evidence: EvidencePacket,
    subject_id: str,
) -> CardMatch:
    """Match one subject's *resolvable* observations against a taxon card.

    Only visible observations count. A feature that could not be resolved in the frame is
    not evidence for the taxon and is not evidence against it.
    """
    observations = evidence.visible_observations_for(subject_id)
    missing = tuple(
        requirement
        for requirement in card.required_for_high_confidence
        if not _requirement_satisfied(requirement, evidence, subject_id)
    )
    return CardMatch(
        taxon_id=card.taxon_id,
        strong_hits=_matches(card.strong_positive_features, observations),
        supporting_hits=_matches(card.supporting_features, observations),
        contradiction_hits=_matches(card.contradictions, observations),
        missing_for_high_confidence=missing,
    )


def _requirement_satisfied(requirement: str, evidence: EvidencePacket, subject_id: str) -> bool:
    """A requirement token like ``needles_or_cones`` is satisfied by any named feature group."""
    for alternative in requirement.split("_or_"):
        if evidence.has_feature(subject_id, alternative):
            return True
    return False


def missing_decisive_features(
    card: TaxonCard, evidence: EvidencePacket, subject_id: str
) -> tuple[str, ...]:
    """What the card says is still needed before this taxon can carry a strong claim."""
    return match_card(card, evidence, subject_id).missing_for_high_confidence
