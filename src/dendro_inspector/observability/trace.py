"""Trace recording.

A recorder accumulates events during a run and freezes them into an immutable
:class:`RunTrace`. Recorders are per-run objects, never module-level state.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from dendro_inspector.observability.events import (
    GRAPH_VERSION,
    ComponentProjection,
    NodeEvent,
    NodeStatus,
    PromptMetadata,
    ProviderCallRecord,
    RunTrace,
)
from dendro_inspector.observability.logging import get_logger
from dendro_inspector.schemas.taxon import Confidence, Resolution

if TYPE_CHECKING:
    from dendro_inspector.schemas.evidence import EvidencePacket


class TraceRecorder:
    """Mutable during a run, immutable afterwards."""

    def __init__(self, case_id: str) -> None:
        self._case_id = case_id
        self._events: list[NodeEvent] = []
        self._pending_calls: list[ProviderCallRecord] = []
        self._providers: dict[str, str] = {}
        self._component_projections: tuple[ComponentProjection, ...] = ()
        self._prompt: PromptMetadata | None = None
        self._retries = 0
        self._escalation_triggered = False
        self._escalation_reasons: tuple[str, ...] = ()
        self._arbiter_used = False
        self._started_at = datetime.now(UTC)
        self._started_perf = time.perf_counter()

    def set_prompt_metadata(self, metadata: PromptMetadata) -> None:
        self._prompt = metadata

    def set_provider(self, role: str, adapter: str, model: str | None) -> None:
        self._providers[role] = f"{adapter}:{model}" if model else adapter

    def record_provider_call(self, record: ProviderCallRecord) -> None:
        """Hold a model call until its own node is recorded.

        Claimed by node name rather than by arrival order, because the reviewer fan-out runs
        three nodes concurrently against one recorder. Draining every pending call into
        whichever node finished the bookkeeping first filed all three reviewers' calls under
        the first one and left the other two reading ``calls=0`` beside minutes of wall time.
        """
        self._pending_calls.append(record)

    def record_component_projections(self, evidence: EvidencePacket) -> None:
        """Record canonical identity mappings without prompts, image bytes or prose."""
        grouped: dict[tuple[str, str], list[str]] = {}
        for observation in evidence.observations:
            component_id = observation.source_component_id
            if component_id is None:
                continue
            key = (observation.subject_id, component_id)
            grouped.setdefault(key, []).append(observation.observation_id)
        self._component_projections = tuple(
            ComponentProjection(
                identity_subject_id=identity_id,
                source_component_id=component_id,
                observation_ids=tuple(observation_ids),
            )
            for (identity_id, component_id), observation_ids in grouped.items()
        )

    def _claim_calls(self, node: str) -> tuple[ProviderCallRecord, ...]:
        """Take the pending calls this node made, leaving other nodes' calls alone."""
        claimed = tuple(record for record in self._pending_calls if record.node == node)
        if claimed:
            self._pending_calls = [record for record in self._pending_calls if record.node != node]
        return claimed

    def record_node(
        self,
        node: str,
        *,
        status: NodeStatus = NodeStatus.OK,
        detail: str | None = None,
        duration_ms: float | None = None,
    ) -> None:
        calls = self._claim_calls(node)
        self._events.append(
            NodeEvent(
                node=node,
                status=status,
                sequence=len(self._events),
                detail=detail,
                duration_ms=duration_ms,
                provider_calls=calls,
            )
        )

    def record_retry(self) -> None:
        self._retries += 1

    def record_escalation(self, *, triggered: bool, reasons: tuple[str, ...]) -> None:
        self._escalation_triggered = triggered
        self._escalation_reasons = reasons

    def record_arbiter_used(self) -> None:
        self._arbiter_used = True

    @property
    def retries(self) -> int:
        return self._retries

    def build(
        self,
        *,
        final_resolution: Resolution | None = None,
        final_confidence: Confidence | None = None,
    ) -> RunTrace:
        finished_at = datetime.now(UTC)
        if self._pending_calls:
            # A call whose node never recorded an event would vanish from the trace. That is
            # a wiring bug in the caller, and a silently short provider-call count is exactly
            # the kind of wrong-but-plausible audit trail this module exists to prevent.
            get_logger("trace").warning(
                "unattributed_provider_calls",
                extra={
                    "case_id": self._case_id,
                    "nodes": sorted({record.node for record in self._pending_calls}),
                    "count": len(self._pending_calls),
                },
            )
        return RunTrace(
            case_id=self._case_id,
            graph_version=GRAPH_VERSION,
            domain_prompt=self._prompt,
            providers=dict(self._providers),
            events=tuple(self._events),
            component_projections=self._component_projections,
            retries=self._retries,
            escalation_triggered=self._escalation_triggered,
            escalation_reasons=self._escalation_reasons,
            arbiter_used=self._arbiter_used,
            final_resolution=final_resolution,
            final_confidence=final_confidence,
            started_at=self._started_at,
            finished_at=finished_at,
            duration_ms=(time.perf_counter() - self._started_perf) * 1000.0,
        )


def write_trace(trace: RunTrace, directory: Path) -> Path:
    """Write a trace as JSON and return the path."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{trace.case_id}.trace.json"
    path.write_text(
        json.dumps(trace.model_dump(mode="json"), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return path
