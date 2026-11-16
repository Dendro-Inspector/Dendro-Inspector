"""Human review: a package for a specialist, and their answers back as a revision of one case.

An answer is a claim about photographs. A specialist looking at the same photographs can
confirm it, correct it, or say the photographs do not decide it. This module carries that
exchange by hand, and three properties keep it safe:

* **The package is the evidence, unchanged.** Photographs are copied byte for byte with
  their SHA-256, and the questions are derived from the stored answer by code, never by a
  model. A photograph whose metadata carries GPS coordinates, or cannot be read, is refused
  unless the operator allows it (``docs/dataset-policy.md``).
* **The answers are untrusted input.** A response is size-limited, schema-checked, bound to
  one package by its content-derived id, and may only answer that package's questions with
  the answers each one lists. Nothing in it is used as a path, a command or an instruction.
* **A correction is about one case.** An import appends a revision to the package's own
  ledger and writes nothing else: no knowledge card, prompt or policy. What a reviewer says
  about one tree changes nothing for the next until a person turns it into reviewed
  knowledge through the usual card review.

Nothing here calls a model or sends anything anywhere. Sharing a package is the operator's
decision, and the photographs in it are as sensitive as the originals.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from datetime import UTC, date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, ValidationError, field_validator, model_validator

from dendro_inspector.evaluation.heldout import Qualification, photo_has_gps
from dendro_inspector.schemas.base import Contract, Identifier
from dendro_inspector.schemas.decisions import SubjectRole
from dendro_inspector.schemas.taxon import Resolution

PACKAGE_SCHEMA_VERSION: Literal[1] = 1
PACKAGE_FILE = "package.json"
TEMPLATE_FILE = "answers.template.json"
README_FILE = "README.txt"
LEDGER_FILE = "revisions.jsonl"
PHOTO_DIR = "photos"

#: A filled-in response is a few kilobytes; anything near this is not one.
MAX_RESPONSE_BYTES = 256 * 1024
MAX_NOTE_CHARS = 1000

_MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}
_IMAGE_ID = r"^img-[1-9][0-9]*$"
_QUESTION_ID = r"^q-[1-9][0-9]*$"
_PACKAGE_ID = r"^rp-[0-9a-f]{16}$"
_SHA256 = r"^[0-9a-f]{64}$"
_DATE = r"^\d{4}-\d{2}-\d{2}$"
#: Control characters other than tab and newline. A note is printed to a terminal, where an
#: escape sequence could rewrite what the operator sees.
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


class HumanReviewError(ValueError):
    """A package that cannot be written, or answers that cannot be recorded."""


class LocationMetadata(StrEnum):
    ABSENT = "absent"
    PRESENT = "present"
    UNKNOWN = "unknown"
    """Pillow is not installed or the metadata could not be read."""


class QuestionKind(StrEnum):
    CONFIRM_IDENTITY = "confirm_identity"
    """The answer names a taxon: is that right at the stated level?"""
    IDENTIFY = "identify"
    """The answer names nothing: what is it?"""
    CHECK_HYPOTHESIS = "check_hypothesis"
    """A name the review could not check and the answer does not confirm: is it right?"""
    CHECK_CONTRADICTION = "check_contradiction"
    """The observation review held against the answer: is it right?"""
    DESIGNATE_TARGET = "designate_target"
    """Several subjects and none designated: which one was photographed to be identified?"""


class Answer(StrEnum):
    AGREE = "agree"
    """What the question states is right."""
    DISAGREE = "disagree"
    """It is wrong, and the reviewer cannot or need not say what is right."""
    NAME = "name"
    """The reviewer gives the taxon, with ``taxon`` and ``resolution``."""
    CANNOT_TELL = "cannot_tell"
    """These photographs do not decide it."""
    SUBJECT = "subject"
    """The reviewer names a subject, with ``subject_id``."""


ALLOWED_ANSWERS: dict[QuestionKind, tuple[Answer, ...]] = {
    QuestionKind.CONFIRM_IDENTITY: (Answer.AGREE, Answer.DISAGREE, Answer.NAME, Answer.CANNOT_TELL),
    QuestionKind.IDENTIFY: (Answer.NAME, Answer.CANNOT_TELL),
    QuestionKind.CHECK_HYPOTHESIS: (Answer.AGREE, Answer.DISAGREE, Answer.NAME, Answer.CANNOT_TELL),
    QuestionKind.CHECK_CONTRADICTION: (Answer.AGREE, Answer.DISAGREE, Answer.CANNOT_TELL),
    QuestionKind.DESIGNATE_TARGET: (Answer.SUBJECT, Answer.CANNOT_TELL),
}


def _plain(value: str) -> str:
    if _CONTROL.search(value):
        msg = "control characters are not allowed"
        raise ValueError(msg)
    return value


# ---------------------------------------------------------------- the package


class PackagePhoto(Contract):
    image_id: str = Field(pattern=_IMAGE_ID)
    file_name: str = Field(pattern=r"^photo-[1-9][0-9]*\.(jpg|jpeg|png|webp)$")
    sha256: str = Field(pattern=_SHA256)
    bytes: int = Field(ge=0)
    media_type: str = Field(max_length=40)
    location_metadata: LocationMetadata


class HypothesisSummary(Contract):
    taxon: Identifier
    display_name: str = Field(max_length=120)
    resolution: Resolution
    explanation: str = Field(default="", max_length=2000)


class SubjectSummary(Contract):
    """One subject's answer as the reviewer sees it."""

    subject_id: Identifier
    role: SubjectRole = SubjectRole.UNSPECIFIED
    verdict: str = Field(default="", max_length=2000)
    selected_taxon: Identifier | None = None
    display_name: str | None = Field(default=None, max_length=120)
    resolution: Resolution = Resolution.UNKNOWN
    status: str = Field(default="", max_length=60)
    confidence_band: str = Field(default="", max_length=16)
    nearest_alternative: Identifier | None = None
    strongest_contradiction: str | None = Field(default=None, max_length=2000)
    supporting_evidence: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    hypothesis: HypothesisSummary | None = None


