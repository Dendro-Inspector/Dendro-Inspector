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

from evil_duck_dendro.schemas.base import Contract, ShortText
from evil_duck_dendro.schemas.taxon import Confidence, Resolution

GRAPH_VERSION = "0.2.1"


class NodeStatus(StrEnum):
    OK = "ok"
    SKIPPED = "skipped"
    RETRIED = "retried"
    FAILED = "failed"


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


class NodeEvent(Contract):
    """One executed node."""

    node: str = Field(max_length=60)
    status: NodeStatus = NodeStatus.OK
    sequence: int = Field(ge=0)
    detail: ShortText | None = None
    duration_ms: float | None = Field(default=None, ge=0)
    provider_calls: tuple[ProviderCallRecord, ...] = ()


class PromptMetadata(Contract):
    """Identity of the opaque, user-managed domain prompt in force for this run."""

    path: str = Field(max_length=400)
    version: str = Field(default="user-managed", max_length=80)
    sha256: str = Field(min_length=64, max_length=64)
    bytes: int = Field(ge=0)
    is_placeholder: bool = False


class RunTrace(Contract):
    """The inspectable record of one graph run."""

    case_id: str = Field(max_length=120)
    graph_version: str = GRAPH_VERSION
    domain_prompt: PromptMetadata | None = None
    providers: dict[str, str] = Field(
        default_factory=dict,
        description="role -> 'adapter:model'. Never a credential.",
    )
    events: tuple[NodeEvent, ...] = ()
    retries: int = Field(default=0, ge=0)
    escalation_triggered: bool = False
    escalation_reasons: tuple[str, ...] = ()
    arbiter_used: bool = False
    final_resolution: Resolution | None = None
    final_confidence: Confidence | None = None
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    duration_ms: float | None = Field(default=None, ge=0)

    @property
    def executed_nodes(self) -> tuple[str, ...]:
        return tuple(event.node for event in self.events)
