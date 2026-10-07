"""Pure helpers over comparison cards.

The confusion reviewer's hardest question — "which alternative explains the same
observations?" — is answered here from declared data rather than from a model's memory.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping

from dendro_inspector.knowledge.evidence_hierarchy import (
    EvidenceTier,
    is_colour_feature,
    positive_observations_for,
    tier_of_feature,
)
from dendro_inspector.schemas.evidence import EvidencePacket
from dendro_inspector.schemas.taxon import ComparisonCard

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
    observations = positive_observations_for(evidence, subject_id)
    if not observations:
        return False
    weak = insufficient_features(cards)
    return all(
        observation.feature in weak or is_colour_feature(observation.feature)
        for observation in observations
    )


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


def follow_up_photos(
    cards: tuple[ComparisonCard, ...],
    taxa: frozenset[str],
    resolved: Collection[str],
) -> tuple[str, ...]:
    """Photographs worth asking for, in the cards' declared priority order.

    The recommendation list is the knowledge author's priority statement. Decisive-feature
    order only supplies bindings used to filter that list; it must never silently re-sort it.
    A photograph bound only to features this subject has already resolved carries no
    information left to gain, and asking for it anyway is what teaches people to ignore the
    request. A white-barked trunk with ``bark.peeling`` already read off it was still being
    asked for another bark macro, while the leaf characters that actually separate birch
    from white poplar went unrequested.

    ``resolved`` is a set of feature paths — full-trust positive observations only, so a
    partial or detached reading of a discriminator still counts as worth re-photographing.
    A photograph bound to both a resolved and an unresolved discriminator stays: it has
    something left to answer.
    """
    bindings: dict[str, set[str]] = {}
    for card in cards:
        for difference in card.decisive_differences:
            if difference.photo is None or len(set(difference.separates) & taxa) < 2:
                continue
            bindings.setdefault(difference.photo, set()).add(difference.feature)
    return drop_resolved_photos(
        recommended_photos(cards),
        {photo: frozenset(features) for photo, features in bindings.items()},
        resolved,
    )


def photo_bindings(
    cards: tuple[ComparisonCard, ...], usable: Collection[str]
) -> dict[str, frozenset[str]]:
    """Photograph target -> the features these cards declare it resolves, kept to ``usable``.

    ``usable`` is the feature set that could actually change the assessment in hand — in
    practice one taxon card's own declared features. A bark macro that resolves
    ``bark.texture`` answers nothing about a card carrying no ``bark.texture`` rule, so
    keeping it in the binding would make the photograph look informative when it is not.
    """
    bindings: dict[str, set[str]] = {}
    for card in cards:
        for difference in card.decisive_differences:
            if difference.photo is None or difference.feature not in usable:
                continue
            bindings.setdefault(difference.photo, set()).add(difference.feature)
    return {photo: frozenset(features) for photo, features in bindings.items()}


def deprioritise_saturated_photos(
    photos: tuple[str, ...],
    bindings: Mapping[str, frozenset[str]],
    reached_tier: EvidenceTier,
) -> tuple[str, ...]:
    """Move photographs that cannot raise the claim behind ones that can. Order only.

    ``drop_resolved_photos`` asks whether a target's features are already answered. This
    asks a different question: whether answering them could change anything. A target whose
    every declared feature sits at or below the tier this subject has already reached at
    decisive trust adds no authority however well it is shot — a second bark macro cannot
    lift a verdict past the bark ceiling, because the ceiling is the tier, not the framing.

    Two live birch runs asked for ``bark_macro_mid_trunk`` from photographs that already
    showed near-macro bark, because the target still had one unresolved bark feature bound
    to it and the flat list was consulted in order. The next informative photograph was a
    leaf.

    Reordered rather than removed, deliberately. If the saturated target is the only one on
    offer, asking a redundant question beats withholding the request — the same fail-open
    reasoning ``drop_resolved_photos`` uses for an unbound photograph. And a subject whose
    bark is present but only capped by doubt is *not* saturated: a better photograph of that
    same bark is genuinely worth asking for, which is why the tier is measured at decisive
    trust rather than from anything visible.
    """

    def adds_authority(photo: str) -> bool:
        features = bindings.get(photo)
        if not features:
            return True
        return any(tier_of_feature(feature) > reached_tier for feature in features)

    return (
        *(photo for photo in photos if adds_authority(photo)),
        *(photo for photo in photos if not adds_authority(photo)),
    )


def drop_resolved_photos(
    photos: tuple[str, ...],
    bindings: Mapping[str, frozenset[str]],
    resolved: Collection[str],
) -> tuple[str, ...]:
    """Photographs that still have a declared, unresolved feature to answer.

    A photograph with no binding at all is kept: unknown information value is not the same
    as none, and failing open means asking one redundant question rather than silently
    withholding the request that would have resolved the case.
    """
    already = set(resolved)
    return tuple(
        photo for photo in photos if not bindings.get(photo) or not bindings[photo] <= already
    )
