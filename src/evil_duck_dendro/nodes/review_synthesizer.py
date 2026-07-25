"""Review synthesis — the adjudication core.

A finding is **not** accepted because a model produced it. It is accepted when it meets one
of five admissibility tests (spec section 13), and rejected with a reason code otherwise.
Rejections are kept, not discarded: "the reviewer said X and we did not act on it, because
Y" is exactly the trail that makes a disputed answer defensible later.

This module is deterministic and shared with the arbiter synthesizer, so a second model's
findings face precisely the same bar as the first model's.
"""

from __future__ import annotations

from evil_duck_dendro.graph.executor import NodeContext
from evil_duck_dendro.graph.state import GraphState
from evil_duck_dendro.schemas.evidence import EvidencePacket
from evil_duck_dendro.schemas.reviews import (
    CorrectionDirective,
    FindingCategory,
    FindingStatus,
    ReasonCode,
    RequiredAction,
    ReviewFinding,
    ReviewResult,
    ReviewStatus,
    ReviewSynthesis,
    Severity,
)
from evil_duck_dendro.schemas.taxon import Confidence, Resolution, confidence_rank, resolution_rank

NODE = "review_synthesizer"

_CONTRADICTION_CATEGORIES = frozenset(
    {
        FindingCategory.BOTANICAL_CONTRADICTION,
        FindingCategory.INVALID_NEGATIVE_EVIDENCE,
        FindingCategory.SUBJECT_CONTAMINATION,
    }
)

_CALIBRATION_CATEGORIES = frozenset(
    {
        FindingCategory.CONFIDENCE_MISCALIBRATION,
        FindingCategory.RESOLUTION_TOO_SPECIFIC,
        FindingCategory.MISSING_DECISIVE_FEATURE,
        FindingCategory.COLOUR_OVERWEIGHTING,
        FindingCategory.UNSUPPORTED_CLAIM,
    }
)


def _known_evidence_ids(evidence: EvidencePacket | None) -> frozenset[str]:
    if evidence is None:
        return frozenset()
    return frozenset(
        {observation.observation_id for observation in evidence.observations}
        | {inference.inference_id for inference in evidence.inferences}
    )


def judge_finding(
    finding: ReviewFinding,
    *,
    evidence: EvidencePacket | None,
    result: ReviewResult,
    known_taxa: frozenset[str],
    seen: set[tuple[FindingCategory, str | None]],
) -> tuple[bool, ReasonCode]:
    """Decide whether one finding is admissible, and say why."""
    signature = (finding.category, finding.subject_id)
    if signature in seen:
        return False, ReasonCode.RESTATES_EXISTING_FINDING

    if finding.evidence_ids:
        known = _known_evidence_ids(evidence)
        if not set(finding.evidence_ids) <= known:
            return False, ReasonCode.EVIDENCE_ID_UNKNOWN
        return True, ReasonCode.REFERENCES_VISIBLE_EVIDENCE

    if finding.category is FindingCategory.CONTRACT_VIOLATION:
        return True, ReasonCode.IDENTIFIES_CONTRACT_VIOLATION

    if finding.category in _CONTRADICTION_CATEGORIES:
        return True, ReasonCode.IDENTIFIES_CONTRADICTION

    if finding.category in _CALIBRATION_CATEGORIES:
        return True, ReasonCode.IMPROVES_CALIBRATION

    if finding.category is FindingCategory.OVERLOOKED_ALTERNATIVE:
        # Either the finding names the alternative itself, or the reviewer supplied a
        # concrete ranking. Prose describing an alternative counts as neither.
        proposed = {candidate.taxon for candidate in result.recommended_candidates}
        if finding.proposed_taxon is not None:
            proposed.add(finding.proposed_taxon)
        if proposed & known_taxa:
            return True, ReasonCode.PLAUSIBLE_OMITTED_ALTERNATIVE
        return False, ReasonCode.NOT_ACTIONABLE

    if finding.category is FindingCategory.REGION_ASSUMPTION:
        return True, ReasonCode.IMPROVES_CALIBRATION

    if finding.required_action is RequiredAction.NONE:
        return False, ReasonCode.NOT_ACTIONABLE

    return False, ReasonCode.NO_EVIDENCE_REFERENCE


