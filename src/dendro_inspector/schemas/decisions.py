"""Decision and user-facing result contracts.

``FinalDecision`` is the scientific verdict. ``StructuredFinalResult`` is the same verdict
shaped for a consumer. ``human_readable`` is presentation. The tone layer may only touch
the last of the three — enforced by :func:`assert_tone_preserved_decision`.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from dendro_inspector.schemas.base import Contract, Identifier, ShortText, ValueToken
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
    strongest_support: ShortText | None = None
    strongest_contradiction: ShortText | None = None
    nearest_alternative: Identifier | None = None
    unresolved_questions: tuple[ShortText, ...] = ()
    best_next_photo: PhotoRequest | None = None
    arbiter_used: bool = False
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
        return self

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
