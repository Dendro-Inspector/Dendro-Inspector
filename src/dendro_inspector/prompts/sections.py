"""The domain prompt's numbered sections, and which of them each model node is sent.

Experimental, behind ``PromptConfig.domain_sections`` and off by default: every node
receives the whole prompt unless it is set to ``per_node``.

The prompt is never rewritten. It is cut at its own section fences, and a node receives
the chosen sections as the exact bytes they are on disk, in their original order. The
sealed hash still covers the whole file, and a prompt whose sections cannot be read fails
closed rather than being sent whole or in part by guesswork.

What is left out is what deterministic code implements and no model call decides: tone
and jokes (sections 4 and 5), the response formats (7 to 11) and admitting a mistake (12)
belong to the response composer and the tone layer (see the derivation table in
``AGENTS.md``). The other cuts follow what a node is asked to do.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import pairwise

#: The fence character the prompt draws its section headings with, in both locales.
FENCE = "━"

#: Section numbers run from 0 (the untitled preamble) through this one.
LAST_SECTION = 18

_NUMBERED = re.compile(r"^(\d{1,2})\.\s")

#: Every node is sent these: the preamble, the core algorithm, the evidence hierarchy,
#: the confidence scale, the key failure cases and the final rule.
SHARED_SECTIONS: frozenset[int] = frozenset({0, 1, 2, 6, 13, 18})

#: Sections a node is sent beyond the shared ones. 3 checking the user's version,
#: 14 base species, 15 cross-sections and firewood, 16 when to ask for photographs,
#: 17 special examples. A node not listed here is sent the whole prompt.
NODE_SECTIONS: dict[str, frozenset[int]] = {
    "planner": SHARED_SECTIONS | {15, 16},
    "evidence_extractor": SHARED_SECTIONS | {14, 15, 17},
    "candidate_generator": SHARED_SECTIONS | {3, 14, 15, 17},
    "botanical_reviewer": SHARED_SECTIONS | {3, 14, 15, 17},
    "confusion_reviewer": SHARED_SECTIONS | {3, 14, 15, 17},
    "confidence_reviewer": SHARED_SECTIONS | {3, 16},
    "arbiter": SHARED_SECTIONS | {3, 14, 15, 17},
}


class DomainSectionError(ValueError):
    """The domain prompt's sections cannot be read, so no section can be chosen."""


@dataclass(frozen=True)
class Section:
    number: int
    title: str
    text: str


def split_sections(text: str) -> tuple[Section, ...]:
    """Cut the prompt at its fenced headings; the pieces join back to ``text`` exactly.

    A heading is a fence line, a title line and a fence line. The first title has no
    number (the core algorithm) and is section 1; every later one is numbered, and the
    numbers must run 1, 2, ... ``LAST_SECTION`` with none missing or repeated.
    """
    lines = text.splitlines(keepends=True)
    starts: list[tuple[int, int, str]] = []
    index = 0
    while index + 2 < len(lines):
        if _is_fence(lines[index]) and _is_fence(lines[index + 2]):
            title = lines[index + 1].strip()
            match = _NUMBERED.match(title)
            if match is not None:
                number = int(match.group(1))
            elif not starts:
                number = 1
            else:
                msg = f"unnumbered section heading after section {starts[-1][1]}: {title!r}"
                raise DomainSectionError(msg)
            starts.append((index, number, title))
            index += 3
            continue
        index += 1
    numbers = [number for _, number, _ in starts]
    if numbers != list(range(1, LAST_SECTION + 1)):
        msg = f"expected sections 1-{LAST_SECTION} in order, found {numbers}"
        raise DomainSectionError(msg)
    bounds = [0, *(start for start, _, _ in starts), len(lines)]
    titles = ["preamble", *(title for _, _, title in starts)]
    sections = tuple(
        Section(number=number, title=titles[number], text="".join(lines[begin:end]))
        for number, (begin, end) in enumerate(pairwise(bounds))
    )
    if "".join(section.text for section in sections) != text:  # pragma: no cover - by construction
        msg = "sections do not reassemble to the prompt"
        raise DomainSectionError(msg)
    return sections


def sections_for(node: str) -> frozenset[int] | None:
    """The section numbers ``node`` is sent, or None for the whole prompt."""
    return NODE_SECTIONS.get(node)


def project(text: str, node: str) -> str:
    """The part of the prompt ``node`` is sent: whole, or its sections in prompt order."""
    chosen = sections_for(node)
    if chosen is None:
        return text
    return "".join(section.text for section in split_sections(text) if section.number in chosen)


def shared_prefix(text: str) -> str:
    """The leading sections every listed node is sent, which therefore start every prompt.

    The cache boundary an adapter may mark. Sent whole to an unlisted node too, since the
    whole prompt starts with the same sections.
    """
    common = frozenset.intersection(*NODE_SECTIONS.values())
    prefix = []
    for section in split_sections(text):
        if section.number not in common:
            break
        prefix.append(section.text)
    return "".join(prefix)


def _is_fence(line: str) -> bool:
    stripped = line.strip()
    return len(stripped) >= 8 and set(stripped) == {FENCE}
