"""Evidence contracts.

The single most important distinction in this project lives here: an **observation** is
something directly visible or explicitly supplied, an **inference** is a claim derived
from observations. They are separate types so that an inference cannot be stored where an
observation belongs, and so that a reviewer can always ask "which visible thing supports
this?" and get a referential answer instead of a paragraph.

Two further distinctions the extractor must preserve:

* ``NOT_VISIBLE`` (the structure could not be resolved) is not ``absent_features``
  (the structure is judged genuinely absent). Treating the first as the second is how a
  photograph of a shaded trunk becomes a confident negative claim.
* scale that is ``ABSENT`` is not scale that is ``APPROXIMATE``.
* a taxonomic subject is not one of its anatomical components. Attached branches, bark
  zones and wood surfaces may name a parent subject, then deterministic normalization
  folds their observations into that identity before any candidate is admitted.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
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

#: Feature families whose evidence can physically belong to a different tree.
#: Kept here (rather than imported from ``knowledge.evidence_hierarchy``) so that
#: ``schemas`` stays at the bottom of the dependency graph and depends on nothing.
#: ``tests/contract/test_data_contract.py`` asserts the two definitions stay in step.
_DETACHABLE_FAMILIES: frozenset[str] = frozenset(
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

#: Feature families that describe exposed wood rather than the outside of a standing tree.
#: Surface provenance is part of their authority: a split face cannot prove end-grain anatomy.
_WOOD_SURFACE_FAMILIES: frozenset[str] = frozenset(
    {
        "wood",
        "cut",
        "rings",
        "pores",
        "rays",
        "resin",
        "heartwood",
        "sapwood",
        "inner_bark",
    }
)


def requires_wood_surface(feature: str) -> bool:
    """Whether a feature describes exposed wood whose physical surface matters."""
    return feature.split(".", 1)[0] in _WOOD_SURFACE_FAMILIES


def _surface_payload(data: Any, *, require_explicit: bool) -> Any:
    """Normalize legacy wood observations or reject incomplete generated output."""
    if not isinstance(data, Mapping):
        return data
    feature = data.get("feature")
    if not isinstance(feature, str):
        return data

    payload = dict(data)
    surface = payload.get("wood_surface")
    if requires_wood_surface(feature):
        if surface is None:
            if require_explicit:
                msg = f"generated wood observation on feature {feature!r} must set wood_surface"
                raise ValueError(msg)
            payload["wood_surface"] = WoodSurface.UNKNOWN
    elif surface is not None:
        msg = f"observation on non-wood feature {feature!r} must omit wood_surface"
        raise ValueError(msg)
    return payload


class ObservationSource(StrEnum):
    IMAGE = "image"
    USER = "user"
    METADATA = "metadata"
    EXTERNAL_CONTEXT = "external_context"


class Visibility(StrEnum):
    """How well the feature could actually be seen."""

    CLEAR = "clear"
    PARTIAL = "partial"
    OBSCURED = "obscured"
    NOT_VISIBLE = "not_visible"


class Reliability(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class AttachmentStatus(StrEnum):
    """Whether detachable evidence belongs to the subject it is recorded against.

    Three states, not two. A boolean collapses "I could not tell" into "definitely not
    attached", and the next reader treats ``false`` as a positive finding of detachment —
    which is a different, stronger claim than the extractor was able to make.

    Only ``CONFIRMED_ATTACHED`` lets evidence count at its own tier. ``UNKNOWN`` and
    ``CONFIRMED_DETACHED`` both demote to context, but they say different things in the
    report and call for different photographs.

    ``USER_ATTESTED`` is never written by a model. Deterministic code sets it when the
    uploader states that named photographs show parts of one photographed tree
    (``CaseInput.part_attestations``; domain prompt section 3: the user may know the
    fruits). Such evidence counts, but only as capped support — it cannot carry high
    confidence or settle a decisive requirement. The general field-context flag does not
    set it, and neither does submitting several photographs together.
    """

    CONFIRMED_ATTACHED = "confirmed_attached"
    CONFIRMED_DETACHED = "confirmed_detached"
    UNKNOWN = "unknown"
    USER_ATTESTED = "user_attested"


class InferenceStrength(StrEnum):
    WEAK = "weak"
    MEDIUM = "medium"
    STRONG = "strong"


class ScaleQuality(StrEnum):
    EXACT = "exact"
    APPROXIMATE = "approximate"
    ABSENT = "absent"


class WoodSurface(StrEnum):
    """The physical surface on which a wood observation was made."""

    PREPARED_END_GRAIN = "prepared_end_grain"
    ROUGH_END_GRAIN = "rough_end_grain"
    SPLIT_FACE = "split_face"
    PLANED_FACE = "planed_face"
    UNKNOWN = "unknown"


class SubjectKind(StrEnum):
    STANDING_TREE = "standing_tree"
    LOG = "log"
    SPLIT_WOOD = "split_wood"
    MATERIAL_GROUP = "material_group"
    BRANCH = "branch"
    DETACHED_PART = "detached_part"
    BARK_SURFACE = "bark_surface"
    WOOD_SURFACE = "wood_surface"
    UNKNOWN = "unknown"


class Subject(Contract):
    """One taxonomic identity scope or a component of one.

    Conclusions are scoped per subject; evidence must never leak between subjects.
    ``parent_subject_id`` is only for a component visibly belonging to the same organism
    or material sample. It is not containment: a neighbouring branch or one piece in a
    mixed pile remains an independent root subject.
    """

    subject_id: Identifier
    kind: SubjectKind = SubjectKind.UNKNOWN
    description: ShortText | None = None
    image_ids: tuple[Identifier, ...] = ()
    parent_subject_id: Identifier | None = Field(
        default=None,
        description=(
            "Identity root this anatomical component visibly belongs to. Omit for an "
            "independent tree, log, detached part or material sample."
        ),
    )


class Observation(Contract):
    """Something directly visible in an image, or explicitly supplied by the user."""

    observation_id: Identifier
    feature: FeaturePath
    value: ValueToken
    subject_id: Identifier
    source_component_id: Identifier | None = Field(
        default=None,
        description=(
            "Original anatomical component before deterministic identity normalization. "
            "Provider-generated observations must omit it; code supplies it during collapse."
        ),
    )
    source: ObservationSource
    visibility: Visibility = Visibility.CLEAR
    reliability: Reliability = Reliability.MEDIUM
    image_id: Identifier | None = Field(
        default=None,
        description="Required when `source` is `image`: the id of the photograph it was read from.",
    )
    region: str | None = Field(default=None, max_length=120)
    attachment: AttachmentStatus | None = Field(
        default=None,
        description=(
            "For detachable evidence (leaf, fruit, cone, needle, branch): whether the "
            "feature is visibly attached to THIS subject's trunk. Required for those "
            "families; must be None for bark, wood, trunk and context features."
        ),
    )
    wood_surface: WoodSurface | None = Field(
        default=None,
        description=(
            "For wood/cut/anatomy observations: prepared or rough end grain, a split or "
            "planed longitudinal face, or unknown. Omitted legacy values become unknown."
        ),
    )
    notes: ShortText | None = None

    @model_validator(mode="before")
    @classmethod
    def _wood_surface_contract(cls, data: Any) -> Any:
        return _surface_payload(data, require_explicit=False)

    @model_validator(mode="after")
    def _image_source_needs_image(self) -> Observation:
        if self.source is ObservationSource.IMAGE and self.image_id is None:
            msg = "observation with source=image must name the image_id it came from"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _detachable_evidence_must_answer_the_attachment_question(self) -> Observation:
        """Force an explicit answer to "does this leaf grow on this trunk?".

        A leaf in the corner of the frame may belong to the neighbouring tree, and an
        unanswered question defaults to a hopeful yes in practice. Making the field
        mandatory for detachable families turns that into a decision someone had to make —
        and ``UNKNOWN`` is an honest, available answer, distinct from "definitely detached".
        """
        detachable = self.feature.split(".", 1)[0] in _DETACHABLE_FAMILIES
        if detachable and self.attachment is None:
            msg = (
                f"observation {self.observation_id!r} on detachable feature "
                f"{self.feature!r} must set attachment "
                f"({'|'.join(status.value for status in AttachmentStatus)})"
            )
            raise ValueError(msg)
        if not detachable and self.attachment is not None:
            msg = (
                f"observation {self.observation_id!r} on feature {self.feature!r} is not "
                "detachable; attachment must be omitted"
            )
            raise ValueError(msg)
        return self

    @property
    def is_attached(self) -> bool:
        """True only when attachment was positively confirmed."""
        return self.attachment is AttachmentStatus.CONFIRMED_ATTACHED


class GeneratedObservation(Observation):
    """Extractor output: new wood observations must state their surface explicitly."""

    @model_validator(mode="before")
    @classmethod
    def _wood_surface_contract(cls, data: Any) -> Any:
        return _surface_payload(data, require_explicit=True)

    @model_validator(mode="after")
    def _component_provenance_is_internal(self) -> GeneratedObservation:
        if self.attachment is AttachmentStatus.USER_ATTESTED:
            msg = (
                "attachment `user_attested` is set by deterministic code from the uploader's "
                "explicit statement; report what the photograph shows (confirmed_attached, "
                "confirmed_detached or unknown)"
            )
            raise ValueError(msg)
        if self.source_component_id is not None:
            msg = "generated observations must omit source_component_id"
            raise ValueError(msg)
        return self


class AttestationStatus(StrEnum):
    """What one uploader statement came to once the photographs were read."""

    APPLIED = "applied"
    NO_TREE = "no_tree"
    """No standing tree was found in the photographs the statement names as the tree."""
    AMBIGUOUS_TREE = "ambiguous_tree"
    """More than one standing tree was found there, so the statement names none of them."""
    NOTHING_CREDITED = "nothing_credited"
    """The tree was found, and no reading in the part photographs qualified."""


class AttestationSkipReason(StrEnum):
    """Why a reading in an attested photograph was left where the extractor filed it."""

    NOT_VISIBLE = "not_visible"
    """Obscured or not visible: the statement cannot vouch for what was not seen."""
    ATTACHMENT_UNKNOWN = "attachment_unknown"
    """The extractor could not tell what the part belongs to (frame edge, background)."""
    LOW_RELIABILITY = "low_reliability"
    """The reading itself is doubtful; whose part it is does not repair that."""
    OTHER_IDENTITY = "other_identity"
    """Filed under another tree, a log or a wood sample: an identity of its own."""


class CreditedObservation(Contract):
    """One reading credited to a tree on the uploader's word, and where it came from."""

    observation_id: Identifier
    from_subject_id: Identifier
    read_as: AttachmentStatus = Field(
        description="What the extractor reported before the statement was applied."
    )


