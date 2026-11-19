"""Grading answers against reference labels.

A grade says whether an answer named the labelled genus. Comparing two strings is not
enough, and each rule here closes a way a comparison can credit or blame the wrong thing:

* **Several subjects.** A record may hold the labelled tree and another tree behind it. A
  name given to the second is not an answer about the first. Only the *target* subjects are
  graded; names on the others are reported as context. When a record has several subjects
  and the label does not say which is the target, the record is graded only if every subject
  grades the same. Otherwise it is ``unscoped`` and lists the grades it could have.
* **Several names.** "Fagus / Carpinus" offers two genera. It is not a correct answer for a
  beech; it is an ambiguous one that contains the label, and it is counted as that.
* **Broader names.** A family is not the genus. It is ``broader_only`` when it is the
  label's family and ``wrong_broader`` when it is another one.
* **Names are compared whole.** "Betulaceae" begins with "Betula" and is not a birch.

Label visibility travels with a reference so summaries can exclude photographs with a
written plant name. A separate helper checks cited image IDs against supplied IDs; an
archive adapter must supply those IDs before it can make that check.

Nothing here reads a model's prose for meaning. Names are parsed as Latin names; a common
name is a different string and grades as a different taxon.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

#: A label's `target_subject_ids` value saying every subject in the record is the labelled
#: plant: one tree photographed in parts, or one lot of firewood.
ALL_SUBJECTS: Literal["all"] = "all"


class Outcome(StrEnum):
    CORRECT = "correct"
    UNSUPPORTED_GENUS = "unsupported_genus"
    UNSUPPORTED_ALTERNATIVES = "unsupported_alternatives"
    UNVERIFIED_BROADER = "unverified_broader"
    BROADER_ONLY = "broader_only"
    AMBIGUOUS_WITH_LABEL = "ambiguous_with_label"
    AMBIGUOUS_WITHOUT_LABEL = "ambiguous_without_label"
    WRONG = "wrong"
    WRONG_BROADER = "wrong_broader"
    MISSING_TARGET = "missing_target"
    NO_NAME = "no_name"
    UNSCOPED = "unscoped"


class Scope(StrEnum):
    """Which subjects a grade rests on."""

    SINGLE_SUBJECT = "single_subject"
    DECLARED_TARGET = "declared_target"
    ALL_SUBJECTS = "all_subjects"
    UNDECLARED = "undeclared"


#: When several target subjects are graded together, the grade furthest left wins: one wrong
#: name on the labelled plant is a wrong answer however many subjects were named correctly.
_PRECEDENCE = (
    Outcome.WRONG,
    Outcome.UNSUPPORTED_GENUS,
    Outcome.UNSUPPORTED_ALTERNATIVES,
    Outcome.WRONG_BROADER,
    Outcome.AMBIGUOUS_WITHOUT_LABEL,
    Outcome.AMBIGUOUS_WITH_LABEL,
    Outcome.CORRECT,
    Outcome.UNVERIFIED_BROADER,
    Outcome.BROADER_ONLY,
    Outcome.NO_NAME,
)

#: Single genus names whose correctness can be checked against a known reference genus.
SINGLE_GENUS_OUTCOMES = (Outcome.CORRECT, Outcome.WRONG)


@dataclass(frozen=True, slots=True)
class ReferenceLabel:
    """What one unit is, and what a grader needs to know about its photographs."""

    genus: str | None
    family: str | None = None
    target_subject_ids: tuple[str, ...] | Literal["all"] = ()
    label_visible: bool | None = None


@dataclass(frozen=True, slots=True)
class SubjectAnswer:
    """What an answer committed to for one subject."""

    subject_id: str
    genera: tuple[str, ...] = ()
    broader: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Grade:
    outcome: Outcome
    scope: Scope
    graded_subject_ids: tuple[str, ...] = ()
    #: For `unscoped`: the grades the record would get, depending on which subject is the
    #: labelled one. Ordered as `_PRECEDENCE`.
    possible_outcomes: tuple[Outcome, ...] = ()
    #: Genus names given to subjects that were not graded, as `(subject_id, genus)`.
    context_names: tuple[tuple[str, str], ...] = ()
    missing_target_ids: tuple[str, ...] = ()


_PARENTHESES = re.compile(r"\([^)]*\)")
_SEPARATORS = re.compile(r"\band/or\b|/|,|;|\||\band\b|\bor\b|\bvs\b\.?", re.IGNORECASE)
_WORD = re.compile(r"[^\W\d_]+\.?")
_QUALIFIERS = frozenset({"cf.", "cf", "aff.", "aff", "probably", "possibly", "likely", "maybe"})
_NOT_A_NAME = frozenset(
    {"unknown", "none", "null", "na", "no", "not", "unidentified", "indeterminate", "uncertain"}
)
_FAMILY_ENDING = "aceae"
#: Endings of names above genus: family, subfamily, order.
_BROADER_ENDINGS = (_FAMILY_ENDING, "oideae", "ales")


def parse_names(text: str | None) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The genus names and the broader names a free-text answer offers, lowercased.

    Parenthesised glosses are dropped, so "Fagus / Carpinus (beech or hornbeam)" is two
    names. Two species of one genus are one genus: "Quercus robur or Q. petraea" offers
    Quercus once.
    """
    if not text:
        return (), ()
    genera: list[str] = []
    broader: list[str] = []
    for part in _SEPARATORS.split(_PARENTHESES.sub(" ", text)):
        words = [word.lower() for word in _WORD.findall(part) if word.lower() not in _QUALIFIERS]
        if not words:
            continue
        abbreviated = words[0].endswith(".")
        first = words[0].rstrip(".")
        if first in _NOT_A_NAME:
            continue
        # "Q. petraea" after "Quercus robur" is the genus already named. A lone letter with
        # no stop ("n/a") is not a name at all.
        if len(first) == 1 and (not abbreviated or (genera and genera[-1].startswith(first))):
            continue
        if first.endswith(_BROADER_ENDINGS):
            broader.append(first)
        else:
            genera.append(first)
    return tuple(dict.fromkeys(genera)), tuple(dict.fromkeys(broader))