class Question(Contract):
    question_id: str = Field(pattern=_QUESTION_ID)
    kind: QuestionKind
    subject_id: Identifier | None = None
    text: str = Field(max_length=4000)
    allowed_answers: tuple[Answer, ...]
    asked_taxon: Identifier | None = Field(
        default=None, description="The taxon the question names: the answer's or the hypothesis'."
    )
    choices: tuple[Identifier, ...] = Field(
        default=(), description="The subjects a `subject` answer may name."
    )


class SourceRecord(Contract):
    """Which engine, prompt and answer the package was made from."""

    result_sha256: str = Field(pattern=_SHA256)
    response_schema_version: int = Field(ge=1)
    trace_present: bool
    graph_version: str | None = Field(default=None, max_length=80)
    trace_schema_version: int | None = None
    code_commit_sha: str | None = Field(default=None, max_length=80)
    code_dirty: bool | None = None
    domain_prompt_version: str | None = Field(default=None, max_length=80)
    domain_prompt_sha256: str | None = Field(default=None, max_length=64)
    providers: dict[str, str] = Field(default_factory=dict)


class ReviewPackage(Contract):
    package_schema_version: Literal[1] = PACKAGE_SCHEMA_VERSION
    package_id: str = Field(
        pattern=_PACKAGE_ID, description="Derived from everything else in the package."
    )
    case_id: str = Field(max_length=120)
    exported_on: str = Field(pattern=_DATE)
    location_allowed: bool = Field(
        description="The operator allowed photographs with location metadata, or unreadable."
    )
    source: SourceRecord
    photos: tuple[PackagePhoto, ...] = Field(min_length=1)
    subjects: tuple[SubjectSummary, ...] = Field(min_length=1)
    target_subject_ids: tuple[Identifier, ...] = ()
    answer_text: str = Field(default="", max_length=20000)
    questions: tuple[Question, ...] = Field(min_length=1)


def package_id_of(package: ReviewPackage) -> str:
    """The id a package's content implies. An edited package no longer matches its id."""
    body = package.model_dump(mode="json", exclude={"package_id"})
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "rp-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- the response


class Reviewer(Contract):
    reviewer: str = Field(min_length=1, max_length=80, description="A role or initials.")
    qualification: Qualification
    date: str = Field(pattern=_DATE)

    @field_validator("reviewer")
    @classmethod
    def _no_control(cls, value: str) -> str:
        return _plain(value)

    @field_validator("date")
    @classmethod
    def _real_date(cls, value: str) -> str:
        date.fromisoformat(value)
        return value


