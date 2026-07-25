"""Pure helpers over comparison cards.

The confusion reviewer's hardest question — "which alternative explains the same
observations?" — is answered here from declared data rather than from a model's memory.
"""

from __future__ import annotations

from evil_duck_dendro.schemas.evidence import EvidencePacket
from evil_duck_dendro.schemas.taxon import ComparisonCard

#: Features that are famously unreliable on their own. Bark colour is the canonical case:
#: it varies with age, aspect, moisture, lighting and white balance.
INSUFFICIENT_ALONE: frozenset[str] = frozenset({"bark.colour", "bark.color", "wood.colour"})


def insufficient_features(cards: tuple[ComparisonCard, ...]) -> frozenset[str]:
    """Union of declared insufficient features plus the built-in colour set."""
    declared = {feature for card in cards for feature in card.insufficient_features}
    return frozenset(declared | INSUFFICIENT_ALONE)


def relies_only_on_insufficient_features(
    evidence: EvidencePacket,
    subject_id: str,
    cards: tuple[ComparisonCard, ...] = (),
) -> bool:
    """Whether a subject's resolvable evidence is *entirely* made of weak features.

    This is the colour-overweighting detector. It fires when every visible observation for
    the subject is on the insufficient list — one colour observation alongside real
    structural evidence is fine.
    """
    observations = evidence.visible_observations_for(subject_id)
    if not observations:
        return False
    weak = insufficient_features(cards)
    return all(observation.feature in weak for observation in observations)


def decisive_features_between(
    cards: tuple[ComparisonCard, ...], taxa: frozenset[str]
) -> tuple[str, ...]:
    """Features that actually separate the given taxa, order-stable and de-duplicated."""
    found: list[str] = []
    for card in cards:
        for difference in card.decisive_differences:
            if len(set(difference.separates) & taxa) >= 2 and difference.feature not in found:
                found.append(difference.feature)
    return tuple(found)


def recommended_photos(cards: tuple[ComparisonCard, ...]) -> tuple[str, ...]:
    """Follow-up photographs that would resolve the confusion, de-duplicated."""
    found: list[str] = []
    for card in cards:
        for photo in card.recommended_follow_up_photos:
            if photo not in found:
                found.append(photo)
    return tuple(found)
