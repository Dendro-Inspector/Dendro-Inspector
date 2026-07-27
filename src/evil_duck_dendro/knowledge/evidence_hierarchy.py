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

from dataclasses import dataclass
from enum import IntEnum

from evil_duck_dendro.schemas.evidence import (
    EvidencePacket,
    ImageLimitation,
    Observation,
    ObservationSource,
    Reliability,
    Visibility,
    WoodSurface,
    requires_wood_surface,
)
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


class EvidenceTrust(IntEnum):
    """Whether model-produced evidence may positively support an identification."""

    CONTEXT_ONLY = 0
    CAPPED_POSITIVE = 1
    FULL_POSITIVE = 2


@dataclass(frozen=True, slots=True)
class EvidenceProjection:
    """Deterministic trust projection for one observation or inference id."""

    evidence_id: str
    source_observations: tuple[Observation, ...]
    trust: EvidenceTrust
    tier: EvidenceTier

    @property
    def supports_identification(self) -> bool:
        return self.trust is not EvidenceTrust.CONTEXT_ONLY and self.tier > EvidenceTier.CONTEXT


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
    # Bark and the tissue directly beneath it.
    ("bark", EvidenceTier.BARK),
    ("inner_bark", EvidenceTier.BARK),
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

#: These observations claim anatomy that only a prepared transverse end grain can show.
_PREPARED_END_GRAIN_PREFIXES: tuple[str, ...] = (
    "pores",
    "rays",
    "wood.vessels",
    "wood.resin_canals",
)

#: Colour is supporting evidence only, regardless of how favourable the photograph looks.
_COLOUR_SUFFIXES: tuple[str, ...] = (".colour", ".color", ".tone")

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


def is_colour_feature(feature: str) -> bool:
    """Whether a feature fundamentally describes colour or tone."""
    return feature.endswith(_COLOUR_SUFFIXES)


def _requires_prepared_end_grain(feature: str) -> bool:
    return any(
        feature == prefix or feature.startswith(f"{prefix}.")
        for prefix in _PREPARED_END_GRAIN_PREFIXES
    )


def _limitation_for(evidence: EvidencePacket, observation: Observation) -> ImageLimitation | None:
    if observation.image_id is None:
        return None
    return next(
        (item for item in evidence.image_limitations if item.image_id == observation.image_id),
        None,
    )


def observation_trust(
    observation: Observation,
    limitation: ImageLimitation | None = None,
) -> EvidenceTrust:
    """Project one observation onto the deterministic identification trust boundary.

    Besides source, visibility, reliability and attachment, the physical wood surface caps
    what an image can prove. Colour and tone remain supporting context rather than full-tier
    evidence under every photographic condition.
    """
    if observation.source is not ObservationSource.IMAGE:
        return EvidenceTrust.CONTEXT_ONLY
    if observation.visibility in (Visibility.OBSCURED, Visibility.NOT_VISIBLE):
        return EvidenceTrust.CONTEXT_ONLY
    if requires_attachment(observation.feature) and not observation.is_attached:
        return EvidenceTrust.CONTEXT_ONLY

    trust = EvidenceTrust.FULL_POSITIVE
    if observation.visibility is Visibility.PARTIAL or observation.reliability is Reliability.LOW:
        trust = EvidenceTrust.CAPPED_POSITIVE

    if _requires_prepared_end_grain(observation.feature):
        if observation.wood_surface is not WoodSurface.PREPARED_END_GRAIN:
            return EvidenceTrust.CONTEXT_ONLY
    elif (
        requires_wood_surface(observation.feature)
        and observation.wood_surface is not WoodSurface.PREPARED_END_GRAIN
    ):
        trust = min(trust, EvidenceTrust.CAPPED_POSITIVE)

    if is_colour_feature(observation.feature):
        trust = min(trust, EvidenceTrust.CAPPED_POSITIVE)

    return trust


def _tier_at_trust(tier: EvidenceTier, trust: EvidenceTrust) -> EvidenceTier:
    if trust is EvidenceTrust.CONTEXT_ONLY:
        return EvidenceTier.CONTEXT
    if trust is EvidenceTrust.CAPPED_POSITIVE:
        return min(tier, EvidenceTier.BARK)
    return tier