class ExpertAnswer(Contract):
    question_id: str = Field(pattern=_QUESTION_ID)
    answer: Answer | None = Field(default=None, description="None leaves the question unanswered.")
    taxon: Identifier | None = None
    resolution: Resolution | None = None
    subject_id: Identifier | None = None
    photos: tuple[str, ...] = Field(default=(), max_length=40)
    note: str = Field(default="", max_length=MAX_NOTE_CHARS)

    @field_validator("note")
    @classmethod
    def _no_control(cls, value: str) -> str:
        return _plain(value)

    @field_validator("photos")
    @classmethod
    def _image_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not re.match(_IMAGE_ID, photo) for photo in value):
            msg = "photos lists image ids such as img-1"
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def _fields_fit_the_answer(self) -> ExpertAnswer:
        named = self.answer is Answer.NAME
        if named and (self.taxon is None or self.resolution in (None, Resolution.UNKNOWN)):
            msg = f"{self.question_id}: a `name` answer gives a taxon and its resolution"
            raise ValueError(msg)
        if not named and (self.taxon is not None or self.resolution is not None):
            msg = f"{self.question_id}: taxon and resolution belong to a `name` answer only"
            raise ValueError(msg)
        if (self.answer is Answer.SUBJECT) != (self.subject_id is not None):
            msg = f"{self.question_id}: subject_id goes with a `subject` answer, and only there"
            raise ValueError(msg)
        return self


class ReviewResponse(Contract):
    package_id: str = Field(pattern=_PACKAGE_ID)
    reviewer: Reviewer
    answers: tuple[ExpertAnswer, ...] = Field(max_length=200)

    @model_validator(mode="after")
    def _one_answer_per_question(self) -> ReviewResponse:
        ids = [answer.question_id for answer in self.answers]
        if len(set(ids)) != len(ids):
            msg = "a question is answered more than once"
            raise ValueError(msg)
        return self


# ---------------------------------------------------------------- the revision


class Outcome(StrEnum):
    CONFIRMED = "confirmed"
    """The reviewer agrees with the name, or gives the same one."""
    REFINED = "refined"
    """The reviewer gives a narrower taxon within the name (``quercus`` → ``quercus_robur``)."""
    CORRECTED = "corrected"
    """The reviewer gives a different taxon."""
    REJECTED = "rejected"
    """The name is wrong and the reviewer gives no other."""
    NAMED = "named"
    """The answer named nothing and the reviewer gives a name."""
    UNDECIDED = "undecided"
    """The reviewer says these photographs do not decide it."""
    UNANSWERED = "unanswered"


class SubjectRevision(Contract):
    subject_id: Identifier
    answer_taxon: Identifier | None
    answer_resolution: Resolution
    outcome: Outcome
    reviewed_taxon: Identifier | None = None
    reviewed_resolution: Resolution | None = None
    hypothesis_outcome: Outcome | None = Field(
        default=None, description="None when the answer held no hypothesis."
    )
    contradiction_upheld: bool | None = Field(
        default=None, description="None when not asked, not answered, or not decided."
    )


class CaseRevision(Contract):
    """One specialist's answers, recorded against one case. Never global knowledge."""

    revision: int = Field(ge=1)
    package_id: str = Field(pattern=_PACKAGE_ID)
    case_id: str = Field(max_length=120)
    response_sha256: str = Field(pattern=_SHA256)
    imported_at: str = Field(max_length=40)
    reviewer: Reviewer
    target_subject_id: Identifier | None = None
    subjects: tuple[SubjectRevision, ...]
    answers: tuple[ExpertAnswer, ...]


# ---------------------------------------------------------------- export


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _location(path: Path) -> LocationMetadata:
    found = photo_has_gps(path)
    if found is None:
        return LocationMetadata.UNKNOWN
    return LocationMetadata.PRESENT if found else LocationMetadata.ABSENT


def _referenced_image_ids(node: Any) -> set[str]:
    """Every ``img-N`` a stored trace cites, wherever it cites one."""
    found: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "image_id" and isinstance(value, str):
                found.add(value)
            elif key.endswith("image_ids") and isinstance(value, list):
                found.update(item for item in value if isinstance(item, str))
            else:
                found |= _referenced_image_ids(value)
    elif isinstance(node, list):
        for item in node:
            found |= _referenced_image_ids(item)
    return {image_id for image_id in found if re.match(_IMAGE_ID, image_id)}


def _enum(kind: type[Resolution] | type[SubjectRole], value: Any, default: Any) -> Any:
    try:
        return kind(value)
    except ValueError:
        return default


def _texts(values: Any, limit: int = 2000) -> tuple[str, ...]:
    return tuple(str(value)[:limit] for value in values or () if value)


