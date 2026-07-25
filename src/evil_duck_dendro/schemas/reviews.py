"""Review contracts.

Reviewers return findings, never prose verdicts. The synthesizer accepts or rejects each
finding against fixed admissibility rules — a finding is never accepted merely because a
model produced it, and a rejection always carries a reason code.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from evil_duck_dendro.schemas.base import Contract, Identifier, ShortText, ValueToken
from evil_duck_dendro.schemas.candidates import Candidate
from evil_duck_dendro.schemas.taxon import Confidence, Resolution


class Reviewer(StrEnum):
    BOTANICAL = "botanical"
    CONFUSION = "confusion"
    CONFIDENCE = "confidence"
    ARBITER = "arbiter"


class FindingCategory(StrEnum):
    BOTANICAL_CONTRADICTION = "botanical_contradiction"
    OVERLOOKED_ALTERNATIVE = "overlooked_alternative"
    COLOUR_OVERWEIGHTING = "colour_overweighting"
    UNSUPPORTED_CLAIM = "unsupported_claim"
    CONFIDENCE_MISCALIBRATION = "confidence_miscalibration"
    RESOLUTION_TOO_SPECIFIC = "resolution_too_specific"
    INVALID_NEGATIVE_EVIDENCE = "invalid_negative_evidence"
    SUBJECT_CONTAMINATION = "subject_contamination"
    REGION_ASSUMPTION = "region_assumption"
    MISSING_DECISIVE_FEATURE = "missing_decisive_feature"
    CONTRACT_VIOLATION = "contract_violation"


class Severity(StrEnum):
    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"


class FindingStatus(StrEnum):
    OPEN = "open"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class Impact(StrEnum):
    CANDIDATE_CHANGE = "candidate_change"
    CONFIDENCE_CHANGE = "confidence_change"
    RESOLUTION_CHANGE = "resolution_change"
    OBSERVATION_CHANGE = "observation_change"
    NO_MATERIAL_CHANGE = "no_material_change"


class ReasonCode(StrEnum):
    """Why a finding was accepted or rejected. Closed vocabulary, so it is countable."""

    REFERENCES_VISIBLE_EVIDENCE = "references_visible_evidence"
    IDENTIFIES_CONTRACT_VIOLATION = "identifies_contract_violation"
    IDENTIFIES_CONTRADICTION = "identifies_contradiction"
    IMPROVES_CALIBRATION = "improves_calibration"
    PLAUSIBLE_OMITTED_ALTERNATIVE = "plausible_omitted_alternative"
    NO_EVIDENCE_REFERENCE = "no_evidence_reference"
    EVIDENCE_ID_UNKNOWN = "evidence_id_unknown"
    RESTATES_EXISTING_FINDING = "restates_existing_finding"
    NOT_ACTIONABLE = "not_actionable"
    OUT_OF_SCOPE = "out_of_scope"


class ReviewStatus(StrEnum):
    PASS = "pass"
    PASS_WITH_FINDINGS = "pass_with_findings"
    FAIL_CORRECTABLE = "fail_correctable"
    FAIL_UNRESOLVABLE = "fail_unresolvable"


class RequiredAction(StrEnum):
    NONE = "none"
    RE_EXTRACT_EVIDENCE = "re_extract_evidence"
    LOWER_CONFIDENCE = "lower_confidence"
    LOWER_RESOLUTION = "lower_resolution"
    RERANK_CANDIDATES = "rerank_candidates"
    REQUEST_ADDITIONAL_PHOTO = "request_additional_photo"
    ABSTAIN = "abstain"


class ReviewFinding(Contract):
    """One defect claim about the current result."""

    finding_id: Identifier
    category: FindingCategory
    severity: Severity
    status: FindingStatus = FindingStatus.OPEN
    reason_code: ReasonCode | None = None
    summary: ShortText
    evidence_ids: tuple[Identifier, ...] = ()
    subject_id: Identifier | None = None
    proposed_taxon: Identifier | None = Field(
        default=None,
        description=(
            "For `overlooked_alternative`: which taxon is being proposed. A structured "
            "field rather than prose, so admissibility can check it against the cards — "
            "an alternative nobody can name is not an alternative."
        ),
    )
    required_action: RequiredAction = RequiredAction.NONE
    impact: Impact = Impact.NO_MATERIAL_CHANGE


class ReviewResult(Contract):
    """One reviewer's structured output."""

    reviewer: Reviewer
    status: ReviewStatus
    findings: tuple[ReviewFinding, ...] = ()
    recommended_candidates: tuple[Candidate, ...] = ()
    recommended_resolution: Resolution | None = None
    recommended_confidence: Confidence | None = None
    subject_id: Identifier | None = None


class CorrectionDirective(Contract):
    """A machine-actionable instruction produced by review synthesis."""

    action: RequiredAction
    subject_id: Identifier | None = None
    target_feature: ValueToken | None = None
    rationale: ShortText


class ReviewSynthesis(Contract):
    """The adjudicated result of all reviewer findings for one pass."""

    accepted_findings: tuple[ReviewFinding, ...] = ()
    rejected_findings: tuple[ReviewFinding, ...] = ()
    required_corrections: tuple[CorrectionDirective, ...] = ()
    candidate_delta: tuple[ShortText, ...] = ()
    confidence_delta: Confidence | None = None
    resolution_delta: Resolution | None = None
    retry_required: bool = False
    escalation_recommended: bool = False
    unresolvable: bool = Field(
        default=False,
        description="A critical finding that a retry cannot fix. Routes to abstain.",
    )

    @property
    def has_critical(self) -> bool:
        return any(finding.severity is Severity.CRITICAL for finding in self.accepted_findings)
