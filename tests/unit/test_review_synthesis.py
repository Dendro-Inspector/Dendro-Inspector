"""Deterministic finding admission and exact finding-bound reranks."""

from __future__ import annotations

import asyncio

from dendro_inspector.graph.state import GraphState
from dendro_inspector.nodes._support import mark_model_findings, merge_findings
from dendro_inspector.nodes.arbiter_synthesizer import run as run_arbiter_synthesizer
from dendro_inspector.nodes.review_synthesizer import adjudicate
from dendro_inspector.schemas.candidates import Candidate
from dendro_inspector.schemas.evidence import (
    AttachmentStatus,
    EvidencePacket,
    Inference,
    Observation,
    ObservationSource,
    Subject,
)
from dendro_inspector.schemas.input import CaseInput
from dendro_inspector.schemas.reviews import (
    FindingCategory,
    FindingOrigin,
    FindingStatus,
    Impact,
    ReasonCode,
    RequiredAction,
    Reviewer,
    ReviewFinding,
    ReviewResult,
    ReviewStatus,
    Severity,
)
from dendro_inspector.schemas.taxon import Confidence, Resolution


def _observation(
    observation_id: str,
    feature: str,
    value: str,
    *,
    subject_id: str = "log_1",
) -> Observation:
    detachable = feature.split(".", 1)[0] in {
        "leaf",
        "needles",
        "fruit",
        "cones",
        "branch",
        "bud",
        "seed",
        "nut",
        "acorn",
        "samara",
    }
    return Observation(
        observation_id=observation_id,
        feature=feature,
        value=value,
        subject_id=subject_id,
        source=ObservationSource.IMAGE,
        image_id="img-1",
        attachment=AttachmentStatus.CONFIRMED_ATTACHED if detachable else None,
    )


def _evidence() -> EvidencePacket:
    return EvidencePacket(
        subjects=(Subject(subject_id="log_1"),),
        observations=(_observation("obs-1", "bark.colour", "reddish"),),
    )


def _rerank_evidence() -> EvidencePacket:
    return EvidencePacket(
        subjects=(Subject(subject_id="log_1"),),
        observations=(
            _observation("pinus-support", "needles.fascicles", "two"),
            _observation("picea-support", "needles.attachment", "single_on_woody_peg"),
        ),
    )


def _finding(**changes) -> ReviewFinding:
    base = {
        "finding_id": "f1",
        "category": FindingCategory.COLOUR_OVERWEIGHTING,
        "severity": Severity.MAJOR,
        "summary": "Leans on colour.",
        "required_action": RequiredAction.LOWER_CONFIDENCE,
        "impact": Impact.CONFIDENCE_CHANGE,
    }
    return ReviewFinding(**(base | changes))


def _result(*findings: ReviewFinding, **changes) -> ReviewResult:
    base = {
        "reviewer": Reviewer.CONFUSION,
        "status": ReviewStatus.PASS_WITH_FINDINGS,
        "findings": findings,
    }
    return ReviewResult(**(base | changes))


def _candidate(taxon: str, rank: int, support_id: str) -> Candidate:
    return Candidate(
        taxon=taxon,
        resolution=Resolution.GENUS,
        supporting_evidence_ids=(support_id,),
        rank=rank,
    )


def _bound(result: ReviewResult, packet: EvidencePacket) -> ReviewResult:
    """Bind the evidence scope the way `review_call` does: the projection is the whole packet.

    Deriving the scope from what the findings happen to cite would put an invented id inside
    the projection, so a test for `evidence_id_unknown` would pass without ever reaching that
    branch. Tests that want a narrower scope set `reviewed_evidence_ids` themselves.
    """
    if result.reviewed_evidence_ids is not None:
        return result
    packet_ids = tuple(observation.observation_id for observation in packet.observations) + tuple(
        inference.inference_id for inference in packet.inferences
    )
    return result.model_copy(update={"reviewed_evidence_ids": packet_ids})


def _adjudicate(knowledge, *results: ReviewResult, evidence=None):
    packet = evidence if evidence is not None else _evidence()
    return adjudicate(
        tuple(_bound(result, packet) for result in results),
        evidence=packet,
        knowledge=knowledge,
    )