def _subjects(response: dict[str, Any]) -> tuple[SubjectSummary, ...]:
    results = {
        result.get("subject"): result
        for result in response.get("results") or ()
        if isinstance(result, dict)
    }
    subjects = []
    for decision in response.get("decisions") or ():
        if not isinstance(decision, dict) or not decision.get("subject_id"):
            continue
        result = results.get(decision["subject_id"], {})
        hypothesis = result.get("visual_hypothesis")
        selected = decision.get("selected_taxon")
        subjects.append(
            SubjectSummary(
                subject_id=decision["subject_id"],
                role=_enum(SubjectRole, result.get("subject_role"), SubjectRole.UNSPECIFIED),
                verdict=str(result.get("verdict") or "")[:2000],
                selected_taxon=selected,
                display_name=decision.get("selected_taxon_display_name") or selected,
                resolution=_enum(Resolution, decision.get("resolution"), Resolution.UNKNOWN),
                status=str(decision.get("status") or "")[:60],
                confidence_band=str(
                    decision.get("confidence_band") or result.get("confidence_band") or ""
                )[:16],
                nearest_alternative=decision.get("nearest_alternative"),
                strongest_contradiction=(
                    str(decision["strongest_contradiction"])[:2000]
                    if decision.get("strongest_contradiction")
                    else None
                ),
                supporting_evidence=_texts(decision.get("supporting_evidence")),
                limitations=_texts(result.get("limitations")),
                hypothesis=(
                    HypothesisSummary(
                        taxon=hypothesis["taxon"],
                        display_name=str(hypothesis.get("display_name") or hypothesis["taxon"]),
                        resolution=_enum(
                            Resolution, hypothesis.get("resolution"), Resolution.UNKNOWN
                        ),
                        explanation=str(hypothesis.get("explanation") or "")[:2000],
                    )
                    if isinstance(hypothesis, dict) and hypothesis.get("taxon")
                    else None
                ),
            )
        )
    return tuple(subjects)


def _label(subject: SubjectSummary, several: bool) -> str:
    label = f"Subject {subject.subject_id}"
    if several and subject.role is SubjectRole.TARGET:
        label += " (the one asked about)"
    elif several and subject.role is SubjectRole.CONTEXT:
        label += " (also in the photographs, not asked about)"
    return label


def questions_for(
    subjects: tuple[SubjectSummary, ...], target_subject_ids: tuple[str, ...]
) -> tuple[Question, ...]:
    """The precise questions a stored answer raises. Derived by code from the answer alone."""
    several = len(subjects) > 1
    drafts: list[dict[str, Any]] = []
    if several and not target_subject_ids:
        ids = tuple(subject.subject_id for subject in subjects)
        drafts.append(
            {
                "kind": QuestionKind.DESIGNATE_TARGET,
                "text": (
                    f"These photographs show {len(ids)} subjects ({', '.join(ids)}). Which one "
                    "were they taken to identify?"
                ),
                "choices": ids,
            }
        )
    for subject in subjects:
        label = _label(subject, several)
        status = subject.status.replace("_", " ") or "no status"
        if subject.selected_taxon is not None:
            alternative = (
                f" Its nearest alternative was {subject.nearest_alternative}."
                if subject.nearest_alternative
                else ""
            )
            band = f", confidence {subject.confidence_band}" if subject.confidence_band else ""
            drafts.append(
                {
                    "kind": QuestionKind.CONFIRM_IDENTITY,
                    "subject_id": subject.subject_id,
                    "asked_taxon": subject.selected_taxon,
                    "text": (
                        f"{label}: the answer names {subject.display_name} at "
                        f"{subject.resolution.value} level ({status}{band}).{alternative} "
                        "Is that right?"
                    ),
                }
            )
        else:
            drafts.append(
                {
                    "kind": QuestionKind.IDENTIFY,
                    "subject_id": subject.subject_id,
                    "text": (
                        f"{label}: the answer names nothing ({status}). What is it, at the "
                        "finest level these photographs decide?"
                    ),
                }
            )
        if subject.hypothesis is not None:
            hypothesis = subject.hypothesis
            drafts.append(
                {
                    "kind": QuestionKind.CHECK_HYPOTHESIS,
                    "subject_id": subject.subject_id,
                    "asked_taxon": hypothesis.taxon,
                    "text": (
                        f"{label}: the model read it as {hypothesis.display_name} "
                        f"({hypothesis.resolution.value}), but review could not check that "
                        f"reading, so the answer does not confirm it. Is it "
                        f"{hypothesis.display_name}?"
                    ),
                }
            )
        if subject.strongest_contradiction:
            drafts.append(
                {
                    "kind": QuestionKind.CHECK_CONTRADICTION,
                    "subject_id": subject.subject_id,
                    "text": (
                        f"{label}: review held this against the answer: "
                        f'"{subject.strongest_contradiction}". Is that observation right for '
                        "these photographs?"
                    ),
                }
            )
    return tuple(
        Question(
            question_id=f"q-{number}",
            allowed_answers=ALLOWED_ANSWERS[draft["kind"]],
            **draft,
        )
        for number, draft in enumerate(drafts, start=1)
    )


