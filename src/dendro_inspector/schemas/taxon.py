"""Taxonomic vocabulary and knowledge-card contracts.

Species and genera are *data*, not procedural agents (see ``docs/architecture.md``).
A taxon card declares which features support it and which taxa it is confused with; it
never contains identification logic.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import Field, model_validator

from dendro_inspector.schemas.base import (
    Contract,
    FeaturePath,
    Identifier,
    ShortText,
    ValueToken,
)


class Resolution(StrEnum):
    """The taxonomic level an answer is pinned to.

    Ordered from broadest to narrowest by :func:`resolution_rank`. ``UNKNOWN`` is a real
    outcome, not an error state.
    """

    FAMILY = "family"
    GENUS = "genus"
    SPECIES_GROUP = "species_group"
    SPECIES = "species"
    UNKNOWN = "unknown"


class Confidence(StrEnum):
    """Coarse confidence. Deliberately three-valued.

    Percentages imply a calibration this system does not have and cannot honestly claim
    from a photograph.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


_RESOLUTION_ORDER: dict[Resolution, int] = {
    Resolution.UNKNOWN: 0,
    Resolution.FAMILY: 1,
    Resolution.GENUS: 2,
    Resolution.SPECIES_GROUP: 3,
    Resolution.SPECIES: 4,
}

_CONFIDENCE_ORDER: dict[Confidence, int] = {
    Confidence.LOW: 0,
    Confidence.MEDIUM: 1,
    Confidence.HIGH: 2,
}


def resolution_rank(resolution: Resolution) -> int:
    """Return a sortable rank where a higher number means a narrower claim."""
    return _RESOLUTION_ORDER[resolution]


def confidence_rank(confidence: Confidence) -> int:
    """Return a sortable rank where a higher number means a stronger claim."""
    return _CONFIDENCE_ORDER[confidence]


def lower_resolution(resolution: Resolution) -> Resolution:
    """Step one level broader. ``FAMILY`` degrades to ``UNKNOWN``; ``UNKNOWN`` is a floor."""
    match resolution:
        case Resolution.SPECIES:
            return Resolution.SPECIES_GROUP
        case Resolution.SPECIES_GROUP:
            return Resolution.GENUS
        case Resolution.GENUS:
            return Resolution.FAMILY
        case Resolution.FAMILY | Resolution.UNKNOWN:
            return Resolution.UNKNOWN


def lower_confidence(confidence: Confidence) -> Confidence:
    """Step one level down. ``LOW`` is a floor."""
    match confidence:
        case Confidence.HIGH:
            return Confidence.MEDIUM
        case Confidence.MEDIUM | Confidence.LOW:
            return Confidence.LOW


class SourceType(StrEnum):
    """Where a knowledge rule came from."""

    DOMAIN_PROMPT = "domain_prompt"
    FIELD_GUIDE = "field_guide"
    LITERATURE = "literature"
    EXPERT_REVIEW = "expert_review"
    INFERRED = "inferred"


class ReviewState(StrEnum):
    UNREVIEWED = "unreviewed"
    REVIEWED = "reviewed"
    DISPUTED = "disputed"


class LifeStage(StrEnum):
    ANY = "any"
    YOUNG = "young"
    MATURE = "mature"
    OLD = "old"


class Applicability(StrEnum):
    """When a feature rule holds. Deciduous characters do not survive January."""

    ANY = "any"
    SPRING = "spring"
    SUMMER = "summer"
    AUTUMN = "autumn"
    WINTER = "winter"
    GROWING_SEASON = "growing_season"


class Provenance(Contract):
    """Where a rule came from, when it holds, and whether anyone has checked it.

    The failure this guards against is quiet and expensive: someone adds a plausible,
    well-written, wrong feature; every test still passes, because the tests check the code
    and not the botany. Provenance does not prevent that — it makes it *attributable*, so a
    later reviewer can find every rule nobody has ever verified.
    """

    source: ShortText
    source_type: SourceType
    region: str | None = Field(default=None, max_length=120)
    life_stage: LifeStage = LifeStage.ANY
    season: Applicability = Applicability.ANY
    confidence: Confidence = Confidence.LOW
    review_state: ReviewState = ReviewState.UNREVIEWED
    reviewed_by: str | None = Field(default=None, max_length=120)
    last_reviewed: date | None = None

    @model_validator(mode="after")
    def _review_claims_need_a_reviewer(self) -> Provenance:
        """A rule cannot claim review without saying who reviewed it and when."""
        if self.review_state is ReviewState.REVIEWED and not (
            self.reviewed_by and self.last_reviewed
        ):
            msg = "review_state=reviewed requires both reviewed_by and last_reviewed"
            raise ValueError(msg)
        return self


class FeatureExpectation(Contract):
    """A feature and the values that count for or against a taxon."""

    feature: FeaturePath
    values: tuple[ValueToken, ...] = Field(min_length=1)
    note: ShortText | None = None
    provenance: Provenance | None = Field(
        default=None,
        description="Overrides the card's provenance for this rule. Inherits when absent.",
    )


class TaxonIdentity(Contract):
    """Canonical identity used when a card's native taxon must be broadened.

    A broader identity is a taxonomic claim in its own right and rarely shares a source with
    the feature rules beneath it: the domain prompt names genera, so a genus identity can
    cite it, but it names no family at all. Carrying provenance here keeps "where did this
    come from?" answerable per claim instead of per file.
    """

    resolution: Resolution
    taxon_id: Identifier
    display_name: str = Field(min_length=1, max_length=120)
    provenance: Provenance | None = Field(
        default=None,
        description=(
            "Where this taxonomic placement came from. Inherits the card's provenance when "
            "absent — which is only honest for an identity the card's own source names."
        ),
    )

    @model_validator(mode="after")
    def _identity_must_be_taxonomic(self) -> TaxonIdentity:
        if self.resolution is Resolution.UNKNOWN:
            msg = "a taxon identity cannot use resolution=unknown"
            raise ValueError(msg)
        return self


