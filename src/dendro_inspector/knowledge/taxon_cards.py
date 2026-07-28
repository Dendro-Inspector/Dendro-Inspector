"""Pure matching helpers over taxon cards.

These functions are deterministic and model-free. They exist so that a reviewer can say
"the card requires needles or cones for high confidence and neither is visible" as a
checkable fact rather than a model opinion.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from dendro_inspector.knowledge.evidence_hierarchy import (
    contextual_observations_for,
    full_positive_observations_for,
    positive_observations_for,
    project_observation,
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


def card_value_vocabulary(cards: Iterable[TaxonCard]) -> dict[str, frozenset[str]]:
    """Every ``feature -> values`` pair any card can match, merged across all cards.

    Matching is exact string equality on both halves, so this mapping is the complete set of
    observations the knowledge base is physically able to use. Anything outside it is
    extracted, carried through the packet, shown to every reviewer, and then silently
    discarded at the admission boundary — which is why both the extractor's vocabulary
    context and the quality gate's diagnostic are built from this one function.
    """
    vocabulary: dict[str, set[str]] = {}
    for card in cards:
        expectations = (
            *card.strong_positive_features,
            *card.supporting_features,
            *card.contradictions,
        )
        for expectation in expectations:
            vocabulary.setdefault(expectation.feature, set()).update(expectation.values)
    return {feature: frozenset(values) for feature, values in vocabulary.items()}


def unmatchable_observations(
    evidence: EvidencePacket,
    vocabulary: Mapping[str, frozenset[str]],
) -> tuple[Observation, ...]:
    """Trusted observations that no card can match on feature and value, in packet order.

    Not an error and not a model failure: an honest observation outside the vocabulary is
    worth more than a forced one inside it. It is a *coverage* measurement — of the cards
    when the feature is missing entirely, of the value list when only the value is.

    Restricted to observations that would otherwise have counted. Context-tier and
    untrusted observations never support an identification whatever the cards say, so
    counting them would inflate the gap with entries no card edit could ever recover —
    on the first live run that was the difference between a reported 46% and a real 30%.
    """
    return tuple(
        observation
        for observation in evidence.observations
        if observation.value not in vocabulary.get(observation.feature, frozenset())
        and project_observation(observation).supports_identification
    )
