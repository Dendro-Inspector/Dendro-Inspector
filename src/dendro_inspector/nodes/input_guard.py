"""Input guard.

Case text and metadata are untrusted. This node records instruction-like content as a
property of the input, not a command. A detected signal can request independent review;
it is not proof of an attack, and absence of a signal is not proof of safety.

The detector is deliberately conservative. Dendrology is full of imperative-sounding prose
("note the fascicles", "compare with Picea") and a guard that flagged ordinary botanical
writing as an attack would be retrained by its users into being switched off. Patterns
cover common English and Ukrainian redirections, regardless of the output locale, not all
paraphrases or languages. Context fencing and deterministic claim caps do not depend on
these patterns matching. Challenge intent is explicit input, never inferred from wording.
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
            r"\b(ignore|disregard|forget)\b[^.\n]{0,40}\b(previous|prior|above|earlier|all)\b[^.\n]{0,20}\b(instruction|prompt|rule|context)"
            r"|\b(ігноруй(?:те)?|нехтуй(?:те)?|забудь(?:те)?)\b[^.\n]{0,40}"
            r"\b(попередн\w*|усі|всі)\b[^.\n]{0,20}\b(інструкці\w*|правил\w*|контекст\w*|промпт\w*)\b",
            re.I,
        ),
    ),
    (
        "role_reassignment",
        re.compile(
            r"\b(you are (now|actually)|act as|pretend to be)\b[^.\n]{0,30}"
            r"\b(assistant|model|system)\b"
            r"|\b((ти|ви) (тепер|відтепер)|дій(?:те)? як)\b[^.\n]{0,30}"
            r"\b(асистент|модель|система)\b",
            re.I,
        ),
    ),
    (
        "system_prompt_probe",
        re.compile(
            r"\b(reveal|print|show|repeat|output|leak)\b[^.\n]{0,30}"
            r"\b(system prompt|instructions|configuration|api key)\b"
            r"|\b(покажи|покажіть|виведи|виведіть|розкрий(?:те)?|повтори|повторіть)\b[^.\n]{0,30}"
            r"\b(системн\w* (промпт|інструкці\w*)|конфігураці\w*|ключ (api|апі))\b",
            re.I,
        ),
    ),
    (
        "output_forcing",
        re.compile(
            r"\b(you must|always) (say|answer|reply|output|return)\b"
            r"|\bregardless of (the )?evidence\b"
            r"|\b(ти (маєш|мусиш)|ви (маєте|мусите)|завжди) "
            r"(казати|говорити|відповідати|кажи|кажіть)\b"
            r"|\bнезалежно від (доказів|свідчень)\b",
            re.I,
        ),
    ),
    (
        "confidence_forcing",
        re.compile(
            r"\b(say|state|report) (it is|that it is)\b[^.\n]{0,30}\b(certain|definitely|100%)\b"
            r"|\b(скажи|скажіть|стверджуй(?:те)?)\b[^.\n]{0,15}\bце\b[^.\n]{0,30}"
            r"(\b(точно|безсумнівно)\b|100%)",
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


def _untrusted_strings(state: GraphState) -> tuple[str, ...]:
    """Case text, filenames, captions and metadata values to scan."""
    case = state.case
    items = [text for text in (case.user_text, case.location, case.habitat) if text]
    for image in case.images:
        items.append(image.path.name)
        if image.caption:
            items.append(image.caption)
    items.extend(value for _, value in sorted(case.metadata.items()))
    return tuple(items)


def scan_for_instructions(state: GraphState) -> tuple[str, ...]:
    """Return the categories of instruction-like content found. Order-stable."""
    found: list[str] = []
    for text in _untrusted_strings(state):
        for label, pattern in _INSTRUCTION_PATTERNS:
            if pattern.search(text) and label not in found:
                found.append(label)
    return tuple(found)


def build_report(state: GraphState) -> GuardReport:
    """Build the report without a model; image-path checks consult the filesystem."""
    case = state.case
    signals = scan_for_instructions(state)
    missing = tuple(image.image_id for image in case.images if not image.exists)

    notes: list[str] = []
    if signals:
        notes.append(
            "Instruction-like content recorded as evidence about the input. "
            "It is not a command; the escalation policy may request independent review."
        )
    if missing:
        notes.append(f"{len(missing)} referenced image file(s) could not be read from disk.")

    has_any_input = bool(case.images) or bool(case.user_text)
    failure_reason = None if has_any_input else "case contains neither an image nor user text"

    return GuardReport(
        safe_to_continue=has_any_input,
        instruction_like_signals=signals,
        missing_images=missing,
        user_challenges_previous_result=case.user_challenges_previous_result,
        controlled_failure_reason=failure_reason,
        notes=tuple(notes),
    )


async def run(state: GraphState, ctx: NodeContext) -> GraphState:
    """Record what the input contains, then continue on the declared topology."""
    del ctx
    return state.evolve(guard=build_report(state))
