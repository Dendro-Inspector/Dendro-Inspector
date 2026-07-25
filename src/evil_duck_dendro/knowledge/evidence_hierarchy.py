"""Evidence hierarchy.

Implements section 2 of the domain prompt ("ІЄРАРХІЯ ДОКАЗІВ") and the confidence scale in
section 6, as data plus pure functions.

The domain prompt is unambiguous that not all evidence is equal, and the ordering is
specific: fruit and seed beat clear foliage, foliage beats bark, and bark alone does not
earn a strong claim no matter how characteristic it looks. Before this module existed the
system treated a bark observation and an acorn as interchangeable support, which is exactly
the failure the prompt spends section 13 warning about.

Two rules follow, both enforced deterministically rather than left to a model:

* **the best available tier caps the claim** — its resolution and its confidence;
* **detachable evidence only counts if it is attached.** Foliage or fruit that cannot be
  shown to belong to the analysed trunk demotes to context, because a leaf at the edge of
  the frame may belong to the neighbouring tree (prompt section 2, FAILURE 6).
"""

from __future__ import annotations

from enum import IntEnum

from evil_duck_dendro.schemas.evidence import EvidencePacket, Observation, Visibility
from evil_duck_dendro.schemas.taxon import Confidence, Resolution


class EvidenceTier(IntEnum):
    """Strength of a kind of evidence. Higher is stronger.

    Ordered exactly as the domain prompt lists it, strongest first:
    fruit/seed/cone/acorn > clear foliage > leaf arrangement > wood cut with bark >
    bark on the whole trunk > silhouette > context.
    """

    CONTEXT = 1
    SILHOUETTE = 2
    BARK = 3
    WOOD_CUT = 4
    LEAF_ARRANGEMENT = 5
    FOLIAGE = 6
    FRUIT_SEED = 7


#: Feature-family prefix -> tier. Longest prefix wins, so `leaf.arrangement` is a distinct
#: (weaker) tier from `leaf.shape` even though both start with `leaf`.
_FAMILY_TIERS: tuple[tuple[str, EvidenceTier], ...] = (
    # Strongest: something the tree produced that can be counted or held.
    ("fruit", EvidenceTier.FRUIT_SEED),
    ("seed", EvidenceTier.FRUIT_SEED),
    ("cones", EvidenceTier.FRUIT_SEED),
    ("acorn", EvidenceTier.FRUIT_SEED),
    ("nut", EvidenceTier.FRUIT_SEED),
    ("samara", EvidenceTier.FRUIT_SEED),
    ("catkin", EvidenceTier.FRUIT_SEED),
    ("pod", EvidenceTier.FRUIT_SEED),
    # Clear foliage.
    ("leaf", EvidenceTier.FOLIAGE),
    ("leaflet", EvidenceTier.FOLIAGE),
    ("needles", EvidenceTier.FOLIAGE),
    ("bud", EvidenceTier.FOLIAGE),
    # Arrangement on the branch — weaker than the leaf itself.
    ("leaf.arrangement", EvidenceTier.LEAF_ARRANGEMENT),
    ("branch.arrangement", EvidenceTier.LEAF_ARRANGEMENT),
    ("branch.short_shoots", EvidenceTier.LEAF_ARRANGEMENT),
    # A cut face, with or without bark at its edge.
    ("wood", EvidenceTier.WOOD_CUT),
    ("cut", EvidenceTier.WOOD_CUT),
    ("rings", EvidenceTier.WOOD_CUT),
    ("pores", EvidenceTier.WOOD_CUT),
    ("rays", EvidenceTier.WOOD_CUT),
    ("resin", EvidenceTier.WOOD_CUT),
    ("heartwood", EvidenceTier.WOOD_CUT),
    ("sapwood", EvidenceTier.WOOD_CUT),
    # Bark.
    ("bark", EvidenceTier.BARK),
    ("lenticels", EvidenceTier.BARK),
    # Shape of the thing as a whole.
    ("trunk", EvidenceTier.SILHOUETTE),
    ("crown", EvidenceTier.SILHOUETTE),
    ("habit", EvidenceTier.SILHOUETTE),
    ("branch", EvidenceTier.SILHOUETTE),
    # Where it is growing and what it is lying in.
    ("context", EvidenceTier.CONTEXT),
    ("site", EvidenceTier.CONTEXT),
    ("material", EvidenceTier.CONTEXT),
)

#: Families whose evidence can physically belong to a different tree. The domain prompt is
#: explicit that unattached foliage must not move a verdict, so these require
#: `attachment_confirmed` before they count at their own tier.
DETACHABLE_FAMILIES: frozenset[str] = frozenset(
    {
        "fruit",
        "seed",
        "cones",
        "acorn",
        "nut",
        "samara",
        "catkin",
        "pod",
        "leaf",
        "leaflet",
        "needles",
        "bud",
        "branch",
    }
)