def _load_result(path: Path) -> tuple[bytes, dict[str, Any], dict[str, Any] | None, int | None]:
    """The stored response, its trace when kept, and how many photos the record names."""
    raw = path.read_bytes()
    try:
        data = json.loads(raw)
    except ValueError as exc:
        msg = f"{path}: not JSON: {exc}"
        raise HumanReviewError(msg) from exc
    if not isinstance(data, dict):
        msg = f"{path}: expected a JSON object"
        raise HumanReviewError(msg)
    # A batch case record wraps the `dendro inspect --json` output in `result`; the case
    # API's projection puts `response` at the top.
    record = data["result"] if isinstance(data.get("result"), dict) else data
    response = record.get("response") or data.get("response")
    if not isinstance(response, dict):
        msg = f"{path}: holds no answer (no `response`); a failed case cannot be reviewed"
        raise HumanReviewError(msg)
    trace = record.get("trace") if isinstance(record.get("trace"), dict) else None
    upload = data.get("upload")
    photos = upload.get("photos") if isinstance(upload, dict) else None
    return raw, response, trace, len(photos) if isinstance(photos, list) else None


def build_package(
    result_path: Path,
    images: list[Path],
    *,
    allow_location: bool = False,
    today: date | None = None,
) -> tuple[ReviewPackage, list[bytes]]:
    """The package for a stored answer and its photographs, given in ``--image`` order."""
    raw, response, trace, recorded_photos = _load_result(result_path)
    if not images:
        msg = "give the photographs the answer was made from, in their original order"
        raise HumanReviewError(msg)
    if recorded_photos is not None and recorded_photos != len(images):
        msg = f"the case record names {recorded_photos} photographs; {len(images)} were given"
        raise HumanReviewError(msg)
    cited = _referenced_image_ids(trace) - {f"img-{n}" for n in range(1, len(images) + 1)}
    if cited:
        msg = (
            f"the answer cites {', '.join(sorted(cited))}, but only {len(images)} photographs "
            "were given"
        )
        raise HumanReviewError(msg)

    photos: list[PackagePhoto] = []
    payloads: list[bytes] = []
    refused: list[str] = []
    for number, path in enumerate(images, start=1):
        suffix = path.suffix.lower()
        if not path.is_file():
            msg = f"photograph not found: {path}"
            raise HumanReviewError(msg)
        if suffix not in _MEDIA_TYPES:
            msg = f"not a JPEG, PNG or WebP file: {path}"
            raise HumanReviewError(msg)
        payload = path.read_bytes()
        location = _location(path)
        if location is not LocationMetadata.ABSENT and not allow_location:
            refused.append(f"{path.name} ({location.value})")
        photos.append(
            PackagePhoto(
                image_id=f"img-{number}",
                file_name=f"photo-{number}{suffix}",
                sha256=_sha256(payload),
                bytes=len(payload),
                media_type=_MEDIA_TYPES[suffix],
                location_metadata=location,
            )
        )
        payloads.append(payload)
    if refused:
        msg = (
            "location metadata present or unreadable in: "
            + ", ".join(refused)
            + ". Strip it, or pass --allow-location to include these photographs as they are"
        )
        raise HumanReviewError(msg)

    subjects = _subjects(response)
    if not subjects:
        msg = f"{result_path}: the answer has no subject to review"
        raise HumanReviewError(msg)
    targets = tuple(
        subject_id
        for subject_id in response.get("target_subject_ids") or ()
        if isinstance(subject_id, str)
    )
    prompt = (trace or {}).get("domain_prompt") or {}
    source = SourceRecord(
        result_sha256=_sha256(raw),
        response_schema_version=int(response.get("schema_version") or 1),
        trace_present=trace is not None,
        graph_version=(trace or {}).get("graph_version"),
        trace_schema_version=(trace or {}).get("trace_schema_version"),
        code_commit_sha=(trace or {}).get("code_commit_sha"),
        code_dirty=(trace or {}).get("code_dirty"),
        domain_prompt_version=prompt.get("version"),
        domain_prompt_sha256=prompt.get("sha256"),
        providers={
            str(role): str(name) for role, name in ((trace or {}).get("providers") or {}).items()
        },
    )
    draft = ReviewPackage(
        package_id="rp-" + "0" * 16,
        case_id=str(response.get("case_id") or (trace or {}).get("case_id") or "case")[:120],
        exported_on=(today or datetime.now(UTC).date()).isoformat(),
        location_allowed=allow_location,
        source=source,
        photos=tuple(photos),
        subjects=subjects,
        target_subject_ids=targets,
        answer_text=str(response.get("human_readable") or "")[:20000],
        questions=questions_for(subjects, targets),
    )
    return draft.model_copy(update={"package_id": package_id_of(draft)}), payloads


