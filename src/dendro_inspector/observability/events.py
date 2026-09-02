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
    duration_ms: float | None = Field(default=None, ge=0)
    #: Provider-reported accounting, summed over every attempt this call made, because
    #: `duration_ms` spans them all. `None` means the provider reported nothing, which is a
    #: different fact from zero and must not be rendered as one.
    input_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(
        default=None,
        ge=0,
        description="Prompt tokens served from the provider's cache, where it says so.",
    )
    output_tokens: int | None = Field(default=None, ge=0)
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
