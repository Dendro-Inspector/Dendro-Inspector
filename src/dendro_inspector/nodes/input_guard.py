"""Input guard.

Every filename, caption, EXIF value and line of user text is untrusted. This node records
instruction-like content as a *property of the input* and hands it downstream as evidence.
It never executes it, never lets it reach a node as an instruction, and never routes on it.

The detector is deliberately conservative. Dendrology is full of imperative-sounding prose
("note the fascicles", "compare with Picea") and a guard that flagged ordinary botanical
writing as an attack would be retrained by its users into being switched off.
"""

from __future__ import annotations

import re

from dendro_inspector.graph.executor import NodeContext
from dendro_inspector.graph.state import GraphState, GuardReport

#: Each pattern targets an attempt to *redirect the system*, not a description of a tree.
_INSTRUCTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "override_prior_instructions",
        re.compile(
            r"\b(ignore|disregard|forget)\b[^.\n]{0,40}\b(previous|prior|above|earlier|all)\b[^.\n]{0,20}\b(instruction|prompt|rule|context)",
            re.I,
        ),
    ),
    (
        "role_reassignment",
        re.compile(
            r"\byou are (now|actually)\b"
            r"|\bact as\b[^.\n]{0,30}\b(assistant|model|system)\b"
            r"|\bpretend to be\b",
            re.I,
        ),
    ),
    (
        "system_prompt_probe",
        re.compile(
            r"\b(reveal|print|show|repeat|output|leak)\b[^.\n]{0,30}"
            r"\b(system prompt|instructions|configuration|api key)\b",
            re.I,
        ),
    ),
    (
        "output_forcing",
        re.compile(
            r"\b(you must|always) (say|answer|reply|output|return)\b"
            r"|\bregardless of (the )?evidence\b",
            re.I,
        ),
    ),
    (
        "confidence_forcing",
        re.compile(
            r"\b(say|state|report) (it is|that it is)\b[^.\n]{0,30}\b(certain|definitely|100%)\b",
            re.I,
        ),
    ),
    (
        "tool_or_command_injection",
        re.compile(
            r"</?(system|instructions?)>|\{\{\s*system\s*\}\}|\brm\s+-rf\b|\bcurl\s+http", re.I
        ),
    ),
)

#: A user pushing back on a previous answer is a legitimate escalation trigger (spec §4),
#: not an attack. Kept separate from the injection patterns for exactly that reason.
_CHALLENGE_PATTERN = re.compile(
    r"\b(that('s| is) wrong|you('re| are) wrong|incorrect|not (a |an )?\w+|are you sure|i disagree|"
    r"it('s| is) actually)\b",
    re.I,
)


def _untrusted_strings(state: GraphState) -> tuple[tuple[str, str], ...]:
    """Every untrusted string in the case, tagged with where it came from."""
    case = state.case
    items: list[tuple[str, str]] = []
    if case.user_text:
        items.append(("user_text", case.user_text))
    if case.location:
        items.append(("location", case.location))
    if case.habitat:
        items.append(("habitat", case.habitat))
    for image in case.images:
        items.append((f"filename:{image.image_id}", image.path.name))
        if image.caption:
            items.append((f"caption:{image.image_id}", image.caption))
    items.extend((f"metadata:{key}", value) for key, value in sorted(case.metadata.items()))
    return tuple(items)


def scan_for_instructions(state: GraphState) -> tuple[str, ...]:
    """Return the categories of instruction-like content found. Order-stable."""
    found: list[str] = []
    for _source, text in _untrusted_strings(state):
        for label, pattern in _INSTRUCTION_PATTERNS:
            if pattern.search(text) and label not in found:
                found.append(label)
    return tuple(found)


def build_report(state: GraphState) -> GuardReport:
    """Deterministic guard analysis. Pure — no I/O, no model."""
    case = state.case
    signals = scan_for_instructions(state)
    missing = tuple(image.image_id for image in case.images if not image.exists)
    challenges = bool(case.user_text and _CHALLENGE_PATTERN.search(case.user_text))

    notes: list[str] = []
    if signals:
        notes.append(
            "Instruction-like content recorded as evidence about the input. "
            "It has not been executed and does not alter graph behaviour."
        )
    if missing:
        notes.append(f"{len(missing)} referenced image file(s) could not be read from disk.")

    has_any_input = bool(case.images) or bool(case.user_text)
    failure_reason = None if has_any_input else "case contains neither an image nor user text"

    return GuardReport(
        safe_to_continue=has_any_input,
        instruction_like_signals=signals,
        missing_images=missing,
        user_challenges_previous_result=challenges,
        controlled_failure_reason=failure_reason,
        notes=tuple(notes),
    )


async def run(state: GraphState, ctx: NodeContext) -> GraphState:
    """Record what the input contains, then continue on the declared topology."""
    del ctx
    return state.evolve(guard=build_report(state))
