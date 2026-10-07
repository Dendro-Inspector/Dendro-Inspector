"""Taxonomic vocabulary and knowledge-card contracts.

Species and genera are *data*, not procedural agents (see ``docs/architecture.md``).
A taxon card declares which features support it and which taxa it is confused with; it
never contains identification logic.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Any

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


class ValueRefinement(Contract):
    """One value that is a narrower reading of another on the same feature path."""

    feature: FeaturePath
    value: ValueToken
    broader: ValueToken

    @model_validator(mode="after")
    def _a_value_cannot_refine_itself(self) -> ValueRefinement:
        if self.value == self.broader:
            msg = f"{self.feature}: {self.value!r} cannot be a narrower reading of itself"
            raise ValueError(msg)
        return self


class ValueVocabulary(Contract):
    """Which values describe one reading of an organ at two levels of detail.

    A card's strong positives are also, unavoidably, a statement about what the taxon does
    *not* look like: read the card's decisive feature and get a different value, and that is
    disagreement. The failure this contract fixes is that string inequality was standing in
    for disagreement, so an apricot denied that the fruit was a drupe and a sycamore leaf
    denied that the leaf was palmate-lobed.

    Deliberately not derived from the strings. ``compound_pinnate_large_leaflets`` happens
    to contain ``compound_pinnate``, but ``apricot`` contains nothing of ``drupe``, and a
    prefix rule would silently relate values that merely share a word. Every relation is
    declared, cited, and reviewable — the same standard the cards are held to.

    Not a matching rule: values here are never promoted to hits. The relation only removes
    a veto, so the more specific reading still has to appear on a card to support anything.
    """

    refinements: tuple[ValueRefinement, ...] = ()
    provenance: Provenance

    @model_validator(mode="before")
    @classmethod
    def _accept_the_nested_mapping(cls, data: Any) -> Any:
        """Read ``{feature: {value: broader}}``, which is how the YAML file is written.

        Flat rows are what the rest of the schemas look like; a nested mapping is what a
        person reading the file wants, because it groups a path's relations together.
        """
        if not isinstance(data, dict):
            return data
        refinements = data.get("refinements")
        if not isinstance(refinements, dict):
            return data
        rows = [
            {"feature": feature, "value": value, "broader": broader}
            for feature, pairs in refinements.items()
            if isinstance(pairs, dict)
            for value, broader in pairs.items()
        ]
        return {**data, "refinements": rows}

    @model_validator(mode="after")
    def _the_relation_is_acyclic(self) -> ValueVocabulary:
        for row in self.refinements:
            seen = {row.value}
            current = row.broader
            while (nxt := self._broader(row.feature, current)) is not None:
                if current in seen:
                    msg = f"{row.feature}: {current!r} is a narrower reading of itself"
                    raise ValueError(msg)
                seen.add(current)
                current = nxt
        return self

    def _broader(self, feature: str, value: str) -> str | None:
        for row in self.refinements:
            if row.feature == feature and row.value == value:
                return row.broader
        return None

    def _with_broader_readings(self, feature: str, value: str) -> frozenset[str]:
        chain = {value}
        current = value
        while (broader := self._broader(feature, current)) is not None and broader not in chain:
            chain.add(broader)
            current = broader
        return frozenset(chain)

    def compatible(self, feature: str, observed: str, declared: str) -> bool:
        """Whether two readings of ``feature`` can describe the same organ.

        True when they are equal, or when either is a narrower reading of the other —
        directly or through a chain. Both directions count: a packet may carry the general
        word for a detail the card names precisely, or the precise word for a general one.
        Neither is a denial; only one of them is *support*, and that is matching's job.
        """
        return (
            observed == declared
            or declared in self._with_broader_readings(feature, observed)
            or observed in self._with_broader_readings(feature, declared)
        )


#: A vocabulary declaring no relations: every distinct value disagrees with every other.
#: The fail-closed default, so a caller that never loaded the file keeps the older, stricter
#: behaviour instead of silently admitting more.
NO_VALUE_RELATIONS = ValueVocabulary(
    provenance=Provenance(
        source="No declared value relations",
        source_type=SourceType.INFERRED,
    )
)


#: Feature families the evidence hierarchy places at bark tier. Declared here because
#: `schemas` must not import from `knowledge`; a contract test asserts this set is exactly
#: the bark-tier families `evidence_hierarchy` recognises, so the two cannot drift.
_BARK_TIER_FAMILIES: frozenset[str] = frozenset({"bark", "inner_bark", "lenticels"})

#: Colour suffixes, mirrored from `evidence_hierarchy` for the same reason and under the
#: same contract test. Colour is supporting evidence however favourable the photograph, so
#: no colour reading may carry a confidence exception.
_COLOUR_SUFFIXES: tuple[str, ...] = (".colour", ".color", ".tone")


class ExceptionCeiling(StrEnum):
    """How far one declared diagnostic reading may lift a claim.

    Distinct from :class:`Confidence`, which stays three-valued because a model is asked for
    three levels. ``VERY_HIGH`` is the same ordinal confidence as ``HIGH`` plus the top
    display band: the domain prompt writes 95-100 for a handful of named readings, and a
    band is the only place that distinction is honest.
    """

    MEDIUM = "medium"
    HIGH = "high"
    VERY_HIGH = "very_high"


class ConfidenceException(Contract):
    """One reading a card declares strong enough to lift the evidence-tier ceiling.

    The hierarchy's ceilings are the right default and the wrong absolute: bark caps at
    ``LOW`` because "definitely an oak, from the bark" is the most common way this kind of
    system embarrasses itself, and section 6 of the domain prompt then puts characteristic
    white papery birch bark among its 95-100 examples. Both are true. A default with
    declared, per-value exceptions is the shape that holds both; a global loosening is not.

    Every field narrows. ``requires`` is opt-in per feature *and* value, and every pair must
    also be a strong positive on the card, so a card cannot exempt evidence it does not
    otherwise call decisive. ``max_resolution`` is the narrowest claim the exception can
    carry, because "the genus is birch" and "the species is silver birch" are not the same
    assertion from the same bark. The exception raises a ceiling and never lowers one, it
    cannot apply to a colour reading, and it is refused outright when the same evidence
    contradicts the card it is lifting.
    """

    requires: tuple[FeatureExpectation, ...] = Field(min_length=1)
    max_resolution: Resolution
    ceiling: ExceptionCeiling
    note: ShortText | None = None
    provenance: Provenance | None = Field(
        default=None,
        description=(
            "Overrides the card's provenance for this exception. An exception is a "
            "confidence policy claim, not a feature rule, and rarely shares a source with "
            "the rules it lifts."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _accept_the_requires_mapping(cls, data: Any) -> Any:
        """Read ``requires: {feature: value}``, or ``{feature: [value, ...]}``."""
        if not isinstance(data, dict):
            return data
        requires = data.get("requires")
        if not isinstance(requires, dict):
            return data
        rows = [
            {"feature": feature, "values": tuple(value) if isinstance(value, list) else (value,)}
            for feature, value in requires.items()
        ]
        return {**data, "requires": rows}

    @model_validator(mode="after")
    def _the_exception_is_answerable(self) -> ConfidenceException:
        if self.max_resolution is Resolution.UNKNOWN:
            msg = "a confidence exception cannot apply at resolution=unknown"
            raise ValueError(msg)
        for expectation in self.requires:
            if expectation.feature.endswith(_COLOUR_SUFFIXES):
                msg = (
                    f"a confidence exception cannot rest on a colour reading; "
                    f"{expectation.feature!r} is one"
                )
                raise ValueError(msg)
        features = [expectation.feature for expectation in self.requires]
        if len(set(features)) != len(features):
            msg = f"a confidence exception names {features} twice; one row per feature"
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
    required_for_high_confidence: tuple[ValueToken, ...] = Field(
        default=(),
        description=(
            "Evidence a claim at this card's own resolution cannot be strong without. Each "
            "entry is a requirement expression: canonical feature paths or feature families "
            "joined by `_and_` and `_or_`, where `_and_` binds tighter — "
            "`leaf.underside_and_leaf.arrangement_or_fruit.type`. "
            "`knowledge.taxon_cards.requirement_selectors` is the grammar's one definition; "
            "a selector no observable feature can match fails a contract test."
        ),
    )
    confidence_exceptions: tuple[ConfidenceException, ...] = Field(
        default=(),
        description=(
            "Readings this card declares strong enough to lift the evidence-tier confidence "
            "ceiling, each with the narrowest claim it may carry. Opt-in per feature *and* "
            "value: `bark.pattern = white_papery_with_black_marks` earns one, "
            "`bark.texture = smooth_grey` does not, and appearing among a card's strong "
            "positives is not sufficient on its own. Every required pair must also appear in "
            "`strong_positive_features`."
        ),
    )
    follow_up_evidence: tuple[ValueToken, ...] = ()
    value_vocabulary: ValueVocabulary = Field(
        default=NO_VALUE_RELATIONS,
        description=(
            "Which values describe one organ at two levels of detail, composed by the "
            "loader from `knowledge/vocabulary.yaml`. Not card data: a card file that "
            "declares it fails a contract test, because the relation between `drupe` and "
            "`apricot` cannot be one thing on the Prunus card and another on the apricot "
            "card. The default declares no relations, so a card built without the loader "
            "keeps the stricter behaviour rather than silently admitting more."
        ),
    )
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

        # A confidence exception may only rest on evidence this card already calls decisive,
        # and may not claim past what the card itself supports. Validated here rather than
        # trusted, because the whole point of the ceilings it lifts is that these are the
        # claims easiest to overstate.
        strong = {
            (expectation.feature, value)
            for expectation in self.strong_positive_features
            for value in expectation.values
        }
        for exception in self.confidence_exceptions:
            for expectation in exception.requires:
                missing = sorted(
                    value
                    for value in expectation.values
                    if (expectation.feature, value) not in strong
                )
                if missing:
                    msg = (
                        f"confidence_exceptions for {self.taxon_id!r} must also appear in "
                        f"strong_positive_features; {expectation.feature!r} lacks {missing}"
                    )
                    raise ValueError(msg)
            narrowest = max(resolution_rank(supported) for supported in self.supported_resolution)
            if resolution_rank(exception.max_resolution) > narrowest:
                msg = (
                    f"confidence_exceptions for {self.taxon_id!r} may not reach "
                    f"{exception.max_resolution.value}; the card supports "
                    f"{[r.value for r in self.supported_resolution]}"
                )
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
    photo: ValueToken | None = Field(
        default=None,
        description=(
            "Which of the card's recommended follow-up photographs would resolve this "
            "feature. None means no photograph in this card can: needle persistence needs a "
            "second visit in another season, not a better macro. The binding is what lets "
            "the planner skip a photograph whose feature is already resolved."
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

    @model_validator(mode="after")
    def _photo_bindings_name_a_recommended_photo(self) -> ComparisonCard:
        """A discriminator may only point at a photograph this card actually recommends.

        Otherwise the binding is a second, unreviewed list of photograph targets living
        inside the first one.
        """
        for difference in self.decisive_differences:
            if difference.photo is not None and (
                difference.photo not in self.recommended_follow_up_photos
            ):
                msg = (
                    f"{self.comparison_id}: {difference.feature} points at photo "
                    f"{difference.photo!r}, which is not in recommended_follow_up_photos"
                )
                raise ValueError(msg)
        return self


class RegionalPack(Contract):
    """Which taxa are plausible in a region, and which regional assumptions are unsafe."""

    region_id: Identifier
    display_name: str = Field(min_length=1, max_length=120)
    likely_taxa: tuple[Identifier, ...] = ()
    unlikely_taxa: tuple[Identifier, ...] = ()
    notes: tuple[ShortText, ...] = ()
    placeholder_content: bool = True