class TestAdmissibility:
    def test_finding_citing_real_evidence_is_accepted(self, knowledge):
        synthesis = _adjudicate(
            knowledge,
            _result(_finding(evidence_ids=("obs-1",))),
        )
        assert len(synthesis.accepted_findings) == 1
        accepted = synthesis.accepted_findings[0]
        assert accepted.status is FindingStatus.ACCEPTED
        assert accepted.reason_code is ReasonCode.REFERENCES_VISIBLE_EVIDENCE
        assert accepted.subject_id == "log_1"

    def test_finding_citing_a_nonexistent_id_is_rejected(self, knowledge):
        synthesis = _adjudicate(
            knowledge,
            _result(_finding(evidence_ids=("obs-does-not-exist",))),
        )
        assert not synthesis.accepted_findings
        assert synthesis.rejected_findings[0].reason_code is ReasonCode.EVIDENCE_ID_UNKNOWN

    def test_finding_cannot_cite_evidence_outside_recorded_projection(self, knowledge):
        synthesis = _adjudicate(
            knowledge,
            _result(
                _finding(evidence_ids=("obs-1",)),
                reviewed_evidence_ids=(),
            ),
        )

        assert not synthesis.accepted_findings
        assert synthesis.rejected_findings[0].reason_code is ReasonCode.OUT_OF_SCOPE

    def test_material_duplicate_ignores_id_prose_and_model_severity(self, knowledge):
        synthesis = _adjudicate(
            knowledge,
            _result(_finding(finding_id="f1", evidence_ids=("obs-1",))),
            _result(
                _finding(
                    finding_id="f2",
                    evidence_ids=("obs-1",),
                    summary="Different wording for the same defect.",
                    severity=Severity.MINOR,
                ),
                reviewer=Reviewer.CONFIDENCE,
            ),
        )
        assert [finding.finding_id for finding in synthesis.accepted_findings] == ["f1"]
        assert synthesis.rejected_findings[0].finding_id == "f2"
        assert synthesis.rejected_findings[0].reason_code is ReasonCode.RESTATES_EXISTING_FINDING

    def test_same_category_and_subject_with_material_difference_is_not_suppressed(self, knowledge):
        synthesis = _adjudicate(
            knowledge,
            _result(
                _finding(finding_id="f1", evidence_ids=("obs-1",)),
                _finding(
                    finding_id="f2",
                    required_action=RequiredAction.REQUEST_ADDITIONAL_PHOTO,
                    impact=Impact.NO_MATERIAL_CHANGE,
                ),
            ),
        )
        assert [finding.finding_id for finding in synthesis.accepted_findings] == ["f1", "f2"]

    def test_foreign_subject_evidence_is_rejected(self, knowledge):
        evidence = EvidencePacket(
            subjects=(Subject(subject_id="log_1"), Subject(subject_id="log_2")),
            observations=(_observation("foreign", "bark.colour", "grey", subject_id="log_2"),),
        )
        synthesis = _adjudicate(
            knowledge,
            _result(
                _finding(subject_id="log_1", evidence_ids=("foreign",)),
                subject_id="log_1",
            ),
            evidence=evidence,
        )
        assert not synthesis.accepted_findings
        assert synthesis.rejected_findings[0].reason_code is ReasonCode.OUT_OF_SCOPE

    def test_cross_subject_inference_evidence_is_rejected(self, knowledge):
        evidence = EvidencePacket(
            subjects=(Subject(subject_id="log_1"), Subject(subject_id="log_2")),
            observations=(
                _observation("own", "bark.colour", "grey", subject_id="log_1"),
                _observation("foreign", "bark.colour", "brown", subject_id="log_2"),
            ),
            inferences=(
                Inference(
                    inference_id="mixed-inference",
                    claim="mixed_support",
                    derived_from=("own", "foreign"),
                ),
            ),
        )
        synthesis = _adjudicate(
            knowledge,
            _result(
                _finding(subject_id="log_1", evidence_ids=("mixed-inference",)),
                subject_id="log_1",
            ),
            evidence=evidence,
        )
        assert synthesis.rejected_findings[0].reason_code is ReasonCode.OUT_OF_SCOPE

    def test_conflicting_finding_and_result_subjects_are_rejected(self, knowledge):
        synthesis = _adjudicate(
            knowledge,
            _result(_finding(subject_id="log_1"), subject_id="log_2"),
        )
        assert synthesis.rejected_findings[0].reason_code is ReasonCode.OUT_OF_SCOPE

    def test_rejections_are_recorded_not_discarded(self, knowledge):
        synthesis = _adjudicate(
            knowledge,
            _result(_finding(evidence_ids=("nope",))),
        )
        assert len(synthesis.rejected_findings) == 1
        assert synthesis.rejected_findings[0].status is FindingStatus.REJECTED
        assert synthesis.rejected_findings[0].reason_code is not None