def project_observation(
    observation: Observation,
    limitation: ImageLimitation | None = None,
) -> EvidenceProjection:
    """Return the trust and effective tier of one observation."""
    trust = observation_trust(observation, limitation)
    return EvidenceProjection(
        evidence_id=observation.observation_id,
        source_observations=(observation,),
        trust=trust,
        tier=_tier_at_trust(tier_of_feature(observation.feature), trust),
    )


def resolve_evidence_observations(
    evidence: EvidencePacket,
    evidence_id: str,
    subject_id: str,
) -> tuple[Observation, ...]:
    """Resolve an observation/inference id to source observations for exactly one subject.

    Unknown ids, cross-subject inferences, and the ambiguous case where an observation and an
    inference reuse the same id all fail closed to an empty tuple.
    """
    observations = tuple(o for o in evidence.observations if o.observation_id == evidence_id)
    inferences = tuple(i for i in evidence.inferences if i.inference_id == evidence_id)
    if len(observations) + len(inferences) != 1:
        return ()

    if observations:
        sources = observations
    else:
        by_id = {observation.observation_id: observation for observation in evidence.observations}
        sources = tuple(
            by_id[source_id] for source_id in inferences[0].derived_from if source_id in by_id
        )
        if len(sources) != len(inferences[0].derived_from):
            return ()

    if not sources or any(source.subject_id != subject_id for source in sources):
        return ()
    return sources


def project_evidence(
    evidence: EvidencePacket,
    evidence_id: str,
    subject_id: str,
) -> EvidenceProjection:
    """Project an observation or inference without trusting model-authored support labels.

    An inference inherits the weakest trust of every source observation. Its tier is never
    stronger than those sources could carry directly, and one contextual source makes the
    entire inference contextual.
    """
    sources = resolve_evidence_observations(evidence, evidence_id, subject_id)
    if not sources:
        return EvidenceProjection(
            evidence_id=evidence_id,
            source_observations=(),
            trust=EvidenceTrust.CONTEXT_ONLY,
            tier=EvidenceTier.CONTEXT,
        )

    source_projections = tuple(
        project_observation(source, _limitation_for(evidence, source)) for source in sources
    )
    trust = min(projection.trust for projection in source_projections)
    tier = min(projection.tier for projection in source_projections)
    return EvidenceProjection(
        evidence_id=evidence_id,
        source_observations=sources,
        trust=trust,
        tier=tier,
    )


def positive_observations_for(evidence: EvidencePacket, subject_id: str) -> tuple[Observation, ...]:
    """Same-subject observations allowed to carry positive identification support."""
    return tuple(
        observation
        for observation in evidence.observations_for(subject_id)
        if observation_trust(observation, _limitation_for(evidence, observation))
        is not EvidenceTrust.CONTEXT_ONLY
    )


def full_positive_observations_for(
    evidence: EvidencePacket, subject_id: str
) -> tuple[Observation, ...]:
    """Same-subject observations allowed to carry their feature family's normal tier."""
    return tuple(
        observation
        for observation in evidence.observations_for(subject_id)
        if observation_trust(observation, _limitation_for(evidence, observation))
        is EvidenceTrust.FULL_POSITIVE
    )


def contextual_observations_for(
    evidence: EvidencePacket, subject_id: str
) -> tuple[Observation, ...]:
    """Resolvable observations retained for flaw and contradiction detection."""
    return tuple(
        observation
        for observation in evidence.observations_for(subject_id)
        if observation.visibility is not Visibility.NOT_VISIBLE
    )


def effective_tier(
    observation: Observation,
    limitation: ImageLimitation | None = None,
) -> EvidenceTier:
    """The tier an observation carries after the shared trust projection."""
    return project_observation(observation, limitation).tier


def best_tier(evidence: EvidencePacket, subject_id: str) -> EvidenceTier:
    """The strongest trusted positive tier available for one subject."""
    tiers = [
        effective_tier(observation, _limitation_for(evidence, observation))
        for observation in evidence.observations_for(subject_id)
    ]
    return max(tiers, default=EvidenceTier.CONTEXT)


def unattached_observations(evidence: EvidencePacket, subject_id: str) -> tuple[Observation, ...]:
    """Detachable evidence that was not confirmed as belonging to this subject.

    Includes both ``UNKNOWN`` and ``CONFIRMED_DETACHED``: neither can carry a verdict. They
    are reported differently, because "I could not trace the branch" asks for a different
    photograph than "this leaf was lying on the ground".
    """
    return tuple(
        o
        for o in contextual_observations_for(evidence, subject_id)
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
