"""Execution events and the run trace.

The trace is the product, not a debug afterthought: every run records which nodes ran,
what they decided, how many retries were spent, whether the arbiter was called, and which
domain-prompt hash was in force. It never records API keys, image bytes, private user
metadata, or hidden model reasoning.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import Field

from dendro_inspector.schemas.base import Contract, Identifier, ShortText
from dendro_inspector.schemas.decisions import (
    AuthorityCheckTrace,
    DecisionDerivation,
    FinalDecision,
)
from dendro_inspector.schemas.evidence import KnowledgeCoverage
from dendro_inspector.schemas.taxon import Confidence, Resolution

GRAPH_VERSION = "0.9.0"


class NodeStatus(StrEnum):
    OK = "ok"
    SKIPPED = "skipped"
    RETRIED = "retried"
    FAILED = "failed"


class PromptCompatibilityStatus(StrEnum):
    """Whether prompt-policy compatibility has been established."""

    UNVALIDATED = "unvalidated"
    COMPATIBLE = "compatible"


class ProviderCallRecord(Contract):
    """One structured model call. Prompt and response bodies are deliberately absent."""

    role: str = Field(max_length=40)
    adapter: str = Field(max_length=40)
    model: str | None = Field(default=None, max_length=120)
    node: str = Field(max_length=60)
    response_model: str = Field(max_length=120)
    attempts: int = Field(default=1, ge=1)
    validation_failures: int = Field(default=0, ge=0)
    validation_errors: tuple[str, ...] = Field(
        default=(),
        max_length=40,
        description=(
            "`attempt-N:<field path>:<error type>` for every validation failure this call "
            "repaired or died on. Field paths and error kinds only, never response bodies."
        ),
    )
    duration_ms: float | None = Field(default=None, ge=0)
    #: Provider-reported accounting, summed over every attempt this call made, because
    #: `duration_ms` spans them all. `None` means the provider reported nothing, which is a
    #: different fact from zero and must not be rendered as one.
    input_tokens: int | None = Field(
        default=None, ge=0, description="Total input tokens, including cache reads and writes."
    )
    cached_input_tokens: int | None = Field(
        default=None,
        ge=0,
        description="Prompt tokens served from the provider's cache, where it says so.",
    )
    output_tokens: int | None = Field(default=None, ge=0)
    cache_write_input_tokens: int | None = Field(
        default=None,
        ge=0,
        description="Input tokens written to the provider's cache, already in input_tokens.",
    )
    reasoning_output_tokens: int | None = Field(
        default=None,
        ge=0,
        description=(
            "The part of `output_tokens` a provider attributes to hidden reasoning, where "
            "it reports one separately. It is billed as output and is already included in "
            "`output_tokens`; this field says how much of that total was never shown."
        ),
    )
    reported_cost_usd: float | None = Field(
        default=None,
        ge=0,
        description="Cost as the provider reported it. Never estimated from a price table.",
    )


class ReviewerProjectionRecord(Contract):
    """Audit-safe summary of the bounded input supplied to one reviewer."""

    reviewer: str = Field(max_length=40)
    evidence_ids: tuple[Identifier, ...] = ()
    image_ids: tuple[Identifier, ...] = ()
    candidate_subject_ids: tuple[Identifier, ...] = ()
    taxon_ids: tuple[Identifier, ...] = ()
    include_comparison_cards: bool = True
    include_regional_pack: bool = True


class NodeEvent(Contract):
    """One executed node."""

    node: str = Field(max_length=60)
    status: NodeStatus = NodeStatus.OK
    sequence: int = Field(ge=0)
    detail: ShortText | None = None
    duration_ms: float | None = Field(default=None, ge=0)
    provider_calls: tuple[ProviderCallRecord, ...] = ()
    reviewer_projection: ReviewerProjectionRecord | None = None


class ComponentProjection(Contract):
    """Auditable component-to-identity mapping created by deterministic normalization."""

    identity_subject_id: Identifier
    source_component_id: Identifier
    observation_ids: tuple[Identifier, ...] = Field(min_length=1)


class PromptMetadata(Contract):
    """Identity and policy compatibility of the prompt bundle in force."""

    path: str = Field(max_length=400)
    version: str = Field(default="user-managed", max_length=80)
    sha256: str = Field(min_length=64, max_length=64)
    bytes: int = Field(ge=0)
    is_placeholder: bool = False
    manifest_schema_version: str | None = Field(default=None, max_length=40)
    policy_revision: str | None = Field(default=None, max_length=80)
    node_prompt_revision: str | None = Field(default=None, max_length=80)
    manifest_path: str | None = Field(default=None, max_length=400)
    manifest_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    compatibility_status: PromptCompatibilityStatus = PromptCompatibilityStatus.UNVALIDATED


#: Bounds on the evidence ledger, so a pathological extraction cannot bloat a case record.
MAX_LEDGER_OBSERVATIONS = 400
MAX_LEDGER_SUBJECTS = 32


class ObservationRecord(Contract):
    """One observation as the decision rules saw it. Tokens only: no notes, no image bytes."""

    observation_id: str = Field(max_length=120)
    subject_id: str = Field(max_length=120)
    feature: str = Field(max_length=120)
    value: str = Field(max_length=120)
    source: str = Field(max_length=40)
    image_id: str | None = Field(default=None, max_length=120)
    visibility: str = Field(max_length=40)
    reliability: str = Field(max_length=40)
    attachment: str | None = Field(default=None, max_length=40)
    wood_surface: str | None = Field(default=None, max_length=40)
    trust: str = Field(
        max_length=40,
        description="Whether this reading may support an identification (EvidenceTrust).",
    )
    tier: int = Field(ge=0, le=10, description="Effective evidence tier after trust.")


class CandidateSupportRecord(Contract):
    """One candidate after validation, with the support it kept."""

    taxon: str = Field(max_length=120)
    resolution: Resolution
    score: str = Field(max_length=40)
    rank: int = Field(ge=1)
    supporting_evidence_ids: tuple[str, ...] = ()
    contradicting_evidence_ids: tuple[str, ...] = ()


class DroppedSupportRecord(Contract):
    """One proposed support id that validation refused, and why (PR #18's details)."""

    taxon: str = Field(max_length=120)
    evidence_id: str = Field(max_length=120)
    reason: str = Field(max_length=60)
    feature: str | None = Field(default=None, max_length=120)
    value: str | None = Field(default=None, max_length=120)
    expected_values: tuple[str, ...] = ()


class CandidateValidationRecord(Contract):
    """What deterministic validation admitted and rejected for one subject's candidates."""

    subject_id: str = Field(max_length=120)
    proposed_taxa: tuple[str, ...] = ()
    admitted: tuple[CandidateSupportRecord, ...] = ()
    dropped_support: tuple[DroppedSupportRecord, ...] = ()
    rejected_taxa: tuple[str, ...] = ()
    demoted_scores: tuple[str, ...] = Field(
        default=(), description="`taxon:proposed->effective` support strength changes."
    )
    malformed_evidence_ids: tuple[str, ...] = ()


class RunTrace(Contract):
    """The inspectable record of one graph run."""

    case_id: str = Field(max_length=120)
    graph_version: str = GRAPH_VERSION
    code_commit_sha: str | None = Field(
        default=None,
        min_length=40,
        max_length=64,
        pattern=r"^[0-9a-f]+$",
        description="Immutable VCS revision, when the runtime can discover one.",
    )
    code_dirty: bool | None = Field(
        default=None,
        description=(
            "Whether tracked or untracked repository files differed from code_commit_sha "
            "when the run began; None means VCS identity was unavailable."
        ),
    )
    domain_prompt: PromptMetadata | None = None
    providers: dict[str, str] = Field(
        default_factory=dict,
        description="role -> 'adapter:model'. Never a credential.",
    )
    events: tuple[NodeEvent, ...] = ()
    component_projections: tuple[ComponentProjection, ...] = ()
    knowledge_coverage: KnowledgeCoverage | None = Field(
        default=None,
        description=(
            "What this run observed that no knowledge card can represent. Recorded on every "
            "run, including clean ones, so a suite can aggregate it — a field that appears "
            "only when something is wrong cannot be a denominator. Without it a failed case "
            "cannot be attributed between a model that did not see and cards that could not "
            "accept what it saw."
        ),
    )
    evidence_observations: tuple[ObservationRecord, ...] = Field(
        default=(),
        max_length=MAX_LEDGER_OBSERVATIONS,
        description=(
            "The final evidence packet's observations: which subject each was attributed "
            "to, how it was read, and whether it was trusted to support an identification."
        ),
    )
    candidate_validations: tuple[CandidateValidationRecord, ...] = Field(
        default=(),
        max_length=MAX_LEDGER_SUBJECTS,
        description=(
            "Per subject, the support validation admitted and the support it refused, with "
            "a machine-readable reason for each refusal."
        ),
    )
    retries: int = Field(default=0, ge=0)
    graph_retry_count: int = Field(
        default=0,
        ge=0,
        description="Graph correction-loop retries; distinct from provider validation attempts.",
    )
    correction_changed_outcome: bool | None = None
    correction_changed_status: bool | None = None
    correction_changed_taxon: bool | None = None
    correction_changed_resolution: bool | None = None
    correction_changed_confidence: bool | None = None
    provisional_decisions: tuple[FinalDecision, ...] = Field(
        default=(),
        description="Deterministic verdicts immediately before any arbiter call.",
    )
    arbiter_changed_status: bool | None = None
    arbiter_changed_taxon: bool | None = None
    arbiter_changed_resolution: bool | None = None
    arbiter_changed_confidence: bool | None = None
    authority_checks: tuple[AuthorityCheckTrace, ...] = Field(
        default=(),
        description=(
            "One attachment-authority record per subject. A run with two subjects has two "
            "records; flattening them produced critical evidence ids from one subject "
            "beside a counterfactual taxon from another, describing no world that existed."
        ),
    )
    decision_derivations: tuple[DecisionDerivation, ...] = Field(
        default=(),
        description="One deterministic composition record per final subject verdict.",
    )
    user_claim_negated: bool = Field(
        default=False,
        description=(
            "The user's claim named a taxon only to deny it. Nothing was offered to rule "
            "on, so the verdict is `possible` rather than a ruling against the denied taxon."
        ),
    )
    evidence_authority_sensitive: bool = Field(
        default=False,
        description="Convenience aggregate: any subject's check came back sensitive.",
    )
    escalation_triggered: bool = False
    escalation_reasons: tuple[str, ...] = ()
    arbiter_used: bool = False
    final_resolution: Resolution | None = None
    final_confidence: Confidence | None = None
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    duration_ms: float | None = Field(default=None, ge=0)
    critical_path_ms: float | None = Field(
        default=None,
        ge=0,
        description=(
            "Wall time no amount of concurrency could remove: every serial node plus the "
            "slowest member of each fan-out round. Compare against duration_ms to see what "
            "running the reviewers together actually bought."
        ),
    )

    @property
    def executed_nodes(self) -> tuple[str, ...]:
        return tuple(event.node for event in self.events)