class SkippedObservation(Contract):
    """One reading in an attested photograph that the statement did not move."""

    observation_id: Identifier
    subject_id: Identifier
    reason: AttestationSkipReason


class AttestationOutcome(Contract):
    """The record of one ``PartAttestation``: who said it, about what, and what it changed.

    Kept beside the evidence revision it produced, so a credited reading can be traced to
    the statement behind it and a refused one keeps its reason. The author's own words stay
    with the case input and are not copied here.
    """

    attestation_id: Identifier
    source: str = Field(max_length=40, description="`AttestationSource` of the statement.")
    part_image_ids: tuple[Identifier, ...]
    tree_image_ids: tuple[Identifier, ...]
    status: AttestationStatus
    target_subject_id: Identifier | None = None
    credited: tuple[CreditedObservation, ...] = ()
    skipped: tuple[SkippedObservation, ...] = ()


class Inference(Contract):
    """A claim derived from observations. Never stored as an observation."""

    inference_id: Identifier
    claim: ValueToken
    derived_from: tuple[Identifier, ...] = Field(
        min_length=1,
        description="Observation ids. An inference with no observable basis is not admissible.",
    )
    strength: InferenceStrength = InferenceStrength.MEDIUM
    limitations: tuple[ValueToken, ...] = ()


class ImageLimitation(Contract):
    """A property of the photograph that caps what can be concluded from it."""

    image_id: Identifier
    lighting: ValueToken | None = Field(
        default=None,
        description=(
            "One token for the light, e.g. `even_daylight`, `overcast_flat`, `even_shade`, "
            "`dappled`, `backlit`, `late_afternoon_warm`. Not a sentence: describe effects "
            "in `notes`."
        ),
    )
    white_balance: ValueToken | None = Field(
        default=None,
        description=(
            "One token for colour rendering, e.g. `neutral`, `uncorrected`, `warm_cast`, "
            "`cool_cast`, `green_cast`. Not a sentence: what it does to colour readings "
            "goes in `notes`."
        ),
    )
    scale: ScaleQuality = ScaleQuality.ABSENT
    notes: ShortText | None = Field(
        default=None, description="Free text for anything the tokens above cannot say."
    )


