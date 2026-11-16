"""Trace recording.

A recorder accumulates events during a run and freezes them into an immutable
:class:`RunTrace`. Recorders are per-run objects, never module-level state.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from dendro_inspector.observability.events import (
    GRAPH_VERSION,
    MAX_LEDGER_OBSERVATIONS,
    MAX_LEDGER_SUBJECTS,
    MAX_REVIEW_CANDIDATES,
    MAX_REVIEW_FINDINGS,
    MAX_REVIEW_ROUNDS,
    AbstentionCause,
    AbstentionRecord,
    CandidateSupportRecord,
    CandidateValidationRecord,
    ComponentProjection,
    DecisionComparison,
    DroppedSupportRecord,
    EvidenceContextRecord,
    ImageLimitationRecord,
    InferenceRecord,
    NodeEvent,
    NodeStatus,
    ObservationRecord,
    ObservationUseRecord,
    PlanRecord,
    PromptMetadata,
    ProviderCallRecord,
    RecommendationRecord,
    RerankRecord,
    ReviewerProjectionRecord,
    ReviewerRecord,
    ReviewFindingRecord,
    ReviewRoundRecord,
    RuntimeIdentity,
    RunTrace,
    SubjectRecord,
)
from dendro_inspector.observability.evidence_history import EvidenceRevision, packet_digest
from dendro_inspector.observability.logging import get_logger
from dendro_inspector.schemas.candidates import CardRetrieval
from dendro_inspector.schemas.decisions import AuthorityCheckStatus
from dendro_inspector.schemas.evidence import KnowledgeCoverage
from dendro_inspector.schemas.review_context import ReviewProjection
from dendro_inspector.schemas.routing import RoutingAssessment
from dendro_inspector.schemas.taxon import Confidence, Resolution

if TYPE_CHECKING:
    from dendro_inspector.knowledge.candidate_validation import CandidateValidationResult
    from dendro_inspector.schemas.candidates import Candidate, CandidateProposal, CandidateSet
    from dendro_inspector.schemas.decisions import (
        AuthorityCheckTrace,
        DecisionDerivation,
        FinalDecision,
        FindingReference,
    )
    from dendro_inspector.schemas.evidence import (
        AttestationOutcome,
        EvidencePacket,
        GeneratedEvidencePacket,
    )
    from dendro_inspector.schemas.reviews import (
        Reviewer,
        ReviewFinding,
        ReviewResult,
        ReviewSynthesis,
    )


#: The verdict fields a correction or an arbitration is said to have changed.
_COMPARED_FIELDS = ("status", "selected_taxon", "resolution", "confidence")


def _decision_field_changed(
    before: tuple[FinalDecision, ...],
    after: tuple[FinalDecision, ...],
    field: str,
) -> bool | None:
    """Whether ``field`` moved for any subject that has a verdict on both sides.

    ``None`` when nothing came before, or when no subject can be compared. A subject with a
    verdict on one side only is not a change. Provisional verdicts exist only for subjects
    that had candidates and final verdicts for every subject, so comparing the union of the
    two reported "the arbiter changed the taxon" whenever a subject without candidates sat
    in the same photographs, on runs where the arbiter moved nothing.
    """
    if not before:
        return None
    prior = {decision.subject_id: getattr(decision, field) for decision in before}
    current = {decision.subject_id: getattr(decision, field) for decision in after}
    shared = prior.keys() & current.keys()
    if not shared:
        return None
    return any(prior[subject_id] != current[subject_id] for subject_id in shared)


def _compare_decisions(
    before: tuple[FinalDecision, ...],
    after: tuple[FinalDecision, ...],
) -> DecisionComparison | None:
    """Which subjects the change flags compared, and which had a verdict on one side only."""
    if not before:
        return None
    prior = {decision.subject_id: decision for decision in before}
    current = {decision.subject_id: decision for decision in after}
    compared = tuple(subject_id for subject_id in prior if subject_id in current)
    return DecisionComparison(
        compared_subject_ids=compared,
        changed_subject_ids=tuple(
            subject_id
            for subject_id in compared
            if any(
                getattr(prior[subject_id], field) != getattr(current[subject_id], field)
                for field in _COMPARED_FIELDS
            )
        ),
        only_before_subject_ids=tuple(s for s in prior if s not in current),
        only_after_subject_ids=tuple(s for s in current if s not in prior),
    )


def _candidate_record(candidate: Candidate) -> CandidateSupportRecord:
    return CandidateSupportRecord(
        taxon=candidate.taxon,
        resolution=candidate.resolution,
        score=candidate.score.value,
        rank=candidate.rank,
        supporting_evidence_ids=candidate.supporting_evidence_ids,
        contradicting_evidence_ids=candidate.contradicting_evidence_ids,
        missing_decisive_features=candidate.missing_decisive_features,
    )


def _finding_record(
    finding: ReviewFinding,
    *,
    finding_ref: str | None,
    raised_subject_id: str | None,
) -> ReviewFindingRecord:
    return ReviewFindingRecord(
        finding_ref=finding_ref,
        finding_id=finding.finding_id,
        origin=finding.origin.value,
        category=finding.category.value,
        severity=finding.severity.value,
        status=finding.status.value,
        reason_code=finding.reason_code.value if finding.reason_code is not None else None,
        summary=finding.summary,
        evidence_ids=finding.evidence_ids,
        raised_subject_id=raised_subject_id,
        subject_id=finding.subject_id,
        proposed_taxon=finding.proposed_taxon,
        required_action=finding.required_action.value,
        impact=finding.impact.value,
    )


class TraceRecorder:
    """Mutable during a run, immutable afterwards."""

    def __init__(self, case_id: str, *, root: Path | None = None) -> None:
        self._case_id = case_id
        self._code_commit_sha, self._code_dirty = _discover_code_revision(root or Path.cwd())
        self._events: list[NodeEvent] = []
        self._pending_calls: list[ProviderCallRecord] = []
        self._pending_review_projections: dict[str, ReviewerProjectionRecord] = {}
        self._providers: dict[str, str] = {}
        self._component_projections: tuple[ComponentProjection, ...] = ()
        self._decision_derivations: dict[str, DecisionDerivation] = {}
        self._prompt: PromptMetadata | None = None
        self._runtime_identity: RuntimeIdentity | None = None
        self._knowledge_coverage: KnowledgeCoverage | None = None
        self._evidence_observations: tuple[ObservationRecord, ...] = ()
        self._evidence_revision = 0
        self._evidence_history: list[EvidenceRevision] = []
        self._candidate_validation_history: list[CandidateValidationRecord] = []
        self._card_retrievals: list[CardRetrieval] = []
        self._routing_history: list[RoutingAssessment] = []
        self._evidence_context: EvidenceContextRecord | None = None
        self._plan: PlanRecord | None = None
        self._candidate_validations: dict[str, CandidateValidationRecord] = {}
        self._review_rounds: dict[tuple[str, int], ReviewRoundRecord] = {}
        self._abstentions: dict[str, AbstentionRecord] = {}
        self._retries = 0
        self._escalation_triggered = False
        self._user_claim_negated = False
        self._escalation_reasons: tuple[str, ...] = ()
        self._escalation_hard_reasons: tuple[str, ...] = ()
        self._escalation_suppressed_by: tuple[str, ...] = ()
        self._arbiter_used = False
        self._started_at = datetime.now(UTC)
        self._started_perf = time.perf_counter()

    def set_prompt_metadata(self, metadata: PromptMetadata) -> None:
        self._prompt = metadata

    def set_runtime_identity(self, identity: RuntimeIdentity) -> None:
        self._runtime_identity = identity

    def record_routing(self, plan: RoutingAssessment) -> None:
        """Keep one round's review plan, whether the graph follows it or only measures it."""
        self._routing_history.append(plan)

    def _model_called(self, stage: str, round_index: int, reviewer: Reviewer) -> bool:
        """Whether a recorded review came from a model call or from code alone."""
        if stage != "internal":
            return True
        return all(
            plan.calls(reviewer)
            for plan in self._routing_history
            if plan.round_index == round_index
        )

    def record_card_retrievals(self, records: tuple[CardRetrieval, ...]) -> None:
        self._card_retrievals.extend(
            record.model_copy(update={"evidence_revision": self._evidence_revision})
            for record in records
        )

    def set_provider(self, role: str, adapter: str, model: str | None) -> None:
        self._providers[role] = f"{adapter}:{model}" if model else adapter

    def record_provider_call(self, record: ProviderCallRecord) -> None:
        """Hold a model call until its own node is recorded.

        Claimed by node name rather than by arrival order, because the reviewer fan-out runs
        three nodes concurrently against one recorder. Draining every pending call into
        whichever node finished the bookkeeping first filed all three reviewers' calls under
        the first one and left the other two reading ``calls=0`` beside minutes of wall time.
        """
        self._pending_calls.append(record)

    def record_component_projections(self, evidence: EvidencePacket) -> None:
        """Record canonical identity mappings without prompts, image bytes or prose."""
        grouped: dict[tuple[str, str], list[str]] = {}
        for observation in evidence.observations:
            component_id = observation.source_component_id
            if component_id is None:
                continue
            key = (observation.subject_id, component_id)
            grouped.setdefault(key, []).append(observation.observation_id)
        self._component_projections = tuple(
            ComponentProjection(
                identity_subject_id=identity_id,
                source_component_id=component_id,
                observation_ids=tuple(observation_ids),
            )
            for (identity_id, component_id), observation_ids in grouped.items()
        )

    def record_evidence(
        self,
        evidence: EvidencePacket,
        *,
        generated: GeneratedEvidencePacket | None = None,
        retry_index: int = 0,
        attestations: tuple[AttestationOutcome, ...] = (),
    ) -> None:
        """Keep the evidence ledger: attribution, reading and trust per observation.

        Compact compatibility fields describe the final packet. Complete raw/effective
        artifacts retain every revision, including notes and regions, before another
        extraction reuses the IDs. The outcome of each uploader statement is kept with the
        revision it shaped. No image bytes, local paths or case metadata are copied.
        """
        from dendro_inspector.knowledge.evidence_hierarchy import (
            effective_tier,
            observation_trust,
        )

        self._evidence_observations = tuple(
            ObservationRecord(
                observation_id=observation.observation_id,
                subject_id=observation.subject_id,
                feature=observation.feature,
                value=observation.value,
                source=observation.source.value,
                image_id=observation.image_id,
                visibility=observation.visibility.value,
                reliability=observation.reliability.value,
                attachment=observation.attachment.value if observation.attachment else None,
                wood_surface=observation.wood_surface.value if observation.wood_surface else None,
                trust=observation_trust(observation).name.lower(),
                tier=int(effective_tier(observation)),
            )
            for observation in evidence.observations[:MAX_LEDGER_OBSERVATIONS]
        )
        self._evidence_revision += 1
        self._evidence_history.append(
            EvidenceRevision(
                revision=self._evidence_revision,
                retry_index=retry_index,
                raw=generated,
                effective=evidence,
                raw_sha256=packet_digest(generated) if generated is not None else None,
                effective_sha256=packet_digest(evidence),
                attestations=attestations,
            )
        )
        # Prior validations remain in history, but cannot describe the new packet's IDs.
        self._candidate_validations.clear()
        self._evidence_context = EvidenceContextRecord(
            revision=self._evidence_revision,
            subjects=tuple(
                SubjectRecord(
                    subject_id=subject.subject_id,
                    kind=subject.kind.value,
                    image_ids=subject.image_ids,
                )
                for subject in evidence.subjects[:MAX_LEDGER_SUBJECTS]
            ),
            inferences=tuple(
                InferenceRecord(
                    inference_id=inference.inference_id,
                    claim=inference.claim,
                    derived_from=inference.derived_from,
                    strength=inference.strength.value,
                    limitations=inference.limitations,
                )
                for inference in evidence.inferences[:MAX_LEDGER_OBSERVATIONS]
            ),
            absent_features=evidence.absent_features,
            context_limitations=evidence.context_limitations,
            image_limitations=tuple(
                ImageLimitationRecord(
                    image_id=limitation.image_id,
                    lighting=limitation.lighting,
                    white_balance=limitation.white_balance,
                    scale=limitation.scale.value,
                )
                for limitation in evidence.image_limitations
            ),
            possible_multiple_taxa=evidence.possible_multiple_taxa,
            instruction_like_content_detected=evidence.instruction_like_content_detected,
        )

    def record_plan(
        self,
        *,
        target_features: tuple[str, ...],
        expect_multiple_subjects: bool,
        bark_only_input: bool,
        split_firewood_input: bool,
    ) -> None:
        """Keep the plan's features and flags. Its notes are planner prose and stay out."""
        self._plan = PlanRecord(
            target_features=target_features,
            expect_multiple_subjects=expect_multiple_subjects,
            bark_only_input=bark_only_input,
            split_firewood_input=split_firewood_input,
        )

    def record_candidate_proposal(self, proposal: CandidateProposal) -> None:
        """Keep even rejected taxa, uncited support and proposals for unusable subjects."""
        if self._evidence_history:
            self._evidence_history[-1] = self._evidence_history[-1].model_copy(
                update={"candidate_proposal": proposal}
            )

    def record_review_round(
        self,
        *,
        stage: Literal["internal", "arbiter"],
        round_index: int,
        results: tuple[ReviewResult, ...],
        synthesis: ReviewSynthesis,
    ) -> None:
        """Keep one review pass: what each reviewer returned and what adjudication admitted.

        Called by the synthesizer that adjudicated the pass, so the record exists for a round
        a correction retry goes on to discard. Findings come from the synthesis' attributions
        and so carry their adjudicated status and reason. A synthesis built without
        attributions still records each review's findings as returned, with no status.
        """
        key = (stage, round_index)
        if key not in self._review_rounds and len(self._review_rounds) >= MAX_REVIEW_ROUNDS:
            get_logger("trace").warning(
                "review_round_not_recorded",
                extra={"case_id": self._case_id, "stage": stage, "round": round_index},
            )
            return

        def reference(finding: ReviewFinding) -> str | None:
            attribution = synthesis.attribution_of(finding)
            return attribution.reference(stage, round_index) if attribution is not None else None

        admitted_reviewers = {
            recommendation.reviewer
            for recommendation in synthesis.recommendations or ()
            if recommendation.reviewer is not None
        }
        reviewers = []
        for index, result in enumerate(results):
            if synthesis.attributions is None:
                findings = tuple(
                    _finding_record(finding, finding_ref=None, raised_subject_id=finding.subject_id)
                    for finding in result.findings
                )
            else:
                findings = tuple(
                    _finding_record(
                        attribution.finding,
                        finding_ref=attribution.reference(stage, round_index),
                        raised_subject_id=attribution.raised_subject_id,
                    )
                    for attribution in sorted(
                        (a for a in synthesis.attributions if a.result_index == index),
                        key=lambda a: a.position,
                    )
                )
            recommends = (
                result.recommended_resolution is not None
                or result.recommended_confidence is not None
            )
            ranked = sorted(result.recommended_candidates, key=lambda candidate: candidate.rank)
            reviewers.append(
                ReviewerRecord(
                    reviewer=result.reviewer.value,
                    status=result.status.value,
                    model_called=self._model_called(stage, round_index, result.reviewer),
                    subject_id=result.subject_id,
                    recommended_resolution=result.recommended_resolution,
                    recommended_confidence=result.recommended_confidence,
                    recommendation_admitted=(
                        result.reviewer in admitted_reviewers if recommends else None
                    ),
                    recommended_candidates=tuple(
                        _candidate_record(candidate) for candidate in ranked[:MAX_REVIEW_CANDIDATES]
                    ),
                    findings=findings[:MAX_REVIEW_FINDINGS],
                    findings_omitted=max(0, len(findings) - MAX_REVIEW_FINDINGS),
                )
            )

        self._review_rounds[key] = ReviewRoundRecord(
            stage=stage,
            round=round_index,
            evidence_revision=self._evidence_revision or None,
            reviewers=tuple(reviewers),
            recommendations=tuple(
                RecommendationRecord(
                    reviewer=(
                        recommendation.reviewer.value
                        if recommendation.reviewer is not None
                        else None
                    ),
                    subject_id=recommendation.subject_id,
                    resolution=recommendation.resolution,
                    confidence=recommendation.confidence,
                    finding_id=(
                        recommendation.finding.finding_id
                        if recommendation.finding is not None
                        else None
                    ),
                    finding_ref=(
                        reference(recommendation.finding)
                        if recommendation.finding is not None
                        else None
                    ),
                )
                for recommendation in synthesis.recommendations or ()
            ),
            reranks=tuple(
                RerankRecord(
                    reviewer=rerank.reviewer.value,
                    subject_id=rerank.candidate_set.subject_id,
                    finding_id=rerank.finding_id,
                    finding_ref=reference(rerank.finding),
                    ranking=tuple(candidate.taxon for candidate in rerank.candidate_set.ordered),
                )
                for rerank in synthesis.admitted_reranks
            ),
            retry_required=synthesis.retry_required,
            unresolvable=synthesis.unresolvable,
            unverified_abstention=synthesis.unverified_abstention,
            reviewer_disagreement=synthesis.reviewer_disagreement,
            escalation_recommended=synthesis.escalation_recommended,
        )

    def record_abstention(
        self,
        *,
        subject_id: str,
        resolution: Resolution,
        cause: AbstentionCause,
        findings: tuple[FindingReference, ...] = (),
    ) -> None:
        """Keep why one subject's claim was withdrawn. The latest record for a subject wins."""
        self._abstentions[subject_id] = AbstentionRecord(
            subject_id=subject_id,
            resolution=resolution,
            cause=cause,
            findings=findings,
        )

    def record_candidate_validation(
        self, proposed: CandidateSet, validation: CandidateValidationResult
    ) -> None:
        """Keep what validation admitted and refused for one subject, with reasons."""
        if (
            proposed.subject_id not in self._candidate_validations
            and len(self._candidate_validations) >= MAX_LEDGER_SUBJECTS
        ):
            return
        self._candidate_validations[proposed.subject_id] = CandidateValidationRecord(
            subject_id=proposed.subject_id,
            proposed_taxa=tuple(candidate.taxon for candidate in proposed.candidates),
            admitted=tuple(
                _candidate_record(candidate) for candidate in validation.candidate_set.candidates
            ),
            dropped_support=tuple(
                DroppedSupportRecord(
                    taxon=detail.taxon,
                    evidence_id=detail.evidence_id,
                    reason=detail.reason,
                    feature=detail.feature,
                    value=detail.value,
                    expected_values=detail.expected_values,
                )
                for detail in validation.dropped_support_details
            ),
            rejected_taxa=validation.rejected_taxa,
            demoted_scores=tuple(
                f"{taxon}:{proposed_strength.value}->{effective.value}"
                for taxon, proposed_strength, effective in validation.demoted_scores
            ),
            malformed_evidence_ids=validation.malformed_evidence_ids,
            evidence_revision=self._evidence_revision or None,
        )
        self._candidate_validation_history.append(self._candidate_validations[proposed.subject_id])

    def _observation_uses(self) -> tuple[ObservationUseRecord, ...]:
        """Account for every effective observation, including evidence no candidate cited."""
        uses = []
        for revision in self._evidence_history:
            validations = [
                record
                for record in self._candidate_validation_history
                if record.evidence_revision == revision.revision
            ]
            for observation in revision.effective.observations:
                evidence_ids = {observation.observation_id}
                evidence_ids.update(
                    inference.inference_id
                    for inference in revision.effective.inferences
                    if observation.observation_id in inference.derived_from
                )
                relevant = [v for v in validations if v.subject_id == observation.subject_id]
                support = tuple(
                    dict.fromkeys(
                        c.taxon
                        for v in relevant
                        for c in v.admitted
                        if evidence_ids.intersection(c.supporting_evidence_ids)
                    )
                )
                contradictions = tuple(
                    dict.fromkeys(
                        c.taxon
                        for v in relevant
                        for c in v.admitted
                        if evidence_ids.intersection(c.contradicting_evidence_ids)
                    )
                )
                rejected = tuple(
                    detail
                    for v in relevant
                    for detail in v.dropped_support
                    if detail.evidence_id in evidence_ids
                )
                proposal = revision.candidate_proposal
                cited = bool(
                    proposal
                    and any(
                        evidence_ids.intersection(candidate.supporting_evidence_ids)
                        or evidence_ids.intersection(candidate.contradicting_evidence_ids)
                        for group in proposal.sets
                        for candidate in group.candidates
                    )
                )
                uses.append(
                    ObservationUseRecord(
                        evidence_revision=revision.revision,
                        evidence_ref=revision.reference(observation.observation_id),
                        observation_id=observation.observation_id,
                        supporting_taxa=support,
                        contradicting_taxa=contradictions,
                        rejected_support=rejected,
                        candidate_validation_ran=bool(relevant),
                        cited=cited or bool(support or contradictions or rejected),
                    )
                )
        return tuple(uses)

    def record_review_projection(self, node: str, projection: ReviewProjection) -> None:
        """Record IDs and countable inputs without storing prompt text or private metadata."""
        self._pending_review_projections[node] = ReviewerProjectionRecord(
            reviewer=projection.reviewer.value,
            evidence_ids=projection.evidence_ids,
            image_ids=projection.image_ids,
            candidate_subject_ids=projection.candidate_subject_ids,
            taxon_ids=projection.taxon_ids,
            include_comparison_cards=projection.include_comparison_cards,
            include_regional_pack=projection.include_regional_pack,
        )

    def _claim_calls(self, node: str) -> tuple[ProviderCallRecord, ...]:
        """Take the pending calls this node made, leaving other nodes' calls alone."""
        claimed = tuple(record for record in self._pending_calls if record.node == node)
        if claimed:
            self._pending_calls = [record for record in self._pending_calls if record.node != node]
        return claimed

    def record_node(
        self,
        node: str,
        *,
        status: NodeStatus = NodeStatus.OK,
        detail: str | None = None,
        duration_ms: float | None = None,
    ) -> None:
        calls = self._claim_calls(node)
        projection = self._pending_review_projections.pop(node, None)
        self._events.append(
            NodeEvent(
                node=node,
                status=status,
                sequence=len(self._events),
                detail=detail,
                duration_ms=duration_ms,
                provider_calls=calls,
                reviewer_projection=projection,
            )
        )

    @property
    def retry_index(self) -> int:
        """The graph attempt currently being recorded (zero for the first pass)."""
        return self._retries

    def record_retry(self) -> None:
        self._retries += 1

    def record_escalation(
        self,
        *,
        triggered: bool,
        reasons: tuple[str, ...],
        hard_reasons: tuple[str, ...] = (),
        suppressed_by: tuple[str, ...] = (),
    ) -> None:
        self._escalation_triggered = triggered
        self._escalation_reasons = reasons
        self._escalation_hard_reasons = hard_reasons
        self._escalation_suppressed_by = suppressed_by

    def record_arbiter_used(self) -> None:
        self._arbiter_used = True

    def record_knowledge_coverage(self, coverage: KnowledgeCoverage) -> None:
        """Record what the cards could not represent, so a suite can attribute a failure.

        Recorded even when the coverage is complete. "No gap" and "nobody measured" are
        different facts, and only one of them can be aggregated.
        """
        self._knowledge_coverage = coverage

    def record_negated_claim(self) -> None:
        """The user named a taxon only to deny it, so no version was offered to rule on."""
        self._user_claim_negated = True

    def record_derivation(self, derivation: DecisionDerivation) -> None:
        """Keep the latest composition for a subject; final evaluation replaces probes."""
        self._decision_derivations[derivation.subject_id] = derivation

    @property
    def retries(self) -> int:
        return self._retries

    def build(
        self,
        *,
        final_resolution: Resolution | None = None,
        final_confidence: Confidence | None = None,
        pre_correction_decisions: tuple[FinalDecision, ...] = (),
        provisional_decisions: tuple[FinalDecision, ...] = (),
        final_decisions: tuple[FinalDecision, ...] = (),
        authority_checks: tuple[AuthorityCheckTrace, ...] = (),
        concurrent_nodes: tuple[str, ...] = (),
    ) -> RunTrace:
        finished_at = datetime.now(UTC)
        if self._pending_review_projections:
            # Same reasoning as unattributed provider calls below: a projection built for a
            # node that never recorded an event would leave the audit trail claiming the
            # reviewer saw the full state.
            get_logger("trace").warning(
                "unattributed_review_projections",
                extra={
                    "case_id": self._case_id,
                    "nodes": sorted(self._pending_review_projections),
                },
            )
            self._pending_review_projections.clear()
        if self._pending_calls:
            # A call whose node never recorded an event would vanish from the trace. That is
            # a wiring bug in the caller, and a silently short provider-call count is exactly
            # the kind of wrong-but-plausible audit trail this module exists to prevent.
            get_logger("trace").warning(
                "unattributed_provider_calls",
                extra={
                    "case_id": self._case_id,
                    "nodes": sorted({record.node for record in self._pending_calls}),
                    "count": len(self._pending_calls),
                },
            )
        correction_changes = {
            field: _decision_field_changed(
                pre_correction_decisions,
                final_decisions,
                field,
            )
            for field in _COMPARED_FIELDS
        }
        changed_values = tuple(value for value in correction_changes.values() if value is not None)
        arbiter_changes = {
            field: (
                _decision_field_changed(provisional_decisions, final_decisions, field)
                if self._arbiter_used
                else None
            )
            for field in _COMPARED_FIELDS
        }
        derivations = tuple(
            self._decision_derivations[decision.subject_id]
            for decision in final_decisions
            if decision.subject_id in self._decision_derivations
        )
        return RunTrace(
            case_id=self._case_id,
            graph_version=GRAPH_VERSION,
            code_commit_sha=self._code_commit_sha,
            code_dirty=self._code_dirty,
            domain_prompt=self._prompt,
            runtime_identity=self._runtime_identity,
            providers=dict(self._providers),
            events=tuple(self._events),
            component_projections=self._component_projections,
            evidence_observations=self._evidence_observations,
            candidate_validations=tuple(self._candidate_validations.values()),
            evidence_context=self._evidence_context,
            evidence_history=tuple(self._evidence_history),
            candidate_validation_history=tuple(self._candidate_validation_history),
            card_retrievals=tuple(self._card_retrievals),
            routing_history=tuple(self._routing_history),
            observation_uses=self._observation_uses(),
            plan=self._plan,
            review_rounds=tuple(self._review_rounds.values()),
            abstentions=tuple(self._abstentions.values()),
            knowledge_coverage=self._knowledge_coverage,
            retries=self._retries,
            graph_retry_count=self._retries,
            correction_changed_outcome=(any(changed_values) if changed_values else None),
            correction_changed_status=correction_changes["status"],
            correction_changed_taxon=correction_changes["selected_taxon"],
            correction_changed_resolution=correction_changes["resolution"],
            correction_changed_confidence=correction_changes["confidence"],
            correction_comparison=_compare_decisions(pre_correction_decisions, final_decisions),
            provisional_decisions=provisional_decisions,
            arbiter_changed_status=arbiter_changes["status"],
            arbiter_changed_taxon=arbiter_changes["selected_taxon"],
            arbiter_changed_resolution=arbiter_changes["resolution"],
            arbiter_changed_confidence=arbiter_changes["confidence"],
            arbiter_comparison=(
                _compare_decisions(provisional_decisions, final_decisions)
                if self._arbiter_used
                else None
            ),
            authority_checks=authority_checks,
            decision_derivations=derivations,
            evidence_authority_sensitive=any(
                check.status is AuthorityCheckStatus.SENSITIVE for check in authority_checks
            ),
            escalation_triggered=self._escalation_triggered,
            user_claim_negated=self._user_claim_negated,
            escalation_reasons=self._escalation_reasons,
            escalation_hard_reasons=self._escalation_hard_reasons,
            escalation_suppressed_by=self._escalation_suppressed_by,
            arbiter_used=self._arbiter_used,
            final_resolution=final_resolution,
            final_confidence=final_confidence,
            started_at=self._started_at,
            finished_at=finished_at,
            duration_ms=(time.perf_counter() - self._started_perf) * 1000.0,
            critical_path_ms=_critical_path_ms(tuple(self._events), frozenset(concurrent_nodes)),
        )


