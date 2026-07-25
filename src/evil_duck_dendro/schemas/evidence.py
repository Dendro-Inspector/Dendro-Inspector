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
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from evil_duck_dendro.schemas.base import (
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


class SubjectKind(StrEnum):
    STANDING_TREE = "standing_tree"
    LOG = "log"
    BRANCH = "branch"
    DETACHED_PART = "detached_part"
    BARK_SURFACE = "bark_surface"
    WOOD_SURFACE = "wood_surface"
    UNKNOWN = "unknown"


class Subject(Contract):
    """One physically distinct thing in the frame.

    Conclusions are scoped per subject; evidence must never leak between subjects.
    Example ids: ``foreground_log_1``, ``background_log_1``, ``standing_tree``.
    """

    subject_id: Identifier
    kind: SubjectKind = SubjectKind.UNKNOWN
    description: ShortText | None = None
    image_ids: tuple[Identifier, ...] = ()


class Observation(Contract):
    """Something directly visible in an image, or explicitly supplied by the user."""

    observation_id: Identifier
    feature: FeaturePath
    value: ValueToken
    subject_id: Identifier
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
    notes: ShortText | None = None

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
        observation_ids = {observation.observation_id for observation in self.observations}

        if len(subject_ids) != len(self.subjects):
            msg = "duplicate subject_id in evidence packet"
            raise ValueError(msg)
        if len(observation_ids) != len(self.observations):
            msg = "duplicate observation_id in evidence packet"
            raise ValueError(msg)

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