#: Values that report a failure to read a feature rather than a reading of it. An extractor
#: that fills `bark.texture` with `not_resolvable` is saying "I looked and could not tell",
#: which is silence on that feature — never a positive observation that disagrees with a
#: card. Keeping these out of value comparisons is what stops absence of evidence from being
#: promoted into evidence of absence.
UNREADABLE_VALUES: frozenset[str] = frozenset(
    {
        "not_resolvable",
        "unresolvable",
        "not_assessable",
        "not_visible",
        "indeterminate",
        "unknown",
        "obscured",
        "absent",
        "not_determinable",
        "undeterminable",
        "not_countable",
        "uncountable",
        # The commonest failed-read words in recorded runs (2026-11-11 coverage audit: 37 of
        # 545 observations), none of which were recognised: a trusted "unresolved" on a
        # card's strong path read as disagreement and could veto the card.
        "unresolved",
        "not_resolved",
        "unreadable",
        "not_seen",
        "not_in_frame",
        "not_visible_on_subject",
        "none_seen",
        "none_visible",
    }
)

#: A token that *ends* by saying the feature could not be read is a failure to read it,
#: whatever comes first: ``numerous_not_countable``, ``leaflets_not_resolvable``. Found on
#: a live walnut upload, where ``leaflet.count = numerous_not_countable`` was taken as a
#: reading that disagreed with the card's ``fifteen_to_twenty_three`` and vetoed the card.
_UNREADABLE_SUFFIX = re.compile(
    r"(?:^|_)(?:not_[a-z]+(?:able|ible)|unresolvable|undeterminable|uncountable|indeterminate"
    r"|unresolved|not_resolved|unreadable)$"
)


