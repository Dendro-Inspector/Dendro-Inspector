"""Case input contracts.

Everything here is untrusted: filenames, captions, EXIF, user text and declared object
type all originate outside the system. The input guard (``nodes/input_guard.py``) records
instruction-like signals, not a safety verdict. Nodes receive case context as labelled
data; deterministic evidence and claim rules apply whether or not a signal was detected.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import Field, model_validator

from dendro_inspector.schemas.base import Contract, Identifier, ShortText


class Season(StrEnum):
    SPRING = "spring"
    SUMMER = "summer"
    AUTUMN = "autumn"
    WINTER = "winter"
    UNKNOWN = "unknown"


class DeclaredObjectType(StrEnum):
    """What the user says the photograph shows. A claim, not a fact."""

    STANDING_TREE = "standing_tree"
    LOG = "log"
    SPLIT_FIREWOOD = "split_firewood"
    BARK = "bark"
    LEAF = "leaf"
    NEEDLE = "needle"
    FRUIT = "fruit"
    CONE = "cone"
    WOOD = "wood"
    BRANCH = "branch"
    SEED = "seed"
    UNKNOWN = "unknown"


class ImageRef(Contract):
    """A reference to an image. Bytes are never carried through graph state."""

    image_id: Identifier
    path: Path
    media_type: str = Field(default="image/jpeg", max_length=80)
    caption: ShortText | None = Field(
        default=None,
        description="User- or file-supplied caption. Untrusted text.",
    )

    @property
    def exists(self) -> bool:
        """Whether the referenced file is readable from this process."""
        return self.path.is_file()


class CaseInput(Contract):
    """One identification request."""

    case_id: Identifier
    images: tuple[ImageRef, ...] = Field(default=(), max_length=16)
    user_text: str | None = Field(default=None, max_length=4000)
    user_challenges_previous_result: bool = Field(
        default=False,
        description=(
            "The caller explicitly requests reconsideration of a previous result. Not inferred "
            "from free text and not proof that the previous result was wrong. Requests "
            "independent review when the graph has a claim to review, and restrains tone."
        ),
    )
    user_claim: str | None = Field(
        default=None,
        max_length=120,
        description=(
            "The taxon the user proposes, if any ('sosna', 'pinus', 'oak'). Untrusted, and "
            "free text by design — people name trees in their own language. It is checked, "
            "never assumed, and never dismissed on bark alone."
        ),
    )
    user_has_field_context: bool = Field(
        default=False,
        description=(
            "The user states knowledge from the site the photograph cannot show: foliage "
            "out of frame, the fruit, where the tree was felled, its history. This is real "
            "evidence the system does not have, and it blocks aggressive contradiction."
        ),
    )
    location: str | None = Field(default=None, max_length=200)
    season: Season = Season.UNKNOWN
    habitat: str | None = Field(default=None, max_length=200)
    declared_object_type: DeclaredObjectType = DeclaredObjectType.UNKNOWN
    metadata: dict[str, str] = Field(
        default_factory=dict,
        description="Untrusted key/value context (EXIF, upload metadata, caller hints).",
    )

    @model_validator(mode="after")
    def _image_ids_are_unique(self) -> CaseInput:
        ids = [image.image_id for image in self.images]
        if len(ids) != len(set(ids)):
            msg = "duplicate image_id in case input"
            raise ValueError(msg)
        return self

    @property
    def image_ids(self) -> tuple[str, ...]:
        return tuple(image.image_id for image in self.images)