class TestDeterministicPrecedence:
    def test_provider_finding_cannot_claim_deterministic_origin(self):
        spoofed = _result(_finding(finding_id="spoofed", origin=FindingOrigin.DETERMINISTIC))
        bounded = mark_model_findings(spoofed)
        assert bounded.findings[0].origin is FindingOrigin.MODEL

    def test_merge_prepends_deterministic_findings_without_preemption(self):
        model = _finding(
            finding_id="model-colour",
            evidence_ids=("obs-1",),
            summary="Model wording.",
        )
        deterministic = _finding(
            finding_id="auto-colour",
            evidence_ids=("obs-1",),
            origin=FindingOrigin.DETERMINISTIC,
            summary="Deterministic wording.",
        )

        merged = merge_findings(_result(model), (deterministic,))

        assert [finding.finding_id for finding in merged.findings] == [
            "auto-colour",
            "model-colour",
        ]
        assert merged.findings[0].origin is FindingOrigin.DETERMINISTIC

    def test_deterministic_duplicate_is_adjudicated_before_model_duplicate(self, knowledge):
        model = _finding(
            finding_id="model-colour",
            evidence_ids=("obs-1",),
            summary="Model wording.",
        )
        deterministic = _finding(
            finding_id="auto-colour",
            evidence_ids=("obs-1",),
            origin=FindingOrigin.DETERMINISTIC,
            summary="Deterministic wording.",
        )
        synthesis = _adjudicate(
            knowledge,
            merge_findings(_result(model), (deterministic,)),
        )

        assert synthesis.accepted_findings[0].finding_id == "auto-colour"
        assert synthesis.accepted_findings[0].origin is FindingOrigin.DETERMINISTIC
        assert synthesis.rejected_findings[0].finding_id == "model-colour"
        assert synthesis.rejected_findings[0].reason_code is ReasonCode.RESTATES_EXISTING_FINDING