def is_positive_reading(value: str) -> bool:
    """Whether an observation's value is an actual reading of the feature."""
    return value not in UNREADABLE_VALUES and _UNREADABLE_SUFFIX.search(value) is None


class KnowledgeCoverage(Contract):
    """How much of one packet's trusted evidence the knowledge cards can represent.

    A gap here measures the cards, never the photograph or the model. An observation that
    lands outside the card vocabulary was seen, believed and carried — and then had nowhere
    to go, because no card declares the feature it names.

    The split matters more than the count. Colour and the project's explicitly
    insufficient-alone features are *meant* to be unmatchable, so counting them as coverage
    gaps would inflate the number with entries no card edit could ever recover. Only
    :attr:`potential_gap_evidence_ids` names evidence a card author could act on.
    """

    observations_total: int = Field(default=0, ge=0)
    intentionally_weak_evidence_ids: tuple[Identifier, ...] = Field(
        default=(),
        description="Unmatchable by existing policy — colour and insufficient-alone features.",
    )
    potential_gap_evidence_ids: tuple[Identifier, ...] = Field(
        default=(),
        description="Unmatchable evidence a card author could recover by editing the cards.",
    )
    features_absent_from_all_cards: tuple[FeaturePath, ...] = Field(
        default=(),
        description="Gap features no card declares at all. A missing card or a missing rule.",
    )
    features_with_unknown_values: tuple[FeaturePath, ...] = Field(
        default=(),
        description="Gap features some card declares, with a value no card lists.",
    )

    @property
    def unmatchable_total(self) -> int:
        return len(self.intentionally_weak_evidence_ids) + len(self.potential_gap_evidence_ids)

    @property
    def has_potential_gap(self) -> bool:
        """Whether a card author could recover any of this run's discarded evidence."""
        return bool(self.potential_gap_evidence_ids)


