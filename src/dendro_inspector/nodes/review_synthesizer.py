"""Deterministic review admissibility and exact finding-bound rerank synthesis."""

from __future__ import annotations

from dendro_inspector.graph.executor import NodeContext
from dendro_inspector.graph.state import GraphState
from dendro_inspector.knowledge.candidate_validation import (
    candidate_ranking_signature,
    validate_candidate_set,
)
from dendro_inspector.knowledge.loader import KnowledgeBase
from dendro_inspector.schemas.candidates import Candidate, CandidateSet
from dendro_inspector.schemas.evidence import EvidencePacket, Observation
from dendro_inspector.schemas.reviews import (
    AdmittedRerank,
    CorrectionDirective,
    FindingCategory,
    FindingOrigin,
    FindingStatus,
    Impact,
    ReasonCode,
    RequiredAction,
    ReviewFinding,
    ReviewResult,
    ReviewStatus,
    ReviewSynthesis,
    Severity,
)
from dendro_inspector.schemas.taxon import Confidence, Resolution, confidence_rank, resolution_rank

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

MaterialSignature = tuple[
    FindingCategory,
    str | None,
    RequiredAction,
    Impact,
    tuple[str, ...],
    str | None,
]


def _evidence_sources(
    evidence: EvidencePacket | None, evidence_id: str
) -> tuple[Observation, ...] | None:
    """Resolve one evidence id; ambiguous or unknown ids fail closed."""
    if evidence is None:
        return None
    observations = tuple(o for o in evidence.observations if o.observation_id == evidence_id)
    inferences = tuple(i for i in evidence.inferences if i.inference_id == evidence_id)
    if len(observations) + len(inferences) != 1:
        return None
    if observations:
        return observations

    by_id = {observation.observation_id: observation for observation in evidence.observations}
    sources = tuple(
        by_id[source_id] for source_id in inferences[0].derived_from if source_id in by_id
    )
    if len(sources) != len(inferences[0].derived_from):
        return None
    return sources or None


def _effective_subject(
    finding: ReviewFinding,
    result: ReviewResult,
    evidence: EvidencePacket | None,
    reviewed_evidence_ids: frozenset[str] | None,
) -> tuple[str | None, ReasonCode | None]:
    """Resolve one unambiguous subject and reject foreign evidence references."""
    if (
        finding.subject_id is not None
        and result.subject_id is not None
        and finding.subject_id != result.subject_id
    ):
        return None, ReasonCode.OUT_OF_SCOPE

    subject_id = finding.subject_id or result.subject_id
    referenced_subjects: set[str] = set()
    for evidence_id in finding.evidence_ids:
        # Resolution first, scope second. The projection currently carries the whole packet,
        # so checking scope first would report every invented id as out_of_scope and retire
        # `evidence_id_unknown` for model findings - collapsing "the model hallucinated an id"
        # and "the model reached into another subject" into one code the evals cannot separate.
        sources = _evidence_sources(evidence, evidence_id)
        if sources is None:
            return subject_id, ReasonCode.EVIDENCE_ID_UNKNOWN
        if reviewed_evidence_ids is not None and evidence_id not in reviewed_evidence_ids:
            return subject_id, ReasonCode.OUT_OF_SCOPE
        referenced_subjects.update(source.subject_id for source in sources)

    if subject_id is not None and referenced_subjects - {subject_id}:
        return subject_id, ReasonCode.OUT_OF_SCOPE
    if subject_id is None:
        if len(referenced_subjects) == 1:
            subject_id = next(iter(referenced_subjects))
        elif len(referenced_subjects) > 1:
            return None, ReasonCode.OUT_OF_SCOPE
        elif evidence is not None and len(evidence.subjects) == 1:
            subject_id = evidence.subjects[0].subject_id
    return subject_id, None


def _material_signature(finding: ReviewFinding) -> MaterialSignature:
    return (
        finding.category,
        finding.subject_id,
        finding.required_action,
        finding.impact,
        tuple(sorted(set(finding.evidence_ids))),
        finding.proposed_taxon,
    )


def _in_scope(
    evidence_ids: tuple[str, ...], reviewed_evidence_ids: frozenset[str] | None
) -> tuple[str, ...]:
    """Drop citations the reviewer was never shown. `None` means no scope was recorded."""
    if reviewed_evidence_ids is None:
        return evidence_ids
    return tuple(
        evidence_id for evidence_id in evidence_ids if evidence_id in reviewed_evidence_ids
    )


def _validated_recommendation(
    result: ReviewResult,
    subject_id: str | None,
    evidence: EvidencePacket | None,
    knowledge: KnowledgeBase,
    reviewed_evidence_ids: frozenset[str] | None,
) -> CandidateSet | None:
    if subject_id is None or evidence is None or not result.recommended_candidates:
        return None

    ordered = sorted(result.recommended_candidates, key=lambda candidate: candidate.rank)
    unique: list[Candidate] = []
    seen_taxa: set[str] = set()
    for candidate in ordered:
        if candidate.taxon in seen_taxa:
            continue
        seen_taxa.add(candidate.taxon)
        unique.append(
            candidate.model_copy(
                update={
                    "supporting_evidence_ids": _in_scope(
                        candidate.supporting_evidence_ids, reviewed_evidence_ids
                    ),
                    "contradicting_evidence_ids": _in_scope(
                        candidate.contradicting_evidence_ids, reviewed_evidence_ids
                    ),
                    "rank": len(unique) + 1,
                }
            )
        )

    proposed = CandidateSet(subject_id=subject_id, candidates=tuple(unique))
    validated = validate_candidate_set(proposed, evidence, knowledge)
    return validated if validated.candidates else None


