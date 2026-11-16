"""The review plan the adaptive gate derives, and the record of why it chose it.

A plan decides which reviewer *models* are asked. It never decides what code checks: every
reviewer's code-raised findings are computed for every plan, so skipping a model call
cannot skip a safeguard. The record is written in shadow mode as well, where the plan is
only measured and the full review runs as before.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from dendro_inspector.schemas.base import (
    Contract,
    EvidenceRef,
    Identifier,
    ShortText,
    ValueToken,
)
from dendro_inspector.schemas.decisions import DecisionStatus
from dendro_inspector.schemas.reviews import Reviewer
from dendro_inspector.schemas.taxon import Confidence, Resolution

#: Bumped whenever a rule in ``nodes/adaptive_gate.py`` changes what a plan would be, so
#: records made under different rules are never pooled.
ROUTING_POLICY_VERSION = "adaptive-routing-1"

#: The reviewers a plan chooses among. The arbiter is not one of them: whether it runs is
#: the escalation gate's decision, under its own settings.
PLANNED_REVIEWERS: tuple[Reviewer, ...] = (
    Reviewer.BOTANICAL,
    Reviewer.CONFUSION,
    Reviewer.CONFIDENCE,
)


class ReviewRoute(StrEnum):
    FULL_REVIEW = "full_review"
    """All three reviewer models, as without adaptive routing."""
    TARGETED_REVIEW = "targeted_review"
    """Only the reviewer models an open question names."""
    FAST_PATH = "fast_path"
    """No reviewer model: code alone supports an uncontested genus at high confidence."""
    REQUEST_PHOTOS = "request_photos"
    """No reviewer model: nothing was admitted, so there is no claim to review and only a
    new photograph can change the answer."""


#: Routes that call no reviewer model.
NO_MODEL_REVIEW: frozenset[ReviewRoute] = frozenset(
    {ReviewRoute.FAST_PATH, ReviewRoute.REQUEST_PHOTOS}
)


class RoutingCheck(Contract):
    """One evaluated rule. Recorded when it passes as well, so a skip can be audited."""

    name: ValueToken
    passed: bool
    subject_id: Identifier | None = None
    evidence_ids: tuple[EvidenceRef, ...] = ()


class ReviewQuestion(Contract):
    """The open question a reviewer is called to answer, stated by code."""

    reviewer: Reviewer
    code: ValueToken
    subject_id: Identifier | None = None
    question: ShortText
    evidence_ids: tuple[EvidenceRef, ...] = ()


class ProvisionalOutcome(Contract):
    """What code alone decided for one subject before any reviewer model was asked."""

    subject_id: Identifier
    status: DecisionStatus
    selected_taxon: Identifier | None = None
    resolution: Resolution
    confidence: Confidence


class RoutingAssessment(Contract):
    """One round's review plan: the route, every rule behind it, and what it bypasses."""

    policy_version: str = Field(default=ROUTING_POLICY_VERSION, max_length=60)
    round_index: int = Field(default=1, ge=1)
    route: ReviewRoute
    applied: bool = Field(
        default=False,
        description="False in shadow mode: the plan was recorded and the full review ran.",
    )
    fast_path_eligible: bool = Field(
        default=False,
        description=(
            "Whether every fast-path rule passed. Recorded whether or not the fast path is "
            "switched on, so its eligibility can be measured before it is."
        ),
    )
    reasons: tuple[ValueToken, ...] = ()
    checks: tuple[RoutingCheck, ...] = ()
    reviewers: tuple[Reviewer, ...] = Field(
        default=(), description="Reviewer models this plan calls."
    )
    skipped_reviewers: tuple[Reviewer, ...] = Field(
        default=(),
        description=(
            "Reviewer models this plan does not call. Their code-raised findings are still "
            "computed and adjudicated."
        ),
    )
    questions: tuple[ReviewQuestion, ...] = ()
    provisional: tuple[ProvisionalOutcome, ...] = ()

    @model_validator(mode="after")
    def _plan_is_coherent(self) -> RoutingAssessment:
        planned = set(PLANNED_REVIEWERS)
        called, skipped = set(self.reviewers), set(self.skipped_reviewers)
        if not (called | skipped) <= planned or called & skipped:
            msg = "a plan names each internal reviewer at most once, called or skipped"
            raise ValueError(msg)
        if called | skipped != planned:
            msg = "a plan accounts for every internal reviewer"
            raise ValueError(msg)
        if self.route is ReviewRoute.FULL_REVIEW and skipped:
            msg = "full review skips no reviewer"
            raise ValueError(msg)
        if self.route in NO_MODEL_REVIEW and called:
            msg = f"{self.route.value} calls no reviewer model"
            raise ValueError(msg)
        if self.route is ReviewRoute.TARGETED_REVIEW and (not called or not skipped):
            msg = "targeted review calls some reviewers and skips others"
            raise ValueError(msg)
        if self.route is ReviewRoute.FAST_PATH and not self.fast_path_eligible:
            msg = "the fast path needs every eligibility rule to pass"
            raise ValueError(msg)
        if any(question.reviewer not in called for question in self.questions):
            msg = "a question is addressed to a reviewer the plan calls"
            raise ValueError(msg)
        return self

    def calls(self, reviewer: Reviewer) -> bool:
        """Whether the graph asks this reviewer's model. Always true for a plan not applied."""
        return not self.applied or reviewer in self.reviewers

    @property
    def bypassed_model_calls(self) -> int:
        """Reviewer calls an applied plan avoids, or would avoid if it were applied."""
        return len(self.skipped_reviewers)
