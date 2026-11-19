"""The held-out evaluation set: a manifest, the checks that keep it independent, its labels.

A held-out set measures field accuracy only while three things stay true, and each is
checked here rather than trusted:

* **Units are independent.** One physical tree or one wood lot is one ``group_id``. A
  photograph belongs to exactly one unit, and the units of one group are never split
  between evaluation and anything else. Several units may share a group (a tree seen in
  two seasons); they are counted as one group when sample sizes are reported.
* **Nothing in it was used for development.** A photograph whose SHA-256 is in a
  development directory named by the manifest (``development_photo_dirs``) is a leak.
* **A label is only as good as its review.** The owner's labels are preliminary. A unit
  is release-ready when at least one specialist review agrees with its genus and none
  disagrees, or, when a review disagreed, the two latest reviews both agree with it. The
  status is derived from the recorded reviews; it is never declared.

The manifest lives with the photographs, outside the repository: photographs are usually
not redistributable and carry location metadata (``docs/dataset-policy.md``). Nothing here
reads a photograph's content beyond its bytes and, when Pillow is installed, whether its
EXIF carries GPS coordinates.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterable
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import Field, ValidationError, model_validator

from dendro_inspector.knowledge.loader import KnowledgeBase
from dendro_inspector.schemas.base import Contract
from dendro_inspector.schemas.taxon import Resolution

#: The planning size the owner set for the first held-out set (2026-11-15).
TARGET_UNITS = (100, 200)

#: Case IDs the control function accepts, so a unit can be submitted under its own ID.
UNIT_ID = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$"
_LATIN = re.compile(r"^[a-z]+$")
_PHOTO_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp"})
_GPS_IFD = 0x8825


class Category(StrEnum):
    """What makes a unit hard. The owner asked for every one of these to be present."""

    UNKNOWN_TAXON = "unknown_taxon"
    """The labelled genus has no taxon card. Derived from the knowledge base as well."""
    MIXED_SUBJECTS = "mixed_subjects"
    CONFUSABLE = "confusable"
    BARK_ONLY = "bark_only"
    WOOD = "wood"


#: Who may review a label. Also the qualifications a human review records (`human_review`).
Qualification = Literal["botanist", "dendrologist", "forester", "arborist", "other"]


class Review(Contract):
    """One specialist's judgement of a unit's label."""

    reviewer: str = Field(min_length=1, max_length=80, description="A role or initials.")
    qualification: Qualification
    verdict: Literal["agree", "disagree"]
    genus: str | None = Field(
        default=None, description="The reviewer's genus when they disagree with the label."
    )
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")

    @model_validator(mode="after")
    def _disagreement_states_a_determination(self) -> Review:
        if self.verdict == "disagree" and (
            "genus" not in self.model_fields_set or self.genus == ""
        ):
            msg = "a disagreeing review names the genus or explicitly sets it to null"
            raise ValueError(msg)
        return self


class Label(Contract):
    genus: str | None = Field(
        pattern=_LATIN.pattern, description="Explicit null means indeterminate."
    )
    family: str | None = Field(default=None, pattern=_LATIN.pattern)
    target_subject_ids: tuple[str, ...] | Literal["all"] = "all"
    label_visible: bool | None = None
    labelled_by: str = Field(default="owner", min_length=1, max_length=80)


class Unit(Contract):
    """One inspection: the photographs submitted together, and what they show."""

    unit_id: str = Field(pattern=UNIT_ID)
    group_id: str = Field(pattern=UNIT_ID, description="The physical tree or wood lot.")
    photos: tuple[str, ...] = Field(min_length=1, max_length=12)
    categories: tuple[Category, ...] = ()
    label: Label
    reviews: tuple[Review, ...] = ()


class HeldOutManifest(Contract):
    schema_version: Literal[1] = 1
    name: str = Field(min_length=1, max_length=80)
    development_photo_dirs: tuple[str, ...] = Field(
        default=(),
        description="Directories of photographs used in development; none may reappear.",
    )
    units: tuple[Unit, ...]


class ReviewStatus(StrEnum):
    PRELIMINARY = "preliminary"
    REVIEWED = "reviewed"
    DISPUTED = "disputed"
    RESOLVED = "resolved"


def review_status(unit: Unit) -> ReviewStatus:
    """Where a unit's label stands, from its reviews alone."""
    reviews = unit.reviews
    if not reviews:
        return ReviewStatus.PRELIMINARY
    if all(review.verdict == "agree" for review in reviews):
        return ReviewStatus.REVIEWED
    latest = reviews[-2:]
    if len(latest) == 2 and all(review.verdict == "agree" for review in latest):
        return ReviewStatus.RESOLVED
    return ReviewStatus.DISPUTED


