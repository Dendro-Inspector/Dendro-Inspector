"""Response composer.

Produces the factual answer — structured result plus plain human-readable text — **before**
the tone layer runs. The ordering is the point: the correct answer exists, in full, without
any personality applied to it.

This node also decides two things the tone layer is then not allowed to change:

* **which response format applies** (domain prompt sections 7-11): no version, with
  version, user correct, user wrong, weak photograph;
* **how hard the duck may bite** (sections 4, 5 and 12). Hard mode is a conjunction of
  conditions, not a mood, and being corrected outranks all of them.

Output language defaults to Ukrainian, as the domain prompt specifies.
"""

from __future__ import annotations

from evil_duck_dendro.graph.executor import NodeContext
from evil_duck_dendro.graph.state import GraphState
from evil_duck_dendro.knowledge.evidence_hierarchy import EvidenceTier
from evil_duck_dendro.nodes._support import locale_of
from evil_duck_dendro.schemas.decisions import (
    CaseResponse,
    DecisionStatus,
    FinalDecision,
    ResponseFormat,
    StructuredFinalResult,
    ToneMode,
    UserClaimVerdict,
)
from evil_duck_dendro.schemas.reviews import FindingCategory
from evil_duck_dendro.schemas.taxon import Confidence, Resolution

NODE = "response_composer"

DEFAULT_LOCALE = "uk"

_STATUS_PHRASE: dict[str, dict[DecisionStatus, str]] = {
    "uk": {
        DecisionStatus.IDENTIFIED: "Визначено",
        DecisionStatus.PROBABLE: "Найімовірніше",
        DecisionStatus.INSUFFICIENT_EVIDENCE: "Недостатньо доказів",
        DecisionStatus.CONFLICTING_EVIDENCE: "Докази суперечать одне одному",
        DecisionStatus.UNSUPPORTED_USER_CLAIM: "Заявлений об'єкт не підтверджується фото",
    },
    "en": {
        DecisionStatus.IDENTIFIED: "Identified",
        DecisionStatus.PROBABLE: "Probable",
        DecisionStatus.INSUFFICIENT_EVIDENCE: "Insufficient evidence",
        DecisionStatus.CONFLICTING_EVIDENCE: "Conflicting evidence",
        DecisionStatus.UNSUPPORTED_USER_CLAIM: "Stated object type not supported by the image",
    },
}

_VERDICT_PHRASE: dict[str, dict[UserClaimVerdict, str]] = {
    "uk": {
        UserClaimVerdict.ACCEPTED: "приймається",
        UserClaimVerdict.POSSIBLE: "можлива, але по цьому фото не залізобетонна",
        UserClaimVerdict.DOUBTFUL: "скоріше ні",
        UserClaimVerdict.REJECTED: "не проходить",
        UserClaimVerdict.NOT_EVALUABLE: "по цьому фото я не можу її ні підтвердити, ні спростувати",
        UserClaimVerdict.NOT_PROVIDED: "—",
    },
    "en": {
        UserClaimVerdict.ACCEPTED: "accepted",
        UserClaimVerdict.POSSIBLE: "possible, but not settled by this photograph",
        UserClaimVerdict.DOUBTFUL: "doubtful",
        UserClaimVerdict.REJECTED: "does not hold",
        UserClaimVerdict.NOT_EVALUABLE: "this photograph can neither support nor refute it",
        UserClaimVerdict.NOT_PROVIDED: "—",
    },
}

_RESOLUTION_PHRASE: dict[str, dict[Resolution, str]] = {
    "uk": {
        Resolution.FAMILY: "родина",
        Resolution.GENUS: "рід",
        Resolution.SPECIES_GROUP: "група видів",
        Resolution.SPECIES: "вид",
        Resolution.UNKNOWN: "невідомо",
    },
    "en": {
        Resolution.FAMILY: "family",
        Resolution.GENUS: "genus",
        Resolution.SPECIES_GROUP: "species group",
        Resolution.SPECIES: "species",
        Resolution.UNKNOWN: "unknown",
    },
}