def answer_template(package: ReviewPackage) -> dict[str, Any]:
    """The response a reviewer fills in. Unchanged, it does not import: nothing is answered."""
    return {
        "package_id": package.package_id,
        "reviewer": {"reviewer": "", "qualification": "other", "date": ""},
        "answers": [
            {
                "question_id": question.question_id,
                "answer": None,
                "taxon": None,
                "resolution": None,
                "subject_id": None,
                "photos": [],
                "note": "",
            }
            for question in package.questions
        ],
    }


_ANSWER_HELP = {
    Answer.AGREE: "what the question states is right",
    Answer.DISAGREE: "it is wrong, and you cannot or need not say what is right",
    Answer.NAME: (
        'give "taxon" (lowercase Latin: genus or genus_species, e.g. "quercus" or '
        '"quercus_robur") and "resolution" (family, genus, species_group or species)'
    ),
    Answer.CANNOT_TELL: "these photographs do not decide it",
    Answer.SUBJECT: 'give "subject_id", one of the subjects the question lists',
}


def render_readme(package: ReviewPackage) -> str:
    """The reviewer's instructions, the photographs, the answer under review, the questions."""
    lines = [
        f"Dendro Inspector review package {package.package_id}",
        f"Case {package.case_id}, exported {package.exported_on}.",
        "",
        "These photographs are shared with you for this review only. Please do not pass them",
        "on. Answer from the photographs alone.",
    ]
    if any(photo.location_metadata is not LocationMetadata.ABSENT for photo in package.photos):
        lines.append("Some photographs may carry GPS coordinates in their metadata.")
    lines += [
        "",
        f'To answer: copy {TEMPLATE_FILE} to answers.json. Fill in "reviewer" (your initials',
        'or role), "qualification" (botanist, dendrologist, forester, arborist or other) and',
        '"date" (YYYY-MM-DD). For each question set "answer" to one of the values it lists, or',
        'leave it null to skip the question. "photos" may list the photographs that decided',
        f'your answer (img-1, img-2, ...); "note" takes up to {MAX_NOTE_CHARS} characters.',
        "",
    ]
    lines += [f"  {answer.value:<12} {text}" for answer, text in _ANSWER_HELP.items()]
    lines += ["", "Photographs:"]
    lines += [f"  {photo.image_id:<6} {PHOTO_DIR}/{photo.file_name}" for photo in package.photos]
    lines += ["", "The answer under review:", ""]
    lines += [f"  {line}" for line in (package.answer_text or "(no text)").splitlines()]
    lines += ["", "Questions:"]
    for question in package.questions:
        lines += [
            "",
            f"{question.question_id}  {question.text}",
            "      answers: " + ", ".join(answer.value for answer in question.allowed_answers),
        ]
    return "\n".join(lines) + "\n"


def _json(payload: Any) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def write_package(package: ReviewPackage, payloads: list[bytes], out: Path) -> None:
    """Write the package into a new folder. An existing folder with anything in it is refused."""
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        msg = f"{out} already exists and is not an empty folder"
        raise HumanReviewError(msg)
    (out / PHOTO_DIR).mkdir(parents=True, exist_ok=True)
    for photo, payload in zip(package.photos, payloads, strict=True):
        (out / PHOTO_DIR / photo.file_name).write_bytes(payload)
    (out / PACKAGE_FILE).write_text(_json(package.model_dump(mode="json")), encoding="utf-8")
    (out / TEMPLATE_FILE).write_text(_json(answer_template(package)), encoding="utf-8")
    (out / README_FILE).write_text(render_readme(package), encoding="utf-8")


# ---------------------------------------------------------------- import


def load_package(folder: Path) -> ReviewPackage:
    """The package in ``folder``, refused if its content or photographs changed since export."""
    try:
        package = ReviewPackage.model_validate_json((folder / PACKAGE_FILE).read_bytes())
    except FileNotFoundError as exc:
        msg = f"{folder} holds no {PACKAGE_FILE}"
        raise HumanReviewError(msg) from exc
    except ValidationError as exc:
        msg = f"{folder / PACKAGE_FILE} is not a review package: {exc.error_count()} errors"
        raise HumanReviewError(msg) from exc
    if package_id_of(package) != package.package_id:
        msg = f"{folder / PACKAGE_FILE} changed after export; its content no longer matches its id"
        raise HumanReviewError(msg)
    for photo in package.photos:
        path = folder / PHOTO_DIR / photo.file_name
        if not path.is_file() or _sha256(path.read_bytes()) != photo.sha256:
            msg = f"{path} is missing or changed since export"
            raise HumanReviewError(msg)
    return package


