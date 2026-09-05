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
    """

    CONFIRMED_ATTACHED = "confirmed_attached"
    CONFIRMED_DETACHED = "confirmed_detached"
    UNKNOWN = "unknown"


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
    image_id: Identifier | None = None
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
        if self.source_component_id is not None:
            msg = "generated observations must omit source_component_id"
            raise ValueError(msg)
        return self


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
    lighting: ValueToken | None = None
    white_balance: ValueToken | None = None
    scale: ScaleQuality = ScaleQuality.ABSENT
    notes: ShortText | None = None


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
    context_limitations: tuple[ValueToken, ...] = ()
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
