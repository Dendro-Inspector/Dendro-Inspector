"""Finding admissibility.

The rule under test: a finding is not accepted because a model produced it. It is accepted
when it meets an admissibility test, and rejected with a reason code otherwise.
"""

from __future__ import annotations

from evil_duck_dendro.nodes.review_synthesizer import adjudicate
from evil_duck_dendro.schemas.candidates import Candidate
from evil_duck_dendro.schemas.evidence import (
    EvidencePacket,
    Observation,
    ObservationSource,
    Subject,
)
from evil_duck_dendro.schemas.reviews import (
    FindingCategory,
    FindingStatus,
    ReasonCode,
    RequiredAction,
    Reviewer,
    ReviewFinding,
    ReviewResult,
    ReviewStatus,
    Severity,
)
from evil_duck_dendro.schemas.taxon import Confidence, Resolution

KNOWN_TAXA = frozenset({"pinus", "picea", "larix"})


def _evidence() -> EvidencePacket:
    return EvidencePacket(
        subjects=(Subject(subject_id="log_1"),),
        observations=(
            Observation(
                observation_id="obs-1",
                feature="bark.colour",
                value="reddish",
                subject_id="log_1",
                source=ObservationSource.IMAGE,
                image_id="img-1",
            ),
        ),
    )


def _finding(**changes) -> ReviewFinding:
    base = {
        "finding_id": "f1",
        "category": FindingCategory.COLOUR_OVERWEIGHTING,
        "severity": Severity.MAJOR,
        "summary": "Leans on colour.",
        "required_action": RequiredAction.LOWER_CONFIDENCE,
    }
    return ReviewFinding(**(base | changes))


def _result(*findings: ReviewFinding, **changes) -> ReviewResult:
    base = {
        "reviewer": Reviewer.CONFUSION,
        "status": ReviewStatus.PASS_WITH_FINDINGS,
        "findings": findings,
    }
    return ReviewResult(**(base | changes))


class TestAdmissibility:
    def test_finding_citing_real_evidence_is_accepted(self):
        synthesis = adjudicate(
            (_result(_finding(evidence_ids=("obs-1",))),),
            evidence=_evidence(),
            known_taxa=KNOWN_TAXA,
        )
        assert len(synthesis.accepted_findings) == 1
        accepted = synthesis.accepted_findings[0]
        assert accepted.status is FindingStatus.ACCEPTED
        assert accepted.reason_code is ReasonCode.REFERENCES_VISIBLE_EVIDENCE

    def test_finding_citing_a_nonexistent_id_is_rejected(self):
        """A model that invents an evidence id does not get to change the answer."""
        synthesis = adjudicate(
            (_result(_finding(evidence_ids=("obs-does-not-exist",))),),
            evidence=_evidence(),
            known_taxa=KNOWN_TAXA,
        )
        assert not synthesis.accepted_findings
        assert synthesis.rejected_findings[0].reason_code is ReasonCode.EVIDENCE_ID_UNKNOWN

    def test_duplicate_findings_are_rejected_as_restatement(self):
        synthesis = adjudicate(
            (
                _result(_finding(finding_id="f1", evidence_ids=("obs-1",))),
                _result(
                    _finding(finding_id="f2", evidence_ids=("obs-1",)), reviewer=Reviewer.CONFIDENCE
                ),
            ),
            evidence=_evidence(),
            known_taxa=KNOWN_TAXA,
        )
        assert len(synthesis.accepted_findings) == 1
        assert synthesis.rejected_findings[0].reason_code is ReasonCode.RESTATES_EXISTING_FINDING

    def test_overlooked_alternative_needs_a_known_taxon_to_be_actionable(self):
        without = adjudicate(
            (
                _result(
                    _finding(
                        category=FindingCategory.OVERLOOKED_ALTERNATIVE,
                        required_action=RequiredAction.RERANK_CANDIDATES,
                    )
                ),
            ),
            evidence=_evidence(),
            known_taxa=KNOWN_TAXA,
        )
        assert without.rejected_findings[0].reason_code is ReasonCode.NOT_ACTIONABLE

        with_candidate = adjudicate(
            (
                _result(
                    _finding(
                        category=FindingCategory.OVERLOOKED_ALTERNATIVE,
                        required_action=RequiredAction.RERANK_CANDIDATES,
                    ),
                    recommended_candidates=(
                        Candidate(taxon="picea", resolution=Resolution.GENUS, rank=1),
                    ),
                ),
            ),
            evidence=_evidence(),
            known_taxa=KNOWN_TAXA,
        )
        assert (
            with_candidate.accepted_findings[0].reason_code
            is ReasonCode.PLAUSIBLE_OMITTED_ALTERNATIVE
        )

    def test_rejections_are_recorded_not_discarded(self):
        """The audit trail: 'the reviewer said X, we did not act on it, because Y'."""
        synthesis = adjudicate(
            (_result(_finding(evidence_ids=("nope",))),),
            evidence=_evidence(),
            known_taxa=KNOWN_TAXA,
        )
        assert len(synthesis.rejected_findings) == 1
        assert synthesis.rejected_findings[0].status is FindingStatus.REJECTED
        assert synthesis.rejected_findings[0].reason_code is not None


class TestDerivedActions:
    def test_re_extract_request_sets_retry_required(self):
        synthesis = adjudicate(
            (
                _result(
                    _finding(
                        category=FindingCategory.INVALID_NEGATIVE_EVIDENCE,
                        required_action=RequiredAction.RE_EXTRACT_EVIDENCE,
                    )
                ),
            ),
            evidence=_evidence(),
            known_taxa=KNOWN_TAXA,
        )
        assert synthesis.retry_required
        assert not synthesis.unresolvable

    def test_critical_abstain_request_is_unresolvable(self):
        synthesis = adjudicate(
            (
                _result(
                    _finding(
                        category=FindingCategory.UNSUPPORTED_CLAIM,
                        severity=Severity.CRITICAL,
                        required_action=RequiredAction.ABSTAIN,
                    )
                ),
            ),
            evidence=_evidence(),
            known_taxa=KNOWN_TAXA,
        )
        assert synthesis.unresolvable

    def test_reviewer_disagreement_recommends_escalation(self):
        synthesis = adjudicate(
            (
                _result(recommended_resolution=Resolution.SPECIES, findings=()),
                _result(
                    reviewer=Reviewer.CONFIDENCE,
                    recommended_resolution=Resolution.GENUS,
                    findings=(),
                ),
            ),
            evidence=_evidence(),
            known_taxa=KNOWN_TAXA,
        )
        assert synthesis.escalation_recommended

    def test_deltas_take_the_most_conservative_recommendation(self):
        synthesis = adjudicate(
            (
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
            ),
            evidence=_evidence(),
            known_taxa=KNOWN_TAXA,
        )
        assert synthesis.confidence_delta is Confidence.LOW
        assert synthesis.resolution_delta is Resolution.GENUS
