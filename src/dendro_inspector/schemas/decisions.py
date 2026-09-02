"""Decision and user-facing result contracts.

``FinalDecision`` is the scientific verdict. ``StructuredFinalResult`` is the same verdict
shaped for a consumer. ``human_readable`` is presentation. The tone layer may only touch
the last of the three — enforced by :func:`assert_tone_preserved_decision`.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from dendro_inspector.schemas.base import Contract, Identifier, ShortText, ValueToken
from dendro_inspector.schemas.candidates import SupportStrength
from dendro_inspector.schemas.evidence import AttachmentStatus
from dendro_inspector.schemas.taxon import Confidence, Resolution


class DecisionStatus(StrEnum):
    IDENTIFIED = "identified"
    PROBABLE = "probable"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    CONFLICTING_EVIDENCE = "conflicting_evidence"
    UNSUPPORTED_USER_CLAIM = "unsupported_user_claim"


class UserClaimVerdict(StrEnum):
    """Ruling on the taxon the user proposed (domain prompt section 3).

    The user may hold evidence the photograph does not show — foliage out of frame, the
    fruit, where the tree was felled. So a claim is checked against the visible features,
    never dismissed for failing to match a preferred answer.

    ``REJECTED`` is deliberately hard to reach: it requires strong contrary evidence above
    bark level. On bark alone the strongest available ruling is ``DOUBTFUL``.

    ``NOT_EVALUABLE`` is distinct from ``DOUBTFUL``. Doubtful means there are reasons to
    doubt the claim; not-evaluable means the photograph cannot support *any* assessment of
    it, in either direction. Reporting the second as the first quietly credits the system
    with an opinion it does not have.
    """

    NOT_PROVIDED = "not_provided"
    NOT_EVALUABLE = "not_evaluable"
    ACCEPTED = "accepted"
    POSSIBLE = "possible"
    DOUBTFUL = "doubtful"
    REJECTED = "rejected"


ResolutionBoundSource = Literal[
    "proposed",
    "card_cap",
    "tier_ceiling",
    "reviewer_recommendation",
    "abstention",
]
ConfidenceStepSource = Literal[
    "seed",
    "tier_cap",
    "requirement_cap",
    "reviewer_recommendation",
    "model_finding",
    "deterministic_finding",
    "abstention",
    "no_identity",
]
RerankSource = Literal["arbiter", "internal", "none"]


class ResolutionBound(Contract):
    """One upper bound considered while composing a taxonomic resolution."""

    source: ResolutionBoundSource
    value: Resolution


class ConfidenceStep(Contract):
    """One ordered confidence operation, including a no-op already honoured upstream.

    ``applied`` says whether the operation ran, not whether the value moved. A step that a
    reviewer floor already honoured is recorded with ``applied=False`` so the audit can tell
    "this finding was skipped" from "this finding cost a step that landed on the same band".
    """

    source: ConfidenceStepSource
    finding_id: Identifier | None = None
    before: Confidence
    after: Confidence
    applied: bool


class DecisionDerivation(Contract):
    """Auditable deterministic composition record for one subject's final verdict."""

    subject_id: Identifier
    proposed_strength: SupportStrength
    effective_strength: SupportStrength
    resolution_bounds: tuple[ResolutionBound, ...] = Field(min_length=1)
    resolution_binding_source: ResolutionBoundSource
    resolution_action_applied: bool
    confidence_steps: tuple[ConfidenceStep, ...] = Field(min_length=1)
    rerank_source: RerankSource = "none"
    rerank_finding_id: Identifier | None = None

    @classmethod
    def terminal(cls, subject_id: str) -> DecisionDerivation:
        """The record for a verdict that never reached composition.

        No candidate survived, or the planner answered before the engine ran. There is still
        exactly one derivation per verdict, so a reader never has to decide whether a missing
        record means "not composed" or "not recorded".
        """
        return cls(
            subject_id=subject_id,
            proposed_strength=SupportStrength.WEAK,
            effective_strength=SupportStrength.WEAK,
            resolution_bounds=(ResolutionBound(source="proposed", value=Resolution.UNKNOWN),),
            resolution_binding_source="proposed",
            resolution_action_applied=False,
            confidence_steps=(
                ConfidenceStep(
                    source="seed",
                    before=Confidence.LOW,
                    after=Confidence.LOW,
                    applied=True,
                ),
            ),
        )

    @model_validator(mode="after")
    def _references_an_applied_bound_and_rerank(self) -> DecisionDerivation:
        if self.resolution_binding_source not in {bound.source for bound in self.resolution_bounds}:
            msg = "resolution_binding_source must name a recorded resolution bound"
            raise ValueError(msg)
        if (self.rerank_source == "none") != (self.rerank_finding_id is None):
            msg = "rerank_finding_id is required exactly when rerank_source is not none"
            raise ValueError(msg)
        return self