class TaxonCard(Contract):
    """Structured, declarative knowledge about one taxon."""

    taxon_id: Identifier
    display_name: str = Field(min_length=1, max_length=120)
    native_resolution: Resolution
    broader_identities: tuple[TaxonIdentity, ...] = ()
    aliases: tuple[str, ...] = Field(
        default=(),
        max_length=24,
        description=(
            "Common names a user might actually type, in any language they might type them. "
            "Used only to recognise a user's own proposal — never to identify anything."
        ),
    )
    supported_resolution: tuple[Resolution, ...] = Field(min_length=1)
    strong_positive_features: tuple[FeatureExpectation, ...] = ()
    supporting_features: tuple[FeatureExpectation, ...] = ()
    contradictions: tuple[FeatureExpectation, ...] = ()
    common_confusions: tuple[Identifier, ...] = ()
    required_for_high_confidence: tuple[ValueToken, ...] = ()
    follow_up_evidence: tuple[ValueToken, ...] = ()
    provenance: Provenance
    placeholder_content: bool = Field(
        default=True,
        description=(
            "True while the card carries demonstration-grade content that has not been "
            "reviewed by a dendrologist. See docs/dataset-policy.md."
        ),
    )

    @model_validator(mode="after")
    def _identities_are_coherent(self) -> TaxonCard:
        if self.native_resolution is Resolution.UNKNOWN:
            msg = "taxon card native_resolution cannot be unknown"
            raise ValueError(msg)
        if Resolution.UNKNOWN in self.supported_resolution:
            msg = "taxon card supported_resolution cannot include unknown"
            raise ValueError(msg)
        if len(set(self.supported_resolution)) != len(self.supported_resolution):
            msg = f"duplicate supported resolution for {self.taxon_id!r}"
            raise ValueError(msg)

        resolutions = [identity.resolution for identity in self.broader_identities]
        if len(set(resolutions)) != len(resolutions):
            msg = f"duplicate broader identity resolution for {self.taxon_id!r}"
            raise ValueError(msg)
        if any(
            resolution_rank(identity.resolution) >= resolution_rank(self.native_resolution)
            for identity in self.broader_identities
        ):
            msg = (
                f"broader identities for {self.taxon_id!r} must be broader than its native identity"
            )
            raise ValueError(msg)

        taxon_ids = [self.taxon_id, *(identity.taxon_id for identity in self.broader_identities)]
        if len(set(taxon_ids)) != len(taxon_ids):
            msg = f"native and broader taxon ids for {self.taxon_id!r} must be unique"
            raise ValueError(msg)
        return self

    @property
    def native_identity(self) -> TaxonIdentity:
        """The card's own taxon, attributed to the card's own source."""
        return TaxonIdentity(
            resolution=self.native_resolution,
            taxon_id=self.taxon_id,
            display_name=self.display_name,
            provenance=self.provenance,
        )

    def identity_at_or_broader(self, resolution: Resolution) -> TaxonIdentity | None:
        """Narrowest declared identity no narrower than ``resolution`` permits."""
        if resolution is Resolution.UNKNOWN:
            return None
        identities = (self.native_identity, *self.broader_identities)
        eligible = [
            identity
            for identity in identities
            if resolution_rank(identity.resolution) <= resolution_rank(resolution)
        ]
        if not eligible:
            return None
        return max(eligible, key=lambda identity: resolution_rank(identity.resolution))

    def supports(self, resolution: Resolution) -> bool:
        """Return whether this card explicitly supports ``resolution``."""
        return resolution in self.supported_resolution


class DecisiveDifference(Contract):
    """A feature that actually separates two or more taxa.

    The confusion *edge* is symmetric — if Pinus is confused with Picea, Picea is confused
    with Pinus. The *discriminator* usually is not. A counted fascicle of two rules Picea
    out; it does not rule Pinus out for anybody. ``favours`` records that direction, so a
    reviewer can say which way the feature actually points instead of inferring it from
    prose.
    """

    feature: FeaturePath
    separates: tuple[Identifier, ...] = Field(min_length=2)
    favours: Identifier | None = Field(
        default=None,
        description=(
            "The taxon this feature points toward when present. None means the feature "
            "distinguishes the group without favouring any single member."
        ),
    )
    note: ShortText | None = None
    provenance: Provenance | None = None

    @model_validator(mode="after")
    def _favoured_taxon_must_be_separated(self) -> DecisiveDifference:
        if self.favours is not None and self.favours not in self.separates:
            msg = f"favours={self.favours!r} is not among the taxa this feature separates"
            raise ValueError(msg)
        return self


class ComparisonCard(Contract):
    """Look-alike group: what is shared, what decides, what is useless on its own."""

    comparison_id: Identifier
    taxa: tuple[Identifier, ...] = Field(min_length=2)
    shared_features: tuple[FeaturePath, ...] = ()
    decisive_differences: tuple[DecisiveDifference, ...] = ()
    insufficient_features: tuple[FeaturePath, ...] = ()
    recommended_follow_up_photos: tuple[ValueToken, ...] = ()
    provenance: Provenance
    placeholder_content: bool = True


class RegionalPack(Contract):
    """Which taxa are plausible in a region, and which regional assumptions are unsafe."""

    region_id: Identifier
    display_name: str = Field(min_length=1, max_length=120)
    likely_taxa: tuple[Identifier, ...] = ()
    unlikely_taxa: tuple[Identifier, ...] = ()
    notes: tuple[ShortText, ...] = ()
    placeholder_content: bool = True