#: The narrowest claim each tier can carry on its own (prompt sections 2, 6 and 14).
_RESOLUTION_CEILING: dict[EvidenceTier, Resolution] = {
    EvidenceTier.FRUIT_SEED: Resolution.SPECIES,
    EvidenceTier.FOLIAGE: Resolution.SPECIES_GROUP,
    EvidenceTier.LEAF_ARRANGEMENT: Resolution.GENUS,
    EvidenceTier.WOOD_CUT: Resolution.GENUS,
    EvidenceTier.BARK: Resolution.GENUS,
    EvidenceTier.SILHOUETTE: Resolution.FAMILY,
    EvidenceTier.CONTEXT: Resolution.FAMILY,
}

#: The strongest confidence each tier can carry on its own.
#:
#: Bark caps at LOW. That is the whole point of FAILURE 8 — "по корі точно яблуня / горіх /
#: дуб / ясен" is the single most common way this kind of system embarrasses itself.
_CONFIDENCE_CEILING: dict[EvidenceTier, Confidence] = {
    EvidenceTier.FRUIT_SEED: Confidence.HIGH,
    EvidenceTier.FOLIAGE: Confidence.HIGH,
    EvidenceTier.LEAF_ARRANGEMENT: Confidence.MEDIUM,
    EvidenceTier.WOOD_CUT: Confidence.MEDIUM,
    EvidenceTier.BARK: Confidence.LOW,
    EvidenceTier.SILHOUETTE: Confidence.LOW,
    EvidenceTier.CONTEXT: Confidence.LOW,
}

#: Displayed confidence bands, mapping this project's ordinal confidence back onto the
#: X/100 scale the domain prompt's response formats use. A band, never a point value:
#: "87/100" claims a calibration nobody has, "85–94/100" states the same thing honestly.
_BANDS: dict[Confidence, str] = {
    Confidence.HIGH: "85–94/100",
    Confidence.MEDIUM: "70–84/100",
    Confidence.LOW: "50–69/100",
}
BAND_DECISIVE = "95–100/100"
BAND_INSUFFICIENT = "<50/100"


def family_of(feature: str) -> str:
    """The first segment of a feature path (``bark.flake_geometry`` -> ``bark``)."""
    return feature.split(".", 1)[0]


def tier_of_feature(feature: str) -> EvidenceTier:
    """Tier for a feature path. Longest matching prefix wins; unknown families are context."""
    best: tuple[int, EvidenceTier] = (0, EvidenceTier.CONTEXT)
    for prefix, tier in _FAMILY_TIERS:
        matches = feature == prefix or feature.startswith(f"{prefix}.")
        if matches and len(prefix) > best[0]:
            best = (len(prefix), tier)
    return best[1]


def requires_attachment(feature: str) -> bool:
    """Whether this feature could physically belong to a neighbouring tree."""
    return family_of(feature) in DETACHABLE_FAMILIES


def effective_tier(observation: Observation) -> EvidenceTier:
    """The tier an observation actually counts at.

    Unresolvable observations count for nothing. Detachable evidence demotes to context
    unless attachment was **positively confirmed** — ``UNKNOWN`` is not a pass. Demoted
    evidence stays recorded and visible in the report; it simply cannot carry a verdict.
    """
    if observation.visibility is Visibility.NOT_VISIBLE:
        return EvidenceTier.CONTEXT
    if requires_attachment(observation.feature) and not observation.is_attached:
        return EvidenceTier.CONTEXT
    return tier_of_feature(observation.feature)


def best_tier(evidence: EvidencePacket, subject_id: str) -> EvidenceTier:
    """The strongest tier available for one subject."""
    tiers = [effective_tier(o) for o in evidence.observations_for(subject_id)]
    return max(tiers, default=EvidenceTier.CONTEXT)


def unattached_observations(evidence: EvidencePacket, subject_id: str) -> tuple[Observation, ...]:
    """Detachable evidence that was not confirmed as belonging to this subject.

    Includes both ``UNKNOWN`` and ``CONFIRMED_DETACHED``: neither can carry a verdict. They
    are reported differently, because "I could not trace the branch" asks for a different
    photograph than "this leaf was lying on the ground".
    """
    return tuple(
        o
        for o in evidence.visible_observations_for(subject_id)
        if requires_attachment(o.feature) and not o.is_attached
    )


def resolution_ceiling(tier: EvidenceTier) -> Resolution:
    return _RESOLUTION_CEILING[tier]


def confidence_ceiling(tier: EvidenceTier) -> Confidence:
    return _CONFIDENCE_CEILING[tier]


def confidence_band(confidence: Confidence, tier: EvidenceTier) -> str:
    """Render confidence on the domain prompt's X/100 scale, as a band.

    The top band is reserved for the case the prompt reserves it for: a fruit, seed, cone or
    acorn present in the frame, with confidence to match.
    """
    if confidence is Confidence.HIGH and tier is EvidenceTier.FRUIT_SEED:
        return BAND_DECISIVE
    return _BANDS[confidence]


def bark_only(evidence: EvidencePacket, subject_id: str) -> bool:
    """Whether the strongest thing available is bark or weaker.

    The trigger for most of the domain prompt's restraint rules: no aggressive rejection of
    a user's version, no high confidence, no sarcasm.
    """
    return best_tier(evidence, subject_id) <= EvidenceTier.BARK