class EvidencePacket(Contract):
    """Everything the graph believes it can see, with referential integrity enforced."""

    subjects: tuple[Subject, ...] = Field(default=(), max_length=16)
    observations: tuple[Observation, ...] = ()
    inferences: tuple[Inference, ...] = ()
    absent_features: tuple[FeaturePath, ...] = Field(
        default=(),
        description="Features judged genuinely absent — NOT merely unresolvable in the frame.",
    )
    image_limitations: tuple[ImageLimitation, ...] = ()
    context_limitations: tuple[ValueToken, ...] = Field(
        default=(),
        description=(
            "Tokens, one per limitation, e.g. `location_unknown`, `season_unknown`, "
            "`crown_not_visible`, `no_scale_reference`, `foliage_not_traceable_to_subject`, "
            "`no_fruit_or_seed_visible`. Never sentences."
        ),
    )
    possible_multiple_taxa: bool = False
    instruction_like_content_detected: bool = False

    @model_validator(mode="after")
    def _referential_integrity(self) -> EvidencePacket:
        subject_ids = {subject.subject_id for subject in self.subjects}
        parent_by_id = {subject.subject_id: subject.parent_subject_id for subject in self.subjects}
        observation_ids = {observation.observation_id for observation in self.observations}
        limitation_ids = {limitation.image_id for limitation in self.image_limitations}

        if len(subject_ids) != len(self.subjects):
            msg = "duplicate subject_id in evidence packet"
            raise ValueError(msg)
        if len(observation_ids) != len(self.observations):
            msg = "duplicate observation_id in evidence packet"
            raise ValueError(msg)
        if len(limitation_ids) != len(self.image_limitations):
            msg = "duplicate image limitation image_id in evidence packet"
            raise ValueError(msg)

        for subject in self.subjects:
            parent_id = subject.parent_subject_id
            if parent_id is not None and parent_id not in subject_ids:
                msg = (
                    f"subject {subject.subject_id!r} references unknown parent subject "
                    f"{parent_id!r}"
                )
                raise ValueError(msg)

            visited = {subject.subject_id}
            while parent_id is not None:
                if parent_id in visited:
                    msg = f"subject parent cycle includes {parent_id!r}"
                    raise ValueError(msg)
                visited.add(parent_id)
                parent_id = parent_by_id[parent_id]

        for observation in self.observations:
            if observation.subject_id not in subject_ids:
                msg = (
                    f"observation {observation.observation_id!r} references unknown subject "
                    f"{observation.subject_id!r}"
                )
                raise ValueError(msg)

        inference_ids: set[str] = set()
        for inference in self.inferences:
            if inference.inference_id in inference_ids:
                msg = f"duplicate inference_id {inference.inference_id!r}"
                raise ValueError(msg)
            inference_ids.add(inference.inference_id)
            unknown = set(inference.derived_from) - observation_ids
            if unknown:
                msg = (
                    f"inference {inference.inference_id!r} derives from unknown observations: "
                    f"{sorted(unknown)}"
                )
                raise ValueError(msg)
        return self

    def validate_image_references(self, available_image_ids: frozenset[str]) -> None:
        """Bind packet references to the code-owned image scope of the extraction call."""
        referenced = {
            observation.image_id
            for observation in self.observations
            if observation.image_id is not None
        }
        referenced.update(image_id for subject in self.subjects for image_id in subject.image_ids)
        referenced.update(limitation.image_id for limitation in self.image_limitations)
        unknown = referenced - available_image_ids
        if unknown:
            msg = f"evidence references unavailable image ids: {sorted(unknown)}"
            raise ValueError(msg)

    def identity_root_id(self, subject_id: str) -> str:
        """Return the independent identity root for a validated subject id."""
        parent_by_id = {subject.subject_id: subject.parent_subject_id for subject in self.subjects}
        current = subject_id
        while (parent_id := parent_by_id[current]) is not None:
            current = parent_id
        return current

    def collapse_subject_components(self) -> EvidencePacket:
        """Fold typed anatomical components into their identity roots.

        The model proposes the parent relation; schema validation proves that relation is
        closed and acyclic; this method performs the only downstream projection. After it,
        every existing same-subject anti-leakage check continues to operate unchanged.
        """
        if not any(subject.parent_subject_id is not None for subject in self.subjects):
            return self

        roots = tuple(subject for subject in self.subjects if subject.parent_subject_id is None)
        observations: list[Observation] = []
        for observation in self.observations:
            root_id = self.identity_root_id(observation.subject_id)
            updates = {"subject_id": root_id}
            if root_id != observation.subject_id:
                updates["source_component_id"] = observation.subject_id
            observations.append(observation.model_copy(update=updates))
        payload = self.model_dump(mode="python")
        payload.update({"subjects": roots, "observations": tuple(observations)})
        return EvidencePacket.model_validate(payload)

    def observations_for(self, subject_id: str) -> tuple[Observation, ...]:
        """Return only this subject's observations — the anti-leakage accessor."""
        return tuple(o for o in self.observations if o.subject_id == subject_id)

    def visible_observations_for(self, subject_id: str) -> tuple[Observation, ...]:
        """Return this subject's observations that were actually resolvable in the frame."""
        return tuple(
            o
            for o in self.observations_for(subject_id)
            if o.visibility is not Visibility.NOT_VISIBLE
        )

    def has_feature(self, subject_id: str, feature_prefix: str) -> bool:
        """Whether any resolvable observation for the subject touches ``feature_prefix``."""
        return any(
            o.feature == feature_prefix or o.feature.startswith(f"{feature_prefix}.")
            for o in self.visible_observations_for(subject_id)
        )


