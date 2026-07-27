"""Case input contracts.

Everything here is untrusted: filenames, captions, EXIF, user text and declared object
type all originate outside the system. The input guard (``nodes/input_guard.py``) is the
only place allowed to interpret them, and it treats instruction-like content as evidence
rather than instruction.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import Field

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

    @property
    def image_ids(self) -> tuple[str, ...]:
        return tuple(image.image_id for image in self.images)