class AuthorityCheckStatus(StrEnum):
    """Outcome of the deterministic attachment-authority check for one subject.

    A boolean could not say *why* it was false. "We ran the counterfactual and the verdict
    did not move" and "there was no counterfactual to run" are different scientific facts,
    and reporting the second as the first credits the run with a check it never performed.
    """

    NOT_APPLICABLE = "not_applicable"
    NOT_TESTABLE = "not_testable"
    NOT_SENSITIVE = "not_sensitive"
    SENSITIVE = "sensitive"


class AuthorityOutcome(Contract):
    """The scientific outcome of one evidence world, reduced to what authority can move."""

    status: DecisionStatus
    taxon: Identifier | None = None
    resolution: Resolution = Resolution.UNKNOWN
    confidence: Confidence = Confidence.LOW


class AuthorityCheckTrace(Contract):
    """Per-subject record of the attachment-authority counterfactual.

    One record per subject, never a union across subjects: a run whose critical evidence
    ids came from one subject and whose counterfactual taxon came from another describes
    no world that ever existed.
    """

    subject_id: Identifier
    status: AuthorityCheckStatus = AuthorityCheckStatus.NOT_APPLICABLE
    critical_evidence_ids: tuple[Identifier, ...] = Field(
        default=(),
        description=(
            "Sensitivity: detachable observation ids whose authority moves the verdict. "
            "Nearly every honest organ-level identification is sensitive in this sense — "
            "that a leaf decides the answer is what leaves are for."
        ),
    )
    risk_evidence_ids: tuple[Identifier, ...] = Field(
        default=(),
        description=(
            "Risk: the subset of the above whose ownership is *structurally* ambiguous. "
            "Sensitivity alone never withdraws a claim; sensitivity plus risk does."
        ),
    )
    policy_applied: bool = Field(
        default=False,
        description=(
            "Whether the conservative evidence world became the one the graph used. True "
            "only when a risky observation is also the hinge, and demoting it moves the "
            "outcome."
        ),
    )
    actual_outcome: AuthorityOutcome | None = Field(
        default=None,
        description="Outcome under evidence exactly as extracted, before the gate acted.",
    )
    counterfactual_outcome: AuthorityOutcome | None = Field(
        default=None,
        description="Outcome under the attachment world the gate did NOT hand to the graph.",
    )
    counterfactual_attachment: AttachmentStatus | None = Field(
        default=None,
        description="Attachment state that produced counterfactual_outcome.",
    )

    @model_validator(mode="after")
    def _sensitivity_carries_its_evidence(self) -> AuthorityCheckTrace:
        sensitive = self.status is AuthorityCheckStatus.SENSITIVE
        if sensitive:
            if (
                not self.critical_evidence_ids
                or self.counterfactual_outcome is None
                or self.counterfactual_attachment is None
            ):
                msg = (
                    "a sensitive authority check requires critical evidence ids and a "
                    "counterfactual outcome and attachment state"
                )
                raise ValueError(msg)
            if not set(self.risk_evidence_ids) <= set(self.critical_evidence_ids):
                msg = "risk evidence ids must be a subset of the critical evidence ids"
                raise ValueError(msg)
            if self.policy_applied and not self.risk_evidence_ids:
                msg = (
                    "the conservative world may only be applied for structurally ambiguous "
                    "evidence; sensitivity on its own is not a reason to withdraw a claim"
                )
                raise ValueError(msg)
            return self
        if (
            self.critical_evidence_ids
            or self.risk_evidence_ids
            or self.policy_applied
            or self.counterfactual_outcome is not None
            or self.counterfactual_attachment is not None
        ):
            msg = "authority metadata requires status=sensitive"
            raise ValueError(msg)
        return self