def _reviewers_disagree(results: tuple[ReviewResult, ...]) -> bool:
    resolutions = {r.recommended_resolution for r in results if r.recommended_resolution}
    confidences = {r.recommended_confidence for r in results if r.recommended_confidence}
    statuses = {r.status for r in results}
    conflicting_status = ReviewStatus.PASS in statuses and bool(
        statuses & {ReviewStatus.FAIL_CORRECTABLE, ReviewStatus.FAIL_UNRESOLVABLE}
    )
    return len(resolutions) > 1 or len(confidences) > 1 or conflicting_status


def _broadest(resolutions: set[Resolution]) -> Resolution | None:
    return min(resolutions, key=resolution_rank) if resolutions else None


def _lowest(confidences: set[Confidence]) -> Confidence | None:
    return min(confidences, key=confidence_rank) if confidences else None


def _directive(finding: ReviewFinding) -> CorrectionDirective:
    return CorrectionDirective(
        action=finding.required_action,
        subject_id=finding.subject_id,
        rationale=finding.summary,
    )


def adjudicate(
    results: tuple[ReviewResult, ...],
    *,
    evidence: EvidencePacket | None,
    known_taxa: frozenset[str],
) -> ReviewSynthesis:
    """Apply admissibility rules to every finding and derive the resulting actions."""
    accepted: list[ReviewFinding] = []
    rejected: list[ReviewFinding] = []
    seen: set[tuple[FindingCategory, str | None]] = set()

    for result in results:
        for finding in result.findings:
            admitted, reason = judge_finding(
                finding,
                evidence=evidence,
                result=result,
                known_taxa=known_taxa,
                seen=seen,
            )
            decided = finding.model_copy(
                update={
                    "status": FindingStatus.ACCEPTED if admitted else FindingStatus.REJECTED,
                    "reason_code": reason,
                }
            )
            if admitted:
                seen.add((finding.category, finding.subject_id))
                accepted.append(decided)
            else:
                rejected.append(decided)

    retry_required = any(
        finding.required_action is RequiredAction.RE_EXTRACT_EVIDENCE for finding in accepted
    )
    unresolvable = any(
        finding.required_action is RequiredAction.ABSTAIN and finding.severity is Severity.CRITICAL
        for finding in accepted
    )
    corrections = tuple(
        _directive(finding)
        for finding in accepted
        if finding.required_action is not RequiredAction.NONE
    )
    candidate_delta = tuple(
        f"{finding.subject_id or 'case'}: {finding.summary}"
        for finding in accepted
        if finding.required_action is RequiredAction.RERANK_CANDIDATES
    )

    return ReviewSynthesis(
        accepted_findings=tuple(accepted),
        rejected_findings=tuple(rejected),
        required_corrections=corrections,
        candidate_delta=candidate_delta,
        confidence_delta=_lowest(
            {r.recommended_confidence for r in results if r.recommended_confidence}
        ),
        resolution_delta=_broadest(
            {r.recommended_resolution for r in results if r.recommended_resolution}
        ),
        retry_required=retry_required,
        escalation_recommended=(
            _reviewers_disagree(results)
            or any(finding.severity is Severity.CRITICAL for finding in accepted)
        ),
        unresolvable=unresolvable,
    )


async def run(state: GraphState, ctx: NodeContext) -> GraphState:
    synthesis = adjudicate(
        state.reviews,
        evidence=state.evidence,
        known_taxa=frozenset(ctx.knowledge.available_taxon_ids()),
    )
    return state.evolve(synthesis=synthesis)
