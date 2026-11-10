"""Does the delivered answer say what the decision engine decided?

The structured decision is authoritative: it is produced by deterministic rules from the
recorded evidence. Everything a reader sees — the per-subject result, the verdict line,
the tone the whole answer is voiced in — is rendered from it, and must not contradict it.

These checks are structural, never semantic guesses about free text. Each returns a stable
machine-readable code, so a regression shows up as a count in an evaluation table rather
than as a sentence someone has to notice.
"""

from __future__ import annotations

from dendro_inspector.knowledge.evidence_hierarchy import BAND_DECISIVE
from dendro_inspector.schemas.decisions import NO_CLAIM_STATUSES, CaseResponse, ToneMode
from dendro_inspector.schemas.taxon import Confidence

#: Codes, kept here so tests, traces and the evaluation harness agree on spelling.
RESULT_WITHOUT_DECISION = "result_without_decision"
DECISION_WITHOUT_RESULT = "decision_without_result"
RESOLUTION_MISMATCH = "resolution_mismatch"
CONFIDENCE_MISMATCH = "confidence_mismatch"
BAND_MISMATCH = "band_mismatch"
VERDICT_OMITS_BAND = "verdict_omits_band"
TEXT_OMITS_VERDICT = "text_omits_verdict"
RESTRAINT_VOICE_WITH_DECISIVE_BAND = "restraint_voice_with_decisive_band"


def explanation_conflicts(
    response: CaseResponse, delivered_mode: ToneMode | None = None
) -> tuple[str, ...]:
    """Every way the rendered answer disagrees with its own decisions, as stable codes.

    ``delivered_mode`` is the voice actually used, which the tone layer may have narrowed
    from ``response.tone_mode``; it defaults to the recorded mode.
    """
    issues: list[str] = []
    decisions = {decision.subject_id: decision for decision in response.decisions}
    results = {result.subject: result for result in response.results}

    for subject in sorted(results.keys() - decisions.keys()):
        issues.append(f"{RESULT_WITHOUT_DECISION}:{subject}")
    for subject in sorted(decisions.keys() - results.keys()):
        issues.append(f"{DECISION_WITHOUT_RESULT}:{subject}")

    for subject in sorted(decisions.keys() & results.keys()):
        decision, result = decisions[subject], results[subject]
        if result.taxonomic_resolution is not decision.resolution:
            issues.append(f"{RESOLUTION_MISMATCH}:{subject}")
        if result.confidence is not decision.confidence:
            issues.append(f"{CONFIDENCE_MISMATCH}:{subject}")
        if result.confidence_band != decision.confidence_band:
            issues.append(f"{BAND_MISMATCH}:{subject}")
        # A no-claim verdict states the absence of a taxon and carries no band by design.
        if decision.status not in NO_CLAIM_STATUSES and decision.confidence_band not in (
            result.verdict
        ):
            issues.append(f"{VERDICT_OMITS_BAND}:{subject}")
        if response.human_readable and result.verdict not in response.human_readable:
            issues.append(f"{TEXT_OMITS_VERDICT}:{subject}")

    # The cautious voice opens by declining a 95+ claim; it cannot frame one.
    mode = delivered_mode or response.tone_mode
    if mode is ToneMode.CAUTIOUS and any(
        decision.confidence is Confidence.HIGH and decision.confidence_band == BAND_DECISIVE
        for decision in response.decisions
    ):
        issues.append(RESTRAINT_VOICE_WITH_DECISIVE_BAND)
    return tuple(issues)