_LABELS: dict[str, dict[str, str]] = {
    "uk": {
        "subject": "Об'єкт",
        "version": "Версія",
        "my_verdict": "Мій вердикт",
        "features": "Ознаки",
        "why_not": "Чому не найближчі альтернативи",
        "verdict": "Вердикт",
        "confidence": "Впевненість",
        "need_photo": "Для точнішого висновку потрібне фото",
        "uncertain": "Що лишається невизначеним",
        "none_recorded": "не зафіксовано",
        "no_taxon": "по цьому фото не можна чесно визначити породу",
        "nothing_would_change": "додаткове фото вже нічого не змінить",
        "placeholder": (
            "> **Демонстраційні дані.** Довідник порід у цій збірці — мінімальний "
            "демонстраційний набір, не перевірений дендрологом. Сприймай таксономію вище "
            "як демонстрацію механіки, а не як визначення."
        ),
        "no_result": "Для цього запиту не вдалося сформувати результат.",
    },
    "en": {
        "subject": "Subject",
        "version": "Your version",
        "my_verdict": "My verdict",
        "features": "Evidence",
        "why_not": "Why not the nearest alternatives",
        "verdict": "Verdict",
        "confidence": "Confidence",
        "need_photo": "For a firmer conclusion, photograph",
        "uncertain": "What remains uncertain",
        "none_recorded": "none recorded",
        "no_taxon": "this photograph cannot honestly carry a taxon",
        "nothing_would_change": "no further photograph would change this",
        "placeholder": (
            "> **Placeholder knowledge.** This build ships a minimal demonstration pack "
            "that has not been reviewed by a dendrologist. Treat the taxonomy above as a "
            "wiring demonstration, not as identification."
        ),
        "no_result": "No result could be produced for this case.",
    },
}


def select_format(decision: FinalDecision, tone: ToneMode) -> ResponseFormat:
    """Pick the domain prompt's response shape for this decision."""
    if decision.status is DecisionStatus.INSUFFICIENT_EVIDENCE:
        return ResponseFormat.WEAK_PHOTO
    match decision.user_claim_verdict:
        case UserClaimVerdict.NOT_PROVIDED:
            return ResponseFormat.NO_VERSION
        case UserClaimVerdict.ACCEPTED:
            return ResponseFormat.USER_CORRECT
        case UserClaimVerdict.REJECTED if tone is ToneMode.HARD:
            return ResponseFormat.USER_WRONG
        case _:
            return ResponseFormat.WITH_VERSION


def decide_tone(state: GraphState) -> tuple[ToneMode, bool]:
    """Decide how hard the duck may bite, and whether a joke is permitted.

    Section 4 lists the conditions for hard mode as a conjunction, and section 12 makes
    correction outrank everything. Every clause below is one of the prompt's, and each is
    checkable without asking a model how confident it feels.
    """
    guard = state.guard
    decisions = state.decisions

    # Section 12: the user corrected us and had the context to do it. No sarcasm, no joke.
    if guard is not None and guard.user_challenges_previous_result:
        return ToneMode.CORRECTIVE, False

    if not decisions:
        return ToneMode.CAUTIOUS, False

    accepted = {
        finding.category
        for synthesis in (state.synthesis, state.arbiter_synthesis)
        if synthesis is not None
        for finding in synthesis.accepted_findings
    }
    restraint_findings = accepted & {
        FindingCategory.COLOUR_OVERWEIGHTING,
        FindingCategory.UNSUPPORTED_CLAIM,
        FindingCategory.OVERLOOKED_ALTERNATIVE,
        FindingCategory.MISSING_DECISIVE_FEATURE,
    }

    leaders_close = any(candidate_set.leaders_are_close() for candidate_set in state.candidate_sets)

    hard = all(
        (
            bool(state.case.user_claim),
            not state.case.user_has_field_context,
            all(d.user_claim_verdict is UserClaimVerdict.REJECTED for d in decisions),
            all(d.confidence is Confidence.HIGH for d in decisions),
            all(d.evidence_tier >= int(EvidenceTier.FOLIAGE) for d in decisions),
            not restraint_findings,
            not leaders_close,
        )
    )
    if hard:
        return ToneMode.HARD, True

    cautious = any(
        d.confidence is Confidence.LOW or d.evidence_tier <= int(EvidenceTier.BARK)
        for d in decisions
    )
    return (ToneMode.CAUTIOUS if cautious else ToneMode.MEASURED), False


def verdict_line(decision: FinalDecision, locale: str) -> str:
    """One sentence that renders the exact identity selected by the decision engine."""
    labels = _LABELS[locale]
    phrase = _STATUS_PHRASE[locale][decision.status]
    if decision.selected_taxon is None or decision.resolution is Resolution.UNKNOWN:
        return f"{phrase} — {labels['no_taxon']}."
    name = decision.selected_taxon_display_name or decision.selected_taxon
    level = _RESOLUTION_PHRASE[locale][decision.resolution]
    return f"{phrase}: {name} ({level}), {labels['confidence'].lower()} {decision.confidence_band}."