def _critical_path_ms(events: tuple[NodeEvent, ...], concurrent: frozenset[str]) -> float | None:
    """Serial node time plus the slowest member of each fan-out round.

    Rounds are runs of consecutive events from ``concurrent``, so a retry that fans out a
    second time is counted twice — it really did happen twice. The caller names the
    concurrent nodes because this module deliberately knows nothing about the graph; with
    no names supplied every node is serial, which is the honest reading of "not told".
    """
    if not events:
        return None
    total = 0.0
    round_max = 0.0
    in_round = False
    for event in events:
        duration = event.duration_ms or 0.0
        if event.node in concurrent:
            in_round = True
            round_max = max(round_max, duration)
            continue
        if in_round:
            total += round_max
            round_max = 0.0
            in_round = False
        total += duration
    return total + round_max if in_round else total


#: Build-time stamp of the commit an image was built from, read when git is unavailable.
CODE_COMMIT_ENV = "DENDRO_CODE_COMMIT_SHA"


def _discover_code_revision(root: Path) -> tuple[str | None, bool | None]:
    """Return the repository commit and dirty state without making Git a runtime dependency.

    Release versions identify the deterministic policy. The VCS revision distinguishes two
    experiment builds made between releases, while ``dirty`` prevents an uncommitted patch
    from borrowing the identity of its parent commit. Installed wheels and source archives
    legitimately have no ``.git`` directory, so inability to discover either value is
    represented explicitly rather than guessed.
    """

    def git(*arguments: str) -> subprocess.CompletedProcess[str] | None:
        try:
            return subprocess.run(
                ("git", "-C", str(root), *arguments),
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return None

    def valid(candidate: str) -> bool:
        return 40 <= len(candidate) <= 64 and all(c in "0123456789abcdef" for c in candidate)

    revision = git("rev-parse", "--verify", "HEAD")
    if revision is None or revision.returncode != 0:
        # A container image has no .git. The build stamps the commit it was built from
        # (DENDRO_CODE_COMMIT_SHA); whether that tree was clean is not known here, so
        # `dirty` stays unknown rather than being claimed.
        stamped = os.environ.get(CODE_COMMIT_ENV, "").strip().lower()
        return (stamped, None) if valid(stamped) else (None, None)
    commit_sha = revision.stdout.strip().lower()
    if not valid(commit_sha):
        return None, None

    status = git("status", "--porcelain=v1", "--untracked-files=normal")
    dirty = None if status is None or status.returncode != 0 else bool(status.stdout.strip())
    return commit_sha, dirty


def write_trace(trace: RunTrace, directory: Path) -> Path:
    """Write a trace as JSON and return the path."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{trace.case_id}.trace.json"
    path.write_text(
        json.dumps(trace.model_dump(mode="json"), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return path