def release_ready(unit: Unit) -> bool:
    return review_status(unit) in (ReviewStatus.REVIEWED, ReviewStatus.RESOLVED)


class CheckReport(Contract):
    """What the checks found. ``errors`` make the set unusable; the rest is description."""

    name: str
    units: int
    groups: int
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    target_units: tuple[int, int] = TARGET_UNITS
    categories: dict[str, int] = Field(default_factory=dict)
    missing_categories: tuple[str, ...] = ()
    review_status: dict[str, int] = Field(default_factory=dict)
    release_ready_units: int = 0
    release_ready_indeterminate_genus_units: int = 0
    genera: dict[str, int] = Field(default_factory=dict)
    label_visible: dict[str, int] = Field(default_factory=dict)
    photos_with_gps: int | None = None

    @property
    def ok(self) -> bool:
        return not self.errors


def load_manifest(path: Path) -> HeldOutManifest:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        return HeldOutManifest.model_validate(payload)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        msg = f"{path}: {exc}"
        raise ValueError(msg) from exc


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _development_hashes(base: Path, directories: Iterable[str]) -> tuple[set[str], list[str]]:
    hashes: set[str] = set()
    problems: list[str] = []
    for directory in directories:
        root = (base / directory).resolve()
        if not root.is_dir():
            problems.append(f"development directory not found: {directory}")
            continue
        for photo in root.rglob("*"):
            if photo.is_file() and photo.suffix.lower() in _PHOTO_SUFFIXES:
                hashes.add(_sha256(photo))
    return hashes, problems


def photo_has_gps(path: Path) -> bool | None:
    """Whether the photo's EXIF holds GPS data; None when that cannot be read."""
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        with Image.open(path) as image:
            return bool(image.getexif().get_ifd(_GPS_IFD))
    except Exception:  # an unreadable photo is reported elsewhere, not here
        return None


def covered_genera(knowledge: KnowledgeBase) -> frozenset[str]:
    """The genera some taxon card names, at its own level or as its genus."""
    genera: set[str] = set()
    for taxon_id in knowledge.available_taxon_ids():
        card = knowledge.try_taxon(taxon_id)
        if card is None:
            continue
        identity = card.identity_at_or_broader(Resolution.GENUS)
        if identity is not None and identity.resolution is Resolution.GENUS:
            genera.add(identity.taxon_id)
    return frozenset(genera)


