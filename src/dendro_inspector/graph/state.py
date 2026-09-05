"""Explicit, serializable graph state.

State is frozen. A node never mutates what it is given — it returns a new state via
:meth:`GraphState.evolve`. That is what makes "no hidden global state" checkable rather
than aspirational: if a node wants to change something, the change is visible in its
return value or it did not happen.
"""

from __future__ import annotations

from typing import Any, Self

from pydantic import Field

from dendro_inspector.schemas.base import Contract, FeaturePath, Identifier, ShortText, ValueToken
from dendro_inspector.schemas.candidates import CandidateSet
from dendro_inspector.schemas.decisions import AuthorityCheckTrace, CaseResponse, FinalDecision
from dendro_inspector.schemas.evidence import EvidencePacket
from dendro_inspector.schemas.input import CaseInput
from dendro_inspector.schemas.reviews import CorrectionDirective, ReviewResult, ReviewSynthesis
from dendro_inspector.schemas.taxon import Resolution


class GuardReport(Contract):
    """What the input guard found. Signals are recorded, never obeyed."""

    safe_to_continue: bool = True
    instruction_like_signals: tuple[ValueToken, ...] = Field(
        default=(),
        description="Categories of instruction-like content found in untrusted input.",
    )
    unreadable_images: tuple[Identifier, ...] = ()
    missing_images: tuple[Identifier, ...] = ()
    user_challenges_previous_result: bool = False
    controlled_failure_reason: ShortText | None = None
    notes: tuple[ShortText, ...] = ()

    @property
    def instruction_like_detected(self) -> bool:
        return bool(self.instruction_like_signals)


class InspectionPlan(Contract):
    """What the planner intends to look for. Typed, so the extractor can be graded on it."""

    target_features: tuple[FeaturePath, ...] = ()
    expect_multiple_subjects: bool = False
    bark_only_input: bool = False
    split_firewood_input: bool = False
    notes: tuple[ShortText, ...] = ()


class EvidenceQualityReport(Contract):
    """Whether the evidence can carry any taxonomic claim at all, and how strong a one."""

    sufficient: bool = False
    usable_subject_ids: tuple[Identifier, ...] = ()
    insufficient_reasons: tuple[ValueToken, ...] = ()
    colour_dependence_detected: bool = False
    best_tier_by_subject: dict[Identifier, int] = Field(
        default_factory=dict,
        description="subject_id -> strongest EvidenceTier available. Caps the claim.",
    )
    unattached_evidence_ids: tuple[Identifier, ...] = Field(
        default=(),
        description="Detachable evidence not confirmed as belonging to its subject.",
    )
    unmatchable_evidence_ids: tuple[Identifier, ...] = Field(
        default=(),
        description=(
            "Trusted observations no knowledge card can match on feature and value. They "
            "would have supported a candidate but cannot, so a high count measures card "
            "coverage rather than photograph quality."
        ),
    )

    def tier_for(self, subject_id: str) -> int:
        """Strongest tier for a subject; context (1) when nothing is recorded."""
        return self.best_tier_by_subject.get(subject_id, 1)


class EscalationDecision(Contract):
    """Why the arbiter was or was not called. Both directions are recorded."""

    required: bool = False
    reasons: tuple[ValueToken, ...] = ()
    suppressed_by: tuple[ValueToken, ...] = ()


class SubjectAbstention(Contract):
    """A conservative resolution bound for one subject whose review cannot continue."""

    subject_id: Identifier
    resolution: Resolution


class GraphState(Contract):
    """Everything the graph knows, at one point in the run."""

    case: CaseInput
    guard: GuardReport | None = None
    plan: InspectionPlan | None = None
    evidence: EvidencePacket | None = None
    quality: EvidenceQualityReport | None = None
    proposed_candidate_sets: tuple[CandidateSet, ...] = Field(
        default=(),
        description=(
            "Model-proposed candidate sets retained so deterministic authority checks can "
            "evaluate an attachment counterfactual without making another model call."
        ),
    )
    candidate_sets: tuple[CandidateSet, ...] = Field(
        default=(),
        description=(
            "The candidate world the rest of the graph is allowed to reason about. The "
            "attachment authority gate narrows it before any reviewer sees it."
        ),
    )
    authority_checks: tuple[AuthorityCheckTrace, ...] = Field(
        default=(),
        description="One deterministic attachment-authority record per subject.",
    )
    reviews: tuple[ReviewResult, ...] = ()
    synthesis: ReviewSynthesis | None = None
    provisional_decisions: tuple[FinalDecision, ...] = Field(
        default=(),
        description=(
            "Deterministic per-subject verdicts computed at the escalation gate, before any "
            "arbiter call. The gate decides on these; the arbiter projection shows these."
        ),
    )
    corrections: tuple[CorrectionDirective, ...] = ()
    escalation: EscalationDecision | None = None
    arbiter_reviews: tuple[ReviewResult, ...] = ()
    arbiter_synthesis: ReviewSynthesis | None = None
    pre_correction_decisions: tuple[FinalDecision, ...] = Field(
        default=(),
        description=(
            "Deterministic decisions immediately before the correction loop. Used only to "
            "measure whether the retry changed the scientific outcome."
        ),
    )
    decisions: tuple[FinalDecision, ...] = ()
    response: CaseResponse | None = None
    final_response: CaseResponse | None = None
    retries: int = Field(default=0, ge=0)
    abstained: bool = False
    abstention_bounds: tuple[SubjectAbstention, ...] = ()

    def abstention_for(self, subject_id: str) -> SubjectAbstention | None:
        return next(
            (bound for bound in self.abstention_bounds if bound.subject_id == subject_id), None
        )

    def is_abstained(self, subject_id: str) -> bool:
        """Legacy run-wide abstention remains readable; new runs retain subject scope."""
        return self.abstained and (
            not self.abstention_bounds or self.abstention_for(subject_id) is not None
        )

    def evolve(self, **changes: Any) -> Self:
        """Return a new state with ``changes`` applied and re-validated."""
        data = self.model_dump()
        data.update(changes)
        return type(self).model_validate(data)

    def candidates_for(self, subject_id: str) -> CandidateSet | None:
        for candidate_set in self.candidate_sets:
            if candidate_set.subject_id == subject_id:
                return candidate_set
        return None

    def authority_check_for(self, subject_id: str) -> AuthorityCheckTrace | None:
        for check in self.authority_checks:
            if check.subject_id == subject_id:
                return check
        return None

    def proposed_candidates_for(self, subject_id: str) -> CandidateSet | None:
        for candidate_set in self.proposed_candidate_sets:
            if candidate_set.subject_id == subject_id:
                return candidate_set
        return None

    @property
    def subject_ids(self) -> tuple[str, ...]:
        if self.evidence is None:
            return ()
        return tuple(subject.subject_id for subject in self.evidence.subjects)

    @property
    def arbiter_used(self) -> bool:
        return bool(self.arbiter_reviews)