def read_response(path: Path) -> tuple[ReviewResponse, str]:
    """A reviewer's answers and their SHA-256. Untrusted: size-limited and schema-checked."""
    if not path.is_file():
        msg = f"answers not found: {path}"
        raise HumanReviewError(msg)
    if path.stat().st_size > MAX_RESPONSE_BYTES:
        msg = f"{path} is over {MAX_RESPONSE_BYTES // 1024} KiB; a response is far smaller"
        raise HumanReviewError(msg)
    raw = path.read_bytes()
    try:
        response = ReviewResponse.model_validate_json(raw)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or 'response'}: {error['msg']}"
            for error in exc.errors()[:8]
        )
        msg = f"{path} is not a usable response: {problems}"
        raise HumanReviewError(msg) from exc
    return response, _sha256(raw)


def read_ledger(folder: Path) -> tuple[CaseRevision, ...]:
    path = folder / LEDGER_FILE
    if not path.exists():
        return ()
    revisions = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            revisions.append(CaseRevision.model_validate_json(line))
        except ValidationError as exc:
            msg = f"{path}: line {number} is not a revision"
            raise HumanReviewError(msg) from exc
    if [revision.revision for revision in revisions] != list(range(1, len(revisions) + 1)):
        msg = f"{path}: revisions are not numbered 1, 2, 3, ..."
        raise HumanReviewError(msg)
    return tuple(revisions)


def _check_against(response: ReviewResponse, package: ReviewPackage) -> None:
    if response.package_id != package.package_id:
        msg = f"these answers belong to package {response.package_id}, not {package.package_id}"
        raise HumanReviewError(msg)
    questions = {question.question_id: question for question in package.questions}
    image_ids = {photo.image_id for photo in package.photos}
    problems = []
    for answer in response.answers:
        question = questions.get(answer.question_id)
        if question is None:
            problems.append(f"{answer.question_id}: no such question in this package")
            continue
        if answer.answer is not None and answer.answer not in question.allowed_answers:
            allowed = ", ".join(value.value for value in question.allowed_answers)
            problems.append(
                f"{answer.question_id}: `{answer.answer.value}` is not one of {allowed}"
            )
        if answer.subject_id is not None and answer.subject_id not in question.choices:
            problems.append(f"{answer.question_id}: {answer.subject_id} is not a listed subject")
        unknown = sorted(set(answer.photos) - image_ids)
        if unknown:
            problems.append(f"{answer.question_id}: no photograph {', '.join(unknown)}")
    if problems:
        raise HumanReviewError("; ".join(problems))
    if not any(answer.answer is not None for answer in response.answers):
        msg = "nothing is answered: every question was left null"
        raise HumanReviewError(msg)


def _outcome(
    asked: str | None, answer: ExpertAnswer | None
) -> tuple[Outcome, str | None, Resolution | None]:
    """What one identity answer says about the name it was asked about."""
    if answer is None or answer.answer is None:
        return Outcome.UNANSWERED, None, None
    if answer.answer is Answer.CANNOT_TELL:
        return Outcome.UNDECIDED, None, None
    if answer.answer is Answer.DISAGREE:
        return Outcome.REJECTED, None, None
    if answer.answer is Answer.AGREE:
        return Outcome.CONFIRMED, asked, None
    taxon = answer.taxon
    if asked is None:
        return Outcome.NAMED, taxon, answer.resolution
    if taxon == asked:
        return Outcome.CONFIRMED, taxon, answer.resolution
    # Taxon ids are genus or genus_species: a reviewer's species of the named genus narrows
    # the name rather than replacing it.
    if taxon is not None and taxon.startswith(f"{asked}_"):
        return Outcome.REFINED, taxon, answer.resolution
    return Outcome.CORRECTED, taxon, answer.resolution