class PhotoRequest(Contract):
    """A targeted request for the one photograph that would most improve the result."""

    target: ValueToken
    reason: ShortText
    subject_id: Identifier | None = None


class FinalDecision(Contract):
    """The per-subject scientific verdict. Immutable once produced."""

    subject_id: Identifier
    selected_taxon: Identifier | None = None
    selected_taxon_display_name: str | None = Field(default=None, min_length=1, max_length=120)
    resolution: Resolution = Resolution.UNKNOWN
    confidence: Confidence = Confidence.LOW
    status: DecisionStatus = DecisionStatus.INSUFFICIENT_EVIDENCE
    supporting_evidence: tuple[ShortText, ...] = Field(
        default=(),
        description=(
            "Every validated supporting observation, strongest first. A tuple rather than "
            "one line: a verdict that cited only its first support displayed less evidence "
            "than the abstention beside it, which reads as the weaker answer."
        ),
    )
    strongest_contradiction: ShortText | None = None
    nearest_alternative: Identifier | None = None
    unresolved_questions: tuple[ShortText, ...] = ()
    best_next_photo: PhotoRequest | None = None
    arbiter_used: bool = False
    abstained: bool = Field(
        default=False,
        description=(
            "The run abstained: this verdict is deliberately broader than the evidence earned."
        ),
    )
    user_claim_verdict: UserClaimVerdict = UserClaimVerdict.NOT_PROVIDED
    evidence_tier: int = Field(
        default=1,
        ge=1,
        le=7,
        description="Strongest evidence tier available for this subject (EvidenceTier).",
    )
    confidence_band: str = Field(
        default="<50/100",
        max_length=16,
        description="Confidence on the domain prompt's X/100 scale, as a band never a point.",
    )
    authority_check_status: AuthorityCheckStatus = Field(
        default=AuthorityCheckStatus.NOT_APPLICABLE,
        description=(
            "What the deterministic attachment-authority gate found for this subject. "
            "Copied from the gate's per-subject record; never a model opinion."
        ),
    )
    critical_evidence_ids: tuple[Identifier, ...] = Field(
        default=(),
        description="Detachable observation ids whose authority materially changes the verdict.",
    )
    authority_policy_applied: bool = Field(
        default=False,
        description=(
            "Whether insufficient attachment provenance made the conservative evidence world "
            "the one this verdict was computed from."
        ),
    )
    counterfactual_status: DecisionStatus | None = None
    counterfactual_taxon: Identifier | None = None
    counterfactual_resolution: Resolution | None = None
    counterfactual_confidence: Confidence | None = None
    counterfactual_attachment: AttachmentStatus | None = Field(
        default=None,
        description=(
            "Attachment state behind the alternate outcome — the world the gate did not "
            "hand to the graph. Confirmed_attached means the claim this verdict declines "
            "to make is the one that would follow if that evidence were proven attached."
        ),
    )

    @model_validator(mode="after")
    def _taxon_identity_matches_resolution(self) -> FinalDecision:
        has_taxon = self.selected_taxon is not None
        has_display_name = self.selected_taxon_display_name is not None
        if has_taxon != has_display_name:
            msg = "selected_taxon and selected_taxon_display_name must be set together"
            raise ValueError(msg)
        if self.resolution is Resolution.UNKNOWN and has_taxon:
            msg = "resolution=unknown requires selected_taxon=None"
            raise ValueError(msg)
        if self.resolution is not Resolution.UNKNOWN and not has_taxon:
            msg = "a non-unknown resolution requires a selected taxon identity"
            raise ValueError(msg)
        if self.evidence_authority_sensitive:
            if (
                not self.critical_evidence_ids
                or self.counterfactual_status is None
                or self.counterfactual_attachment is None
            ):
                msg = (
                    "evidence-authority sensitivity requires critical evidence ids and a "
                    "counterfactual outcome and attachment state"
                )
                raise ValueError(msg)
        elif (
            self.critical_evidence_ids
            or self.authority_policy_applied
            or self.counterfactual_attachment is not None
        ):
            msg = "authority metadata requires authority_check_status=sensitive"
            raise ValueError(msg)
        if self.authority_policy_applied and self.counterfactual_status is None:
            msg = "an applied authority policy requires a counterfactual outcome"
            raise ValueError(msg)
        return self

    @property
    def evidence_authority_sensitive(self) -> bool:
        """Convenience view of :attr:`authority_check_status`, for readers that only ask."""
        return self.authority_check_status is AuthorityCheckStatus.SENSITIVE

    @property
    def claims_species(self) -> bool:
        return self.resolution is Resolution.SPECIES


