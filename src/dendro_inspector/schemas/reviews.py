"""Review contracts.

Reviewers return findings, never prose verdicts. The synthesizer accepts or rejects each
finding against fixed admissibility rules — a finding is never accepted merely because a
model produced it, and a rejection always carries a reason code.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from dendro_inspector.schemas.base import (
    IDENTIFIER_RULE,
    Contract,
    Identifier,
    ShortText,
    ValueToken,
)
from dendro_inspector.schemas.candidates import Candidate, CandidateSet
from dendro_inspector.schemas.taxon import Confidence, Resolution


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


class FindingOrigin(StrEnum):
    MODEL = "model"
    DETERMINISTIC = "deterministic"


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

    finding_id: Identifier = Field(description=IDENTIFIER_RULE)
    category: FindingCategory
    severity: Severity
    status: FindingStatus = FindingStatus.OPEN
    origin: FindingOrigin = FindingOrigin.MODEL
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
    reviewed_evidence_ids: tuple[Identifier, ...] | None = Field(
        default=None,
        description=(
            "Code-owned evidence scope bound at the reviewer model boundary. Provider "
            "values are ignored; model findings may cite only ids in this tuple."
        ),
    )


class CorrectionDirective(Contract):
    """A machine-actionable instruction produced by review synthesis."""

    action: RequiredAction
    subject_id: Identifier | None = None
    target_feature: ValueToken | None = None
    rationale: ShortText


class AdmittedRerank(Contract):
    """One validated ranking bound to the exact finding that admitted it."""

    finding: ReviewFinding
    reviewer: Reviewer
    candidate_set: CandidateSet

    @model_validator(mode="after")
    def _finding_and_ranking_are_bound(self) -> AdmittedRerank:
        if self.finding.status is not FindingStatus.ACCEPTED:
            msg = "admitted rerank finding must have status=accepted"
            raise ValueError(msg)
        if self.finding.required_action is not RequiredAction.RERANK_CANDIDATES:
            msg = "admitted rerank finding must require rerank_candidates"
            raise ValueError(msg)
        if self.finding.subject_id != self.candidate_set.subject_id:
            msg = "admitted rerank finding and candidate set must name the same subject"
            raise ValueError(msg)
        return self

    @property
    def finding_id(self) -> str:
        return self.finding.finding_id


class AdmittedRecommendation(Contract):
    """A review bound for one subject, optionally tied to its own accepted finding.

    A bare recommendation can cap a claim but cannot waive any finding. Embedding the
    exact accepted finding follows the same binding contract as ``AdmittedRerank``;
    equal ids alone are insufficient because separate reviewers may reuse an id.
    """

    reviewer: Reviewer | None = None
    subject_id: Identifier
    confidence: Confidence | None = None
    resolution: Resolution | None = None
    finding: ReviewFinding | None = None

    @model_validator(mode="after")
    def _finding_is_bound(self) -> AdmittedRecommendation:
        if self.finding is not None and (
            self.finding.status is not FindingStatus.ACCEPTED
            or self.finding.origin is not FindingOrigin.MODEL
            or self.finding.subject_id != self.subject_id
        ):
            msg = "recommendation must bind an accepted model finding for the same subject"
            raise ValueError(msg)
        return self


class FindingAttribution(Contract):
    """Which review raised one adjudicated finding, and where it stood in that review.

    Code-owned, never a model's claim. A finding id cannot do this job: three reviewers each
    number their findings from one, so ``f1`` is three different claims in most runs. The
    synthesis lists every adjudicated finding once, in adjudication order, bound to the
    review it came from, so a downgrade can be traced to the reviewer that asked for it.
    """

    reviewer: Reviewer
    result_index: int = Field(ge=0, description="Position of the review within this pass.")
    position: int = Field(
        ge=0,
        description="Position of the finding within that review, code-raised findings first.",
    )
    raised_subject_id: Identifier | None = Field(
        default=None,
        description=(
            "The subject the finding named as raised. `finding.subject_id` is the subject "
            "adjudication resolved it to, which is the one every decision rule reads."
        ),
    )
    finding: ReviewFinding

    @model_validator(mode="after")
    def _finding_is_adjudicated(self) -> FindingAttribution:
        if self.finding.status is FindingStatus.OPEN:
            msg = "an attributed finding must carry its adjudicated status"
            raise ValueError(msg)
        return self

    def reference(self, stage: str, round_index: int) -> str:
        """A name for this finding that is unique within one run.

        ``internal-1/2-confusion/1`` is the first finding of the second review (the
        confusion reviewer) in the first internal round. Stage and round come from the
        caller because a synthesis does not know which pass it belongs to.
        """
        return (
            f"{stage}-{round_index}/{self.result_index + 1}-{self.reviewer.value}"
            f"/{self.position + 1}"
        )


class ReviewSynthesis(Contract):
    """The adjudicated result of all reviewer findings for one pass."""

    accepted_findings: tuple[ReviewFinding, ...] = ()
    rejected_findings: tuple[ReviewFinding, ...] = ()
    attributions: tuple[FindingAttribution, ...] | None = Field(
        default=None,
        description=(
            "Every adjudicated finding bound to the review that raised it, in adjudication "
            "order. None denotes a synthesis built without that record; its findings can "
            "still be applied, but not attributed to a reviewer."
        ),
    )
    admitted_reranks: tuple[AdmittedRerank, ...] = ()
    recommendations: tuple[AdmittedRecommendation, ...] | None = Field(
        default=None,
        description=(
            "Code-owned subject and finding bindings. None denotes a legacy synthesis; "
            "its aggregate deltas may cap a single subject but never waive findings. "
            "New synthesis always supplies a tuple, including when nothing was admitted."
        ),
    )
    required_corrections: tuple[CorrectionDirective, ...] = ()
    candidate_delta: tuple[ShortText, ...] = ()
    confidence_delta: Confidence | None = None
    resolution_delta: Resolution | None = None
    retry_required: bool = False
    reviewer_disagreement: bool = Field(
        default=False,
        description="Review recommendations or admitted reranks conflict materially.",
    )
    escalation_recommended: bool = False
    unresolvable: bool = Field(
        default=False,
        description=(
            "A critical abstain finding with a verifiable basis: raised by code, or citing "
            "admitted evidence. A retry cannot fix it. Routes to abstain."
        ),
    )
    unverified_abstention: bool = Field(
        default=False,
        description=(
            "A critical abstain finding asserted by a model with no evidence citation. It "
            "does not route to abstain; it abstains only if independent arbitration confirms "
            "it, or fails closed if arbitration does not run."
        ),
    )

    @model_validator(mode="after")
    def _recommendations_reference_accepted_findings(self) -> ReviewSynthesis:
        for recommendation in self.recommendations or ():
            if (
                recommendation.finding is not None
                and recommendation.finding not in self.accepted_findings
            ):
                msg = "recommendation finding must belong to this synthesis"
                raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _attributions_cover_the_adjudicated_findings(self) -> ReviewSynthesis:
        if self.attributions is None:
            return self
        attributed = [attribution.finding for attribution in self.attributions]
        accepted = [f for f in attributed if f.status is FindingStatus.ACCEPTED]
        rejected = [f for f in attributed if f.status is FindingStatus.REJECTED]
        if accepted != list(self.accepted_findings) or rejected != list(self.rejected_findings):
            msg = "attributions must list exactly the accepted and rejected findings, in order"
            raise ValueError(msg)
        return self

    def attribution_of(self, finding: ReviewFinding) -> FindingAttribution | None:
        """The review behind one accepted finding, or None when nothing recorded it.

        Exact for accepted findings: adjudication admits a material claim once, so no two
        accepted findings of one pass are equal.
        """
        for attribution in self.attributions or ():
            if attribution.finding == finding:
                return attribution
        return None

    @property
    def has_critical(self) -> bool:
        return any(finding.severity is Severity.CRITICAL for finding in self.accepted_findings)