def revise(
    package: ReviewPackage,
    response: ReviewResponse,
    response_sha256: str,
    history: Iterable[CaseRevision],
    *,
    now: datetime | None = None,
) -> CaseRevision:
    """The revision these answers make to this one case. Writes nothing."""
    history = tuple(history)
    duplicate = next((r for r in history if r.response_sha256 == response_sha256), None)
    if duplicate is not None:
        msg = f"these answers were already recorded as revision {duplicate.revision}"
        raise HumanReviewError(msg)
    _check_against(response, package)
    answers = {answer.question_id: answer for answer in response.answers}

    def asked(kind: QuestionKind, subject_id: str | None) -> tuple[Question, ExpertAnswer | None]:
        question = next(
            q for q in package.questions if q.kind is kind and q.subject_id == subject_id
        )
        return question, answers.get(question.question_id)

    kinds = {(q.kind, q.subject_id) for q in package.questions}
    subjects = []
    for subject in package.subjects:
        identity_kind = (
            QuestionKind.CONFIRM_IDENTITY
            if subject.selected_taxon is not None
            else QuestionKind.IDENTIFY
        )
        _, identity = asked(identity_kind, subject.subject_id)
        outcome, taxon, resolution = _outcome(subject.selected_taxon, identity)
        if outcome is Outcome.CONFIRMED and resolution is None:
            resolution = subject.resolution
        hypothesis_outcome = None
        if (QuestionKind.CHECK_HYPOTHESIS, subject.subject_id) in kinds:
            question, answer = asked(QuestionKind.CHECK_HYPOTHESIS, subject.subject_id)
            hypothesis_outcome = _outcome(question.asked_taxon, answer)[0]
        upheld = None
        if (QuestionKind.CHECK_CONTRADICTION, subject.subject_id) in kinds:
            _, answer = asked(QuestionKind.CHECK_CONTRADICTION, subject.subject_id)
            if answer is not None and answer.answer in (Answer.AGREE, Answer.DISAGREE):
                upheld = answer.answer is Answer.AGREE
        subjects.append(
            SubjectRevision(
                subject_id=subject.subject_id,
                answer_taxon=subject.selected_taxon,
                answer_resolution=subject.resolution,
                outcome=outcome,
                reviewed_taxon=taxon,
                reviewed_resolution=resolution if taxon is not None else None,
                hypothesis_outcome=hypothesis_outcome,
                contradiction_upheld=upheld,
            )
        )
    target = None
    if (QuestionKind.DESIGNATE_TARGET, None) in kinds:
        _, answer = asked(QuestionKind.DESIGNATE_TARGET, None)
        target = answer.subject_id if answer is not None else None
    return CaseRevision(
        revision=len(history) + 1,
        package_id=package.package_id,
        case_id=package.case_id,
        response_sha256=response_sha256,
        imported_at=(now or datetime.now(UTC)).isoformat(timespec="seconds"),
        reviewer=response.reviewer,
        target_subject_id=target,
        subjects=tuple(subjects),
        answers=response.answers,
    )


def append_revision(folder: Path, revision: CaseRevision) -> None:
    """Append to the package's ledger. Earlier revisions are never rewritten."""
    with (folder / LEDGER_FILE).open("a", encoding="utf-8", newline="\n") as ledger:
        ledger.write(revision.model_dump_json() + "\n")


def import_answers(
    folder: Path, answers_path: Path
) -> tuple[CaseRevision, tuple[CaseRevision, ...]]:
    """Check a reviewer's answers and record them as the case's next revision."""
    package = load_package(folder)
    history = read_ledger(folder)
    response, digest = read_response(answers_path)
    revision = revise(package, response, digest, history)
    append_revision(folder, revision)
    return revision, (*history, revision)


def render_revision(revision: CaseRevision, history: tuple[CaseRevision, ...]) -> str:
    """What the revision records, and where the case's reviews agree or not."""
    reviewer = revision.reviewer
    lines = [
        f"revision {revision.revision} of case {revision.case_id}: {reviewer.reviewer} "
        f"({reviewer.qualification}, {reviewer.date})",
    ]
    if revision.target_subject_id:
        lines.append(f"  target designated by the reviewer: {revision.target_subject_id}")
    for subject in revision.subjects:
        named = subject.answer_taxon or "nothing"
        line = f"  {subject.subject_id}: answer named {named}; {subject.outcome.value}"
        if subject.reviewed_taxon and subject.outcome is not Outcome.CONFIRMED:
            line += f" as {subject.reviewed_taxon} ({subject.reviewed_resolution})"
        if subject.hypothesis_outcome is not None:
            line += f"; hypothesis {subject.hypothesis_outcome.value}"
        if subject.contradiction_upheld is not None:
            upheld = "upheld" if subject.contradiction_upheld else "rejected"
            line += f"; contradiction {upheld}"
        lines.append(line)
    if len(history) > 1:
        for subject in revision.subjects:
            taxa = [
                next(
                    (
                        s.reviewed_taxon
                        for s in earlier.subjects
                        if s.subject_id == subject.subject_id
                    ),
                    None,
                )
                for earlier in history
            ]
            given = [taxon for taxon in taxa if taxon]
            verdict = (
                "agree" if given and len(set(given)) == 1 and len(given) == len(taxa) else "differ"
            )
            shown = ", ".join(taxon or "-" for taxon in taxa)
            lines.append(f"  {subject.subject_id} across {len(taxa)} reviews: {shown} ({verdict})")
    lines.append("Recorded for this case only; no knowledge card, prompt or policy was changed.")
    return "\n".join(lines)