class TestFindingBoundReranks:
    def test_valid_recommendation_is_bound_to_exact_admitted_finding(self, knowledge):
        finding = _finding(
            finding_id="rerank-picea",
            category=FindingCategory.BOTANICAL_CONTRADICTION,
            subject_id="log_1",
            evidence_ids=("picea-support",),
            proposed_taxon="picea",
            required_action=RequiredAction.RERANK_CANDIDATES,
            impact=Impact.CANDIDATE_CHANGE,
        )
        result = _result(
            finding,
            subject_id="log_1",
            recommended_candidates=(
                _candidate("picea", 1, "picea-support"),
                _candidate("pinus", 2, "pinus-support"),
            ),
        )

        synthesis = _adjudicate(knowledge, result, evidence=_rerank_evidence())

        assert [finding.finding_id for finding in synthesis.accepted_findings] == ["rerank-picea"]
        assert len(synthesis.admitted_reranks) == 1
        rerank = synthesis.admitted_reranks[0]
        assert rerank.finding_id == "rerank-picea"
        assert rerank.finding == synthesis.accepted_findings[0]
        assert rerank.reviewer is Reviewer.CONFUSION
        assert [candidate.taxon for candidate in rerank.candidate_set.ordered] == [
            "picea",
            "pinus",
        ]

    def test_recommendation_cannot_use_evidence_outside_recorded_projection(self, knowledge):
        finding = _finding(
            finding_id="out-of-scope-rerank",
            category=FindingCategory.BOTANICAL_CONTRADICTION,
            subject_id="log_1",
            evidence_ids=("picea-support",),
            proposed_taxon="picea",
            required_action=RequiredAction.RERANK_CANDIDATES,
            impact=Impact.CANDIDATE_CHANGE,
        )
        result = _result(
            finding,
            subject_id="log_1",
            reviewed_evidence_ids=("picea-support",),
            recommended_candidates=(_candidate("picea", 1, "pinus-support"),),
        )

        synthesis = _adjudicate(knowledge, result, evidence=_rerank_evidence())

        assert not synthesis.admitted_reranks
        assert synthesis.rejected_findings[0].reason_code is ReasonCode.NOT_ACTIONABLE

    def test_recommendation_without_rerank_finding_is_inert(self, knowledge):
        result = _result(
            _finding(required_action=RequiredAction.LOWER_CONFIDENCE),
            subject_id="log_1",
            recommended_candidates=(_candidate("picea", 1, "picea-support"),),
        )
        synthesis = _adjudicate(knowledge, result, evidence=_rerank_evidence())
        assert not synthesis.admitted_reranks

    def test_finding_cannot_borrow_recommendation_from_another_result(self, knowledge):
        finding_result = _result(
            _finding(
                finding_id="unbound",
                subject_id="log_1",
                required_action=RequiredAction.RERANK_CANDIDATES,
                impact=Impact.CANDIDATE_CHANGE,
            ),
            subject_id="log_1",
        )
        recommendation_result = _result(
            subject_id="log_1",
            findings=(),
            recommended_candidates=(_candidate("picea", 1, "picea-support"),),
        )

        synthesis = _adjudicate(
            knowledge,
            finding_result,
            recommendation_result,
            evidence=_rerank_evidence(),
        )

        assert not synthesis.admitted_reranks
        assert synthesis.rejected_findings[0].reason_code is ReasonCode.NOT_ACTIONABLE

    def test_unsupported_recommendation_is_rejected(self, knowledge):
        result = _result(
            _finding(
                finding_id="unsupported-rerank",
                subject_id="log_1",
                required_action=RequiredAction.RERANK_CANDIDATES,
                impact=Impact.CANDIDATE_CHANGE,
            ),
            subject_id="log_1",
            recommended_candidates=(_candidate("picea", 1, "pinus-support"),),
        )

        synthesis = _adjudicate(knowledge, result, evidence=_rerank_evidence())

        assert not synthesis.admitted_reranks
        assert synthesis.rejected_findings[0].reason_code is ReasonCode.NOT_ACTIONABLE

    def test_proposed_taxon_must_survive_recommendation_validation(self, knowledge):
        result = _result(
            _finding(
                finding_id="missing-proposed",
                category=FindingCategory.OVERLOOKED_ALTERNATIVE,
                subject_id="log_1",
                proposed_taxon="picea",
                required_action=RequiredAction.RERANK_CANDIDATES,
                impact=Impact.CANDIDATE_CHANGE,
            ),
            subject_id="log_1",
            recommended_candidates=(_candidate("pinus", 1, "pinus-support"),),
        )

        synthesis = _adjudicate(knowledge, result, evidence=_rerank_evidence())

        assert not synthesis.admitted_reranks
        assert synthesis.rejected_findings[0].reason_code is ReasonCode.NOT_ACTIONABLE

    def test_conflicting_valid_reranks_recommend_escalation(self, knowledge):
        picea_first = _result(
            _finding(
                finding_id="picea-first",
                category=FindingCategory.BOTANICAL_CONTRADICTION,
                subject_id="log_1",
                evidence_ids=("picea-support",),
                required_action=RequiredAction.RERANK_CANDIDATES,
                impact=Impact.CANDIDATE_CHANGE,
            ),
            subject_id="log_1",
            recommended_candidates=(
                _candidate("picea", 1, "picea-support"),
                _candidate("pinus", 2, "pinus-support"),
            ),
        )
        pinus_first = _result(
            _finding(
                finding_id="pinus-first",
                category=FindingCategory.OVERLOOKED_ALTERNATIVE,
                subject_id="log_1",
                evidence_ids=("pinus-support",),
                proposed_taxon="pinus",
                required_action=RequiredAction.RERANK_CANDIDATES,
                impact=Impact.CANDIDATE_CHANGE,
            ),
            reviewer=Reviewer.CONFIDENCE,
            subject_id="log_1",
            recommended_candidates=(
                _candidate("pinus", 1, "pinus-support"),
                _candidate("picea", 2, "picea-support"),
            ),
        )

        synthesis = _adjudicate(
            knowledge,
            picea_first,
            pinus_first,
            evidence=_rerank_evidence(),
        )

        assert len(synthesis.admitted_reranks) == 2
        assert synthesis.escalation_recommended