def check(manifest: HeldOutManifest, base: Path, knowledge: KnowledgeBase) -> CheckReport:
    """Every check, over photographs resolved relative to ``base`` (the manifest's folder)."""
    errors: list[str] = []
    warnings: list[str] = []

    ids = Counter(unit.unit_id for unit in manifest.units)
    errors += [f"unit_id used more than once: {uid}" for uid, n in ids.items() if n > 1]

    development, problems = _development_hashes(base, manifest.development_photo_dirs)
    errors += problems
    if not manifest.development_photo_dirs:
        warnings.append("no development_photo_dirs named: leakage from development not checked")

    owner_of: dict[str, str] = {}
    gps: list[bool | None] = []
    for unit in manifest.units:
        for name in unit.photos:
            photo = base / name
            if not photo.is_file():
                errors.append(f"{unit.unit_id}: photo not found: {name}")
                continue
            if photo.suffix.lower() not in _PHOTO_SUFFIXES:
                errors.append(f"{unit.unit_id}: not a JPEG, PNG or WebP file: {name}")
                continue
            digest = _sha256(photo)
            if digest in owner_of and owner_of[digest] != unit.unit_id:
                errors.append(f"{unit.unit_id}: same photograph as unit {owner_of[digest]}: {name}")
            owner_of.setdefault(digest, unit.unit_id)
            if digest in development:
                errors.append(f"{unit.unit_id}: photograph used in development: {name}")
            gps.append(photo_has_gps(photo))

    by_group: dict[str, set[str]] = {}
    for unit in manifest.units:
        if Category.MIXED_SUBJECTS not in unit.categories and unit.label.genus is not None:
            by_group.setdefault(unit.group_id, set()).add(unit.label.genus)
    errors += [
        f"group {group} is one tree or lot but is labelled {sorted(genera)}"
        for group, genera in sorted(by_group.items())
        if len(genera) > 1
    ]
    # The protocol keeps genus: null for a tree that no evidence names. Another unit of the
    # same tree naming a genus contradicts that, unless only this unit's photos fall short.
    indeterminate = {
        unit.group_id
        for unit in manifest.units
        if Category.MIXED_SUBJECTS not in unit.categories and unit.label.genus is None
    }
    warnings += [
        f"group {group} is one tree or lot: some units have no genus, others are "
        f"labelled {sorted(by_group[group])}"
        for group in sorted(indeterminate & by_group.keys())
    ]

    covered = covered_genera(knowledge)
    categories: Counter[str] = Counter()
    for unit in manifest.units:
        declared = set(unit.categories)
        if Category.UNKNOWN_TAXON in declared and unit.label.genus in covered:
            errors.append(
                f"{unit.unit_id}: marked unknown_taxon but a card covers {unit.label.genus}"
            )
        if unit.label.genus is None and Category.UNKNOWN_TAXON in declared:
            errors.append(f"{unit.unit_id}: unknown_taxon requires a known genus without a card")
        if unit.label.genus is not None and unit.label.genus not in covered:
            declared.add(Category.UNKNOWN_TAXON)
        categories.update(category.value for category in declared)

    groups = len({unit.group_id for unit in manifest.units})
    low, high = TARGET_UNITS
    if not low <= len(manifest.units) <= high:
        warnings.append(f"{len(manifest.units)} units; the planning target is {low}-{high}")
    missing = tuple(c.value for c in Category if categories[c.value] == 0)
    if missing:
        warnings.append(f"no unit in: {', '.join(missing)}")
    statuses = Counter(review_status(unit).value for unit in manifest.units)
    if statuses[ReviewStatus.DISPUTED.value]:
        warnings.append(
            f"{statuses[ReviewStatus.DISPUTED.value]} disputed units need a second review"
        )
    checked = [value for value in gps if value is not None]
    if any(checked):
        warnings.append(f"{sum(checked)} photographs carry GPS coordinates in EXIF")

    return CheckReport(
        name=manifest.name,
        units=len(manifest.units),
        groups=groups,
        errors=tuple(errors),
        warnings=tuple(warnings),
        categories=dict(sorted(categories.items())),
        missing_categories=missing,
        review_status=dict(sorted(statuses.items())),
        release_ready_units=sum(release_ready(unit) for unit in manifest.units),
        release_ready_indeterminate_genus_units=sum(
            unit.label.genus is None and release_ready(unit) for unit in manifest.units
        ),
        genera=dict(
            sorted(
                Counter(u.label.genus for u in manifest.units if u.label.genus is not None).items()
            )
        ),
        label_visible=dict(
            sorted(
                Counter(str(unit.label.label_visible).lower() for unit in manifest.units).items()
            )
        ),
        photos_with_gps=sum(checked) if len(checked) == len(gps) and gps else None,
    )


def labels(manifest: HeldOutManifest, *, release_only: bool) -> dict[str, Any]:
    """Reference labels in the format ``coverage-audit --labels`` reads, keyed by unit ID."""
    entries: dict[str, Any] = {}
    for unit in manifest.units:
        if release_only and not release_ready(unit):
            continue
        entry: dict[str, Any] = {
            "genus": unit.label.genus,
            "target_subject_ids": (
                "all"
                if unit.label.target_subject_ids == "all"
                else list(unit.label.target_subject_ids)
            ),
            "label_visible": unit.label.label_visible,
        }
        if unit.label.family is not None:
            entry["family"] = unit.label.family
        entries[unit.unit_id] = entry
    return {"labels": entries}


def render_text(report: CheckReport) -> str:
    low, high = report.target_units
    lines = [
        f"held-out set {report.name}: {report.units} units in {report.groups} groups "
        f"(target {low}-{high} units)",
        f"  review: {json.dumps(report.review_status)}; release-ready {report.release_ready_units}",
        f"  categories: {json.dumps(report.categories)}",
        f"  genera: {json.dumps(report.genera)}",
        f"  indeterminate genus (release-ready): {report.release_ready_indeterminate_genus_units}",
        f"  label visible: {json.dumps(report.label_visible)}",
        "  GPS in EXIF: "
        + (
            "not checked (Pillow missing or unreadable photos)"
            if report.photos_with_gps is None
            else str(report.photos_with_gps)
        ),
    ]
    lines += [f"  warning: {warning}" for warning in report.warnings]
    lines += [f"  ERROR: {error}" for error in report.errors]
    lines.append("OK" if report.ok else f"FAILED: {len(report.errors)} errors")
    return "\n".join(lines)