def judge_finding(
    finding: ReviewFinding,
    *,
    known_taxa: frozenset[str],
    seen: set[MaterialSignature],
    rerank: CandidateSet | None,
) -> tuple[bool, ReasonCode]:
    """Decide whether one already-subject-validated finding is admissible."""
    if _material_signature(finding) in seen:
        return False, ReasonCode.RESTATES_EXISTING_FINDING

    if finding.required_action is RequiredAction.RERANK_CANDIDATES:
        if rerank is None:
            return False, ReasonCode.NOT_ACTIONABLE
        surviving_taxa = {candidate.taxon for candidate in rerank.candidates}
        if finding.proposed_taxon is not None and finding.proposed_taxon not in surviving_taxa:
            return False, ReasonCode.NOT_ACTIONABLE

    if finding.evidence_ids:
        return True, ReasonCode.REFERENCES_VISIBLE_EVIDENCE

    if finding.category is FindingCategory.CONTRACT_VIOLATION:
        return True, ReasonCode.IDENTIFIES_CONTRACT_VIOLATION

    if finding.category in _CONTRADICTION_CATEGORIES:
        return True, ReasonCode.IDENTIFIES_CONTRADICTION

    if finding.category in _CALIBRATION_CATEGORIES:
        return True, ReasonCode.IMPROVES_CALIBRATION

    if finding.category is FindingCategory.OVERLOOKED_ALTERNATIVE:
        proposed = {finding.proposed_taxon} if finding.proposed_taxon is not None else set()
        if rerank is not None:
            proposed.update(candidate.taxon for candidate in rerank.candidates)
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


def _reranks_conflict(reranks: list[AdmittedRerank]) -> bool:
    by_subject: dict[str, set[tuple[tuple[object, ...], ...]]] = {}
    for rerank in reranks:
        by_subject.setdefault(rerank.candidate_set.subject_id, set()).add(
            candidate_ranking_signature(rerank.candidate_set)
        )
    return any(len(rankings) > 1 for rankings in by_subject.values())


def adjudicate(
    results: tuple[ReviewResult, ...],
    *,
    evidence: EvidencePacket | None,
    knowledge: KnowledgeBase,
) -> ReviewSynthesis:
    """Adjudicate deterministic findings first and bind only valid exact reranks."""
    accepted: list[ReviewFinding] = []
    rejected: list[ReviewFinding] = []
    admitted_reranks: list[AdmittedRerank] = []
    seen: set[MaterialSignature] = set()
    known_taxa = frozenset(knowledge.available_taxon_ids())

    entries = [
        (result_index, finding_index, result, finding)
        for result_index, result in enumerate(results)
        for finding_index, finding in enumerate(result.findings)
    ]
    entries.sort(
        key=lambda entry: (
            0 if entry[3].origin is FindingOrigin.DETERMINISTIC else 1,
            entry[0],
            entry[1],
        )
    )

    for _, _, result, finding in entries:
        reviewed_evidence_ids = (
            None
            if result.reviewed_evidence_ids is None
            else frozenset(result.reviewed_evidence_ids)
        )
        subject_id, subject_error = _effective_subject(
            finding,
            result,
            evidence,
            None if finding.origin is FindingOrigin.DETERMINISTIC else reviewed_evidence_ids,
        )
        effective = finding.model_copy(update={"subject_id": subject_id})
        rerank = (
            _validated_recommendation(
                result,
                subject_id,
                evidence,
                knowledge,
                reviewed_evidence_ids,
            )
            if finding.required_action is RequiredAction.RERANK_CANDIDATES
            else None
        )
        if subject_error is None:
            admitted, reason = judge_finding(
                effective,
                known_taxa=known_taxa,
                seen=seen,
                rerank=rerank,
            )
        else:
            admitted, reason = False, subject_error

        decided = effective.model_copy(
            update={
                "status": FindingStatus.ACCEPTED if admitted else FindingStatus.REJECTED,
                "reason_code": reason,
            }
        )
        if admitted:
            seen.add(_material_signature(effective))
            accepted.append(decided)
            if rerank is not None:
                admitted_reranks.append(
                    AdmittedRerank(
                        finding=decided,
                        reviewer=result.reviewer,
                        candidate_set=rerank,
                    )
                )
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
        f"{rerank.candidate_set.subject_id}: finding {rerank.finding_id} admitted rerank"
        for rerank in admitted_reranks
    )
    reviewer_disagreement = _reviewers_disagree(results) or _reranks_conflict(admitted_reranks)
    critical_finding = any(finding.severity is Severity.CRITICAL for finding in accepted)

    return ReviewSynthesis(
        accepted_findings=tuple(accepted),
        rejected_findings=tuple(rejected),
        admitted_reranks=tuple(admitted_reranks),
        required_corrections=corrections,
        candidate_delta=candidate_delta,
        confidence_delta=_lowest(
            {r.recommended_confidence for r in results if r.recommended_confidence}
        ),
        resolution_delta=_broadest(
            {r.recommended_resolution for r in results if r.recommended_resolution}
        ),
        retry_required=retry_required,
        reviewer_disagreement=reviewer_disagreement,
        escalation_recommended=reviewer_disagreement or critical_finding,
        unresolvable=unresolvable,
    )


async def run(state: GraphState, ctx: NodeContext) -> GraphState:
    synthesis = adjudicate(
        state.reviews,
        evidence=state.evidence,
        knowledge=ctx.knowledge,
    )
    return state.evolve(synthesis=synthesis)