class TestArbiterSynthesis:
    def test_arbiter_uses_same_validation_and_records_bound_rerank(self, node_context):
        finding = _finding(
            finding_id="arbiter-rerank",
            category=FindingCategory.OVERLOOKED_ALTERNATIVE,
            subject_id="log_1",
            evidence_ids=("picea-support",),
            proposed_taxon="picea",
            required_action=RequiredAction.RERANK_CANDIDATES,
            impact=Impact.CANDIDATE_CHANGE,
        )
        review = _result(
            finding,
            reviewer=Reviewer.ARBITER,
            subject_id="log_1",
            recommended_candidates=(
                _candidate("picea", 1, "picea-support"),
                _candidate("pinus", 2, "pinus-support"),
            ),
        )
        state = GraphState(
            case=CaseInput(case_id="arbiter-case"),
            evidence=_rerank_evidence(),
            arbiter_reviews=(_bound(review, _rerank_evidence()),),
        )

        updated = asyncio.run(run_arbiter_synthesizer(state, node_context))

        assert updated.arbiter_synthesis is not None
        assert len(updated.arbiter_synthesis.admitted_reranks) == 1
        assert updated.arbiter_synthesis.admitted_reranks[0].reviewer is Reviewer.ARBITER


class TestDerivedActions:
    def test_re_extract_request_sets_retry_required(self, knowledge):
        synthesis = _adjudicate(
            knowledge,
            _result(
                _finding(
                    category=FindingCategory.INVALID_NEGATIVE_EVIDENCE,
                    required_action=RequiredAction.RE_EXTRACT_EVIDENCE,
                )
            ),
        )
        assert synthesis.retry_required
        assert not synthesis.unresolvable

    def test_critical_abstain_request_is_unresolvable(self, knowledge):
        synthesis = _adjudicate(
            knowledge,
            _result(
                _finding(
                    category=FindingCategory.UNSUPPORTED_CLAIM,
                    severity=Severity.CRITICAL,
                    required_action=RequiredAction.ABSTAIN,
                )
            ),
        )
        assert synthesis.unresolvable

    def test_reviewer_disagreement_recommends_escalation(self, knowledge):
        synthesis = _adjudicate(
            knowledge,
            _result(recommended_resolution=Resolution.SPECIES, findings=()),
            _result(
                reviewer=Reviewer.CONFIDENCE,
                recommended_resolution=Resolution.GENUS,
                findings=(),
            ),
        )
        assert synthesis.escalation_recommended
        assert synthesis.reviewer_disagreement
        assert not synthesis.has_critical

    def test_critical_finding_records_distinct_escalation_provenance(self, knowledge):
        synthesis = _adjudicate(
            knowledge,
            _result(
                _finding(
                    category=FindingCategory.UNSUPPORTED_CLAIM,
                    severity=Severity.CRITICAL,
                    required_action=RequiredAction.ABSTAIN,
                )
            ),
        )

        assert synthesis.escalation_recommended
        assert not synthesis.reviewer_disagreement
        assert synthesis.has_critical

    def test_deltas_take_the_most_conservative_recommendation(self, knowledge):
        synthesis = _adjudicate(
            knowledge,
            _result(
                recommended_confidence=Confidence.HIGH,
                recommended_resolution=Resolution.SPECIES,
                findings=(),
            ),
            _result(
                reviewer=Reviewer.CONFIDENCE,
                recommended_confidence=Confidence.LOW,
                recommended_resolution=Resolution.GENUS,
                findings=(),
            ),
        )
        assert synthesis.confidence_delta is Confidence.LOW
        assert synthesis.resolution_delta is Resolution.GENUS