class StructuredFinalResult(Contract):
    """Machine-readable answer for one subject."""

    verdict: ShortText
    subject: Identifier
    taxonomic_resolution: Resolution
    confidence: Confidence
    confidence_band: str = Field(default="<50/100", max_length=16)
    user_claim_verdict: UserClaimVerdict = UserClaimVerdict.NOT_PROVIDED
    supporting_evidence: tuple[ShortText, ...] = ()
    ruled_out: tuple[ShortText, ...] = Field(
        default=(),
        description="Why the nearest alternatives are not the answer (prompt sections 7-8).",
    )
    strongest_contradiction: ShortText | None = None
    nearest_alternative: Identifier | None = None
    limitations: tuple[ShortText, ...] = ()
    best_next_photo: PhotoRequest | None = None


class ToneMode(StrEnum):
    """How hard the answer is allowed to bite (domain prompt sections 4, 5 and 12).

    ``HARD`` requires a conjunction of conditions, not a mood. ``CORRECTIVE`` outranks it:
    after being corrected, there is no sarcasm and no joke — only the admission, the
    feature that was overweighted, and the rule to keep.
    """

    HARD = "hard"
    MEASURED = "measured"
    CAUTIOUS = "cautious"
    CORRECTIVE = "corrective"


class ResponseFormat(StrEnum):
    """Which of the domain prompt's response shapes applies (sections 7-11)."""

    NO_VERSION = "no_version"
    WITH_VERSION = "with_version"
    USER_CORRECT = "user_correct"
    USER_WRONG = "user_wrong"
    WEAK_PHOTO = "weak_photo"


class CaseResponse(Contract):
    """The composed answer for a whole case, before and after the tone layer."""

    case_id: Identifier
    results: tuple[StructuredFinalResult, ...] = ()
    decisions: tuple[FinalDecision, ...] = ()
    human_readable: str = Field(default="", max_length=20000)
    tone_applied: bool = False
    tone_mode: ToneMode = ToneMode.MEASURED
    response_format: ResponseFormat = ResponseFormat.NO_VERSION
    joke_allowed: bool = Field(
        default=False,
        description=(
            "Section 5: a joke needs a clearly wrong user version, high confidence and "
            "strong evidence — and is forbidden outright after the system's own mistake."
        ),
    )
    locale: str = Field(default="uk", max_length=8)


def assert_tone_preserved_decision(
    before: CaseResponse,
    after: CaseResponse,
) -> None:
    """Raise if the tone layer changed anything scientific.

    The tone layer is allowed to rewrite ``human_readable`` and set ``tone_applied``.
    Touching a taxon, resolution, confidence, contradiction, alternative or photo request
    is a contract violation, not a style choice.
    """
    if before.results != after.results:
        msg = "tone layer mutated structured results"
        raise ValueError(msg)
    if before.decisions != after.decisions:
        msg = "tone layer mutated final decisions"
        raise ValueError(msg)
    if before.case_id != after.case_id:
        msg = "tone layer mutated case_id"
        raise ValueError(msg)
    if before.tone_mode is not after.tone_mode:
        msg = "tone layer changed its own permission level"
        raise ValueError(msg)
    if before.joke_allowed != after.joke_allowed:
        msg = "tone layer granted itself permission to joke"
        raise ValueError(msg)