class GeneratedEvidencePacket(EvidencePacket):
    """Provider response contract with strict surface provenance for new observations."""

    observations: tuple[GeneratedObservation, ...] = ()

    def to_evidence_packet(self) -> EvidencePacket:
        """Return the canonical persistence/runtime contract after generated-output checks."""
        packet = EvidencePacket.model_validate(self.model_dump(mode="python"))
        return packet.collapse_subject_components()


class TargetedEvidencePacket(GeneratedEvidencePacket):
    """The extractor's response when it is also asked which subject the photographs are of.

    Requested only with ``GraphConfig.target_selection = extractor``; every other run uses
    :class:`GeneratedEvidencePacket` and its schema unchanged. The proposal is a reading of
    how the photographs are framed. It is not evidence: it supports no taxon and selects
    none, and code decides what it designates.
    """

    main_subject_id: Identifier | None = Field(
        default=None,
        description=(
            "The `subject_id` of the one subject these photographs are of: the tree, log or "
            "part the photographer framed. Omit it when several subjects are equally the "
            "subject or you cannot tell. A subject standing behind or beside the main one "
            "is not it."
        ),
    )

    def generated(self) -> GeneratedEvidencePacket:
        """The same packet without the proposal: what the evidence history stores."""
        return GeneratedEvidencePacket.model_validate(
            self.model_dump(mode="python", exclude={"main_subject_id"})
        )

    def to_evidence_packet(self) -> EvidencePacket:
        return self.generated().to_evidence_packet()

    def main_root_id(self) -> str | None:
        """The identity root the proposal names; a component stands for its tree.

        An id that names no subject of this packet designates nothing. The proposal is
        optional and decides no verdict, so a wrong one is dropped rather than made a
        reason to reject an otherwise valid extraction.
        """
        listed = {subject.subject_id for subject in self.subjects}
        if self.main_subject_id is None or self.main_subject_id not in listed:
            return None
        return self.identity_root_id(self.main_subject_id)