def grade_subject(answer: SubjectAnswer, label: ReferenceLabel) -> Outcome:
    """The grade one subject's answer earns, were that subject the labelled plant."""
    genera = {name.lower() for name in answer.genera}
    if len(genera) > 1 or (genera and answer.broader):
        if label.genus is None:
            return Outcome.UNSUPPORTED_ALTERNATIVES
        return (
            Outcome.AMBIGUOUS_WITH_LABEL
            if label.genus in genera
            else Outcome.AMBIGUOUS_WITHOUT_LABEL
        )
    if genera:
        if label.genus is None:
            return Outcome.UNSUPPORTED_GENUS
        return Outcome.CORRECT if label.genus in genera else Outcome.WRONG
    if answer.broader:
        # Only a family can be checked against the label. An order or a class is broader
        # than the label's family and cannot be told wrong without a taxonomy.
        families = [name for name in answer.broader if name.endswith(_FAMILY_ENDING)]
        if label.family is not None and any(name != label.family for name in families):
            return Outcome.WRONG_BROADER
        if label.genus is None and (label.family is None or set(answer.broader) != {label.family}):
            return Outcome.UNVERIFIED_BROADER
        return Outcome.BROADER_ONLY
    return Outcome.NO_NAME


def _combined(outcomes: Collection[Outcome]) -> Outcome:
    return next(outcome for outcome in _PRECEDENCE if outcome in outcomes)


def grade_record(subjects: Sequence[SubjectAnswer], label: ReferenceLabel) -> Grade:
    """Grade one record: its target subjects only, or ``unscoped`` when they are not known."""
    if label.target_subject_ids and label.target_subject_ids != ALL_SUBJECTS:
        present = {subject.subject_id for subject in subjects}
        missing = tuple(target for target in label.target_subject_ids if target not in present)
        if missing:
            return Grade(
                Outcome.MISSING_TARGET,
                Scope.DECLARED_TARGET,
                context_names=tuple(
                    (subject.subject_id, genus)
                    for subject in subjects
                    if subject.subject_id not in label.target_subject_ids
                    for genus in subject.genera
                ),
                missing_target_ids=missing,
            )
        graded = tuple(s for s in subjects if s.subject_id in label.target_subject_ids)
        scope = Scope.DECLARED_TARGET
    elif not subjects:
        if label.genus is None:
            scope = (
                Scope.ALL_SUBJECTS if label.target_subject_ids == ALL_SUBJECTS else Scope.UNDECLARED
            )
            return Grade(Outcome.MISSING_TARGET, scope)
        return Grade(Outcome.NO_NAME, Scope.SINGLE_SUBJECT)
    elif label.target_subject_ids == ALL_SUBJECTS:
        graded, scope = tuple(subjects), Scope.ALL_SUBJECTS
    elif len(subjects) == 1:
        graded, scope = tuple(subjects), Scope.SINGLE_SUBJECT
    else:
        each = {grade_subject(subject, label) for subject in subjects}
        if len(each) == 1:
            # Whichever subject is the labelled one, the grade is the same.
            return Grade(
                next(iter(each)),
                Scope.UNDECLARED,
                graded_subject_ids=tuple(s.subject_id for s in subjects),
            )
        return Grade(
            Outcome.UNSCOPED,
            Scope.UNDECLARED,
            possible_outcomes=tuple(o for o in _PRECEDENCE if o in each),
        )
    graded_ids = {subject.subject_id for subject in graded}
    return Grade(
        _combined({grade_subject(subject, label) for subject in graded}),
        scope,
        graded_subject_ids=tuple(subject.subject_id for subject in graded),
        context_names=tuple(
            (subject.subject_id, genus)
            for subject in subjects
            if subject.subject_id not in graded_ids
            for genus in subject.genera
        ),
    )


def invalid_image_references(cited: Iterable[str], supplied: Collection[str]) -> tuple[str, ...]:
    """Cited photograph ids that are not among the photographs supplied, each once."""
    return tuple(dict.fromkeys(image_id for image_id in cited if image_id not in supplied))


def wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float] | None:
    """The Wilson score interval for a proportion, or ``None`` when nothing was counted.

    Used instead of the normal approximation because the counts here are small and often
    zero: zero wrong names in forty still leaves an upper bound near nine percent.
    """
    if total <= 0:
        return None
    share = successes / total
    spread = z * math.sqrt(share * (1 - share) / total + z * z / (4 * total * total))
    centre = share + z * z / (2 * total)
    scale = 1 + z * z / total
    return (max(0.0, (centre - spread) / scale), min(1.0, (centre + spread) / scale))


def summarise_grades(graded: Iterable[tuple[str, Grade, ReferenceLabel]]) -> dict[str, Any]:
    """Record-level coverage and conditional accuracy, with distinct-case denominators.

    Wilson intervals are withheld for repeated case IDs: repeated runs of a photograph
    are not independent samples. Unknown label visibility is not treated as label-free.
    """
    pairs = list(graded)

    def block(items: Sequence[tuple[str, Grade, ReferenceLabel]]) -> dict[str, Any]:
        outcomes = Counter(grade.outcome.value for _, grade, _ in items)
        gradable_names = sum(outcomes[outcome.value] for outcome in SINGLE_GENUS_OUTCOMES)
        named = gradable_names + outcomes[Outcome.UNSUPPORTED_GENUS.value]
        wrong = outcomes[Outcome.WRONG.value]
        distinct = len({case for case, _, _ in items})
        independent = distinct == len(items)
        interval = wilson_interval(wrong, gradable_names) if independent else None
        interval_reason = None
        if not independent:
            interval_reason = "repeated_case_ids"
        elif not gradable_names:
            interval_reason = "no_names_with_known_reference_genus" if named else "no_genus_names"
        return {
            "records": len(items),
            "distinct_cases": distinct,
            "outcomes": dict(outcomes),
            "scopes": dict(Counter(grade.scope.value for _, grade, _ in items)),
            "single_genus_names": named,
            "names_with_known_reference_genus": gradable_names,
            "coverage": round(named / len(items), 3) if items else None,
            "wrong_genus_rate": round(wrong / gradable_names, 3) if gradable_names else None,
            "conditional_accuracy": round((gradable_names - wrong) / gradable_names, 3)
            if gradable_names
            else None,
            "wrong_genus_rate_interval_95": (
                [round(interval[0], 3), round(interval[1], 3)] if interval else None
            ),
            "interval_unavailable_reason": interval_reason,
        }

    indeterminate = Counter(grade.outcome for _, grade, label in pairs if label.genus is None)
    return {
        "all": block(pairs),
        "indeterminate_genus": {
            "records": indeterminate.total(),
            "genus_offered": indeterminate[Outcome.UNSUPPORTED_GENUS]
            + indeterminate[Outcome.UNSUPPORTED_ALTERNATIVES],
            "acceptable_abstentions": indeterminate[Outcome.NO_NAME]
            + indeterminate[Outcome.BROADER_ONLY],
            "outcomes": dict(indeterminate),
        },
        "label_not_visible": block([pair for pair in pairs if pair[2].label_visible is False]),
        "label_visibility_unknown": sum(label.label_visible is None for _, _, label in pairs),
    }


def load_labels(path: Path | None) -> dict[str, ReferenceLabel]:
    """Load flat genus labels or objects with family, target IDs and label visibility.

    Invalid metadata fails rather than silently changing which answers are credited.
    Omitted label visibility remains unknown.
    """
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("reference labels must be an object keyed by case ID")
    entries = payload.get("labels", payload)
    if not isinstance(entries, dict):
        raise ValueError("labels must be an object keyed by case ID")

    def name(value: Any, field: str, case: str) -> str:
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z]+", value.strip()):
            raise ValueError(f"{case}: {field} must be a single Latin taxon name")
        return value.strip().lower()

    labels: dict[str, ReferenceLabel] = {}
    for case, value in entries.items():
        entry = value if isinstance(value, dict) else {"genus": value}
        explicit_indeterminate = (
            isinstance(value, dict) and "genus" in entry and entry["genus"] is None
        )
        targets = entry.get("target_subject_ids", [])
        if targets != ALL_SUBJECTS and (
            not isinstance(targets, list)
            or any(not isinstance(t, str) or not t.strip() for t in targets)
        ):
            raise ValueError(f"{case}: target_subject_ids must be a list of IDs or 'all'")
        visible = entry.get("label_visible")
        if visible is not None and not isinstance(visible, bool):
            raise ValueError(f"{case}: label_visible must be true, false or null")
        family = entry.get("family")
        labels[case] = ReferenceLabel(
            genus=None if explicit_indeterminate else name(entry.get("genus"), "genus", case),
            family=name(family, "family", case) if family is not None else None,
            target_subject_ids=ALL_SUBJECTS if targets == ALL_SUBJECTS else tuple(targets),
            label_visible=visible,
        )
    return labels