def build_result(
    decision: FinalDecision,
    locale: str,
) -> StructuredFinalResult:
    supporting = (decision.strongest_support,) if decision.strongest_support else ()
    ruled_out: tuple[str, ...] = ()
    if decision.nearest_alternative:
        reason = decision.strongest_contradiction or _LABELS[locale]["none_recorded"]
        ruled_out = (f"{decision.nearest_alternative}: {reason}",)
    return StructuredFinalResult(
        verdict=verdict_line(decision, locale),
        subject=decision.subject_id,
        taxonomic_resolution=decision.resolution,
        confidence=decision.confidence,
        confidence_band=decision.confidence_band,
        user_claim_verdict=decision.user_claim_verdict,
        supporting_evidence=supporting,
        ruled_out=ruled_out,
        strongest_contradiction=decision.strongest_contradiction,
        nearest_alternative=decision.nearest_alternative,
        limitations=decision.unresolved_questions,
        best_next_photo=decision.best_next_photo,
    )


def _render_block(
    result: StructuredFinalResult,
    decision: FinalDecision,
    state: GraphState,
    locale: str,
    response_format: ResponseFormat,
) -> str:
    labels = _LABELS[locale]
    lines = [f"### {labels['subject']}: {result.subject}", ""]

    if response_format is not ResponseFormat.NO_VERSION and state.case.user_claim:
        lines.append(f"**{labels['version']}:** {state.case.user_claim}")
        lines.append(
            f"**{labels['my_verdict']}:** {_VERDICT_PHRASE[locale][decision.user_claim_verdict]}"
        )
        lines.append("")

    lines.append(f"**{labels['verdict']}.** {result.verdict}")
    lines.append("")

    lines.append(f"**{labels['features']}:**")
    if result.supporting_evidence:
        lines.extend(f"- {item}" for item in result.supporting_evidence)
    else:
        lines.append(f"- {labels['none_recorded']}")
    lines.append("")

    lines.append(f"**{labels['why_not']}:**")
    if result.ruled_out:
        lines.extend(f"- {item}" for item in result.ruled_out)
    else:
        lines.append(f"- {labels['none_recorded']}")
    lines.append("")

    lines.append(f"**{labels['uncertain']}:**")
    if result.limitations:
        lines.extend(f"- {item}" for item in result.limitations)
    else:
        lines.append(f"- {labels['none_recorded']}")
    lines.append("")

    lines.append(f"**{labels['confidence']}:** {result.confidence_band}")
    lines.append("")

    if result.best_next_photo:
        lines.append(
            f"**{labels['need_photo']}:** `{result.best_next_photo.target}` — "
            f"{result.best_next_photo.reason}"
        )
    else:
        lines.append(f"**{labels['need_photo']}:** {labels['nothing_would_change']}")
    return "\n".join(lines)


def render_human_readable(
    results: tuple[StructuredFinalResult, ...],
    decisions: tuple[FinalDecision, ...],
    state: GraphState,
    *,
    locale: str,
    response_format: ResponseFormat,
    placeholder_knowledge: bool,
) -> str:
    if not results:
        return _LABELS[locale]["no_result"]

    blocks = [
        _render_block(result, decision, state, locale, response_format)
        for result, decision in zip(results, decisions, strict=False)
    ]
    text = "\n\n---\n\n".join(blocks)
    if placeholder_knowledge:
        text += "\n\n" + _LABELS[locale]["placeholder"]
    return text


async def run(state: GraphState, ctx: NodeContext) -> GraphState:
    locale = locale_of(state)
    tone, joke_allowed = decide_tone(state)
    results = tuple(build_result(decision, locale) for decision in state.decisions)
    response_format = (
        select_format(state.decisions[0], tone) if state.decisions else ResponseFormat.NO_VERSION
    )
    placeholder = any(
        (card := ctx.knowledge.try_taxon(decision.selected_taxon)) is not None
        and card.placeholder_content
        for decision in state.decisions
        if decision.selected_taxon
    ) or any(
        leader is not None
        and (card := ctx.knowledge.try_taxon(leader.taxon)) is not None
        and card.placeholder_content
        for candidate_set in state.candidate_sets
        if (leader := candidate_set.leader) is not None
    )
    response = CaseResponse(
        case_id=state.case.case_id,
        results=results,
        decisions=state.decisions,
        human_readable=render_human_readable(
            results,
            state.decisions,
            state,
            locale=locale,
            response_format=response_format,
            placeholder_knowledge=placeholder,
        ),
        tone_mode=tone,
        response_format=response_format,
        joke_allowed=joke_allowed,
        locale=locale,
    )
    return state.evolve(response=response)
