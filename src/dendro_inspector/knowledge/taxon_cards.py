"""Pure matching helpers over taxon cards.

These functions are deterministic and model-free. They exist so that a reviewer can say
"the card requires needles or cones for high confidence and neither is visible" as a
checkable fact rather than a model opinion.
"""

from __future__ import annotations

from dataclasses import dataclass

from dendro_inspector.knowledge.evidence_hierarchy import (
    contextual_observations_for,
    full_positive_observations_for,
    positive_observations_for,
)
from dendro_inspector.schemas.evidence import EvidencePacket, Observation
from dendro_inspector.schemas.taxon import FeatureExpectation, TaxonCard


@dataclass(frozen=True, slots=True)
class CardMatch:
    """How one subject's evidence lines up against one taxon card."""

    taxon_id: str
    strong_hits: tuple[str, ...]
    supporting_hits: tuple[str, ...]
    contradiction_hits: tuple[str, ...]
    missing_for_high_confidence: tuple[str, ...]
    full_strong_hits: tuple[str, ...]

    @property
    def has_contradiction(self) -> bool:
        return bool(self.contradiction_hits)

    @property
    def high_confidence_supported(self) -> bool:
        return not self.missing_for_high_confidence and bool(self.full_strong_hits)


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
    """Match one subject's evidence against a taxon card at the shared trust boundary.

    Positive hits must be trusted image evidence. Contextual observations stay available for
    contradiction detection, while high-confidence requirements require full (not capped)
    positive support.
    """
    positive = positive_observations_for(evidence, subject_id)
    full_positive = full_positive_observations_for(evidence, subject_id)
    missing = tuple(
        requirement
        for requirement in card.required_for_high_confidence
        if not _requirement_satisfied(requirement, full_positive)
    )
    return CardMatch(
        taxon_id=card.taxon_id,
        strong_hits=_matches(card.strong_positive_features, positive),
        supporting_hits=_matches(card.supporting_features, positive),
        contradiction_hits=_matches(
            card.contradictions, contextual_observations_for(evidence, subject_id)
        ),
        missing_for_high_confidence=missing,
        full_strong_hits=_matches(card.strong_positive_features, full_positive),
    )


def _requirement_satisfied(requirement: str, observations: tuple[Observation, ...]) -> bool:
    """A token like ``needles_or_cones`` needs a full-trust named feature group."""
    for alternative in requirement.split("_or_"):
        if any(
            observation.feature == alternative or observation.feature.startswith(f"{alternative}.")
            for observation in observations
        ):
            return True
    return False


def missing_decisive_features(
    card: TaxonCard, evidence: EvidencePacket, subject_id: str
) -> tuple[str, ...]:
    """What the card says is still needed before this taxon can carry a strong claim."""
    return match_card(card, evidence, subject_id).missing_for_high_confidence
