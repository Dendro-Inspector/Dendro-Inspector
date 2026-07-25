"""Evaluation contracts.

An evaluation case declares an input, the provider scenario to run it against, and
assertions over the *graph's decisions* — not over prose. Everything here is deterministic
for v0.1: no paid API call is required to run the public suite.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from evil_duck_dendro.schemas.base import Contract, Identifier, ShortText, ValueToken
from evil_duck_dendro.schemas.decisions import DecisionStatus, UserClaimVerdict
from evil_duck_dendro.schemas.input import CaseInput
from evil_duck_dendro.schemas.reviews import FindingCategory
from evil_duck_dendro.schemas.taxon import Confidence, Resolution


class EvalExpectation(Contract):
    """Assertions for one case. Every field is optional; only stated ones are checked."""

    expected_taxon: Identifier | None = None
    expected_taxon_in_top_3: Identifier | None = None
    expected_resolution: Resolution | None = None
    max_resolution: Resolution | None = Field(
        default=None,
        description="The narrowest permitted claim. Species-level output fails when this is genus.",
    )
    max_confidence: Confidence | None = None
    expected_status: DecisionStatus | None = None
    min_subjects: int | None = Field(default=None, ge=1)
    require_next_photo: bool = False
    require_escalation: bool | None = None
    require_finding_category: FindingCategory | None = None
    require_candidate_delta: bool = False
    forbid_evidence_leak_between_subjects: bool = False
    max_retries: int | None = Field(default=None, ge=0)
    expected_user_claim_verdict: UserClaimVerdict | None = Field(
        default=None,
        description="Ruling on the user's own version. `rejected` must be hard to reach.",
    )
    forbid_user_claim_rejection: bool = Field(
        default=False,
        description=(
            "Assert the user's version was NOT rejected — the restraint rule for bark-only "
            "evidence and for a user who holds field context."
        ),
    )
    expected_evidence_tier: int | None = Field(
        default=None,
        ge=1,
        le=7,
        description="Strongest evidence tier the case should resolve to (EvidenceTier).",
    )
    require_alternative_named: bool = Field(
        default=False,
        description="Assert a look-alike was surfaced that the model itself did not propose.",
    )


class EvalCase(Contract):
    """One public evaluation case."""

    case_id: Identifier
    title: ShortText
    description: ShortText
    scenario: ValueToken = Field(
        description="Fake-provider scenario file stem under evals/fixtures/.",
    )
    input: CaseInput
    expect: EvalExpectation = EvalExpectation()
    placeholder_content: bool = True


class AssertionOutcome(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"


class AssertionResult(Contract):
    name: str = Field(min_length=1, max_length=120)
    outcome: AssertionOutcome
    detail: ShortText | None = None


class CaseOutcome(Contract):
    case_id: Identifier
    passed: bool
    assertions: tuple[AssertionResult, ...] = ()
    error: ShortText | None = None
    resolution: Resolution | None = None
    confidence: Confidence | None = None
    selected_taxon: Identifier | None = None
    arbiter_used: bool = False
    retries: int = 0
    schema_valid: bool = True


class EvalMetrics(Contract):
    """Metrics defined in docs/evaluation.md. Counts are reported alongside rates."""

    cases: int = 0
    top_1_accuracy: float = 0.0
    top_3_recall: float = 0.0
    correct_resolution_rate: float = 0.0
    overconfidence_rate: float = 0.0
    abstention_quality: float = 0.0
    schema_validity: float = 0.0
    escalation_precision: float = 0.0
    escalation_recall: float = 0.0
    unnecessary_arbiter_call_rate: float = 0.0
    scored_top_1: int = 0
    scored_top_3: int = 0
    scored_resolution: int = 0
    scored_abstention: int = 0
    escalations_expected: int = 0
    escalations_observed: int = 0
    escalations_correct: int = 0


class EvalReport(Contract):
    suite: str = Field(min_length=1, max_length=80)
    passed: int = 0
    failed: int = 0
    outcomes: tuple[CaseOutcome, ...] = ()
    metrics: EvalMetrics = EvalMetrics()

    @property
    def ok(self) -> bool:
        return self.failed == 0
