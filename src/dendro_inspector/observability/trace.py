"""Trace recording.

A recorder accumulates events during a run and freezes them into an immutable
:class:`RunTrace`. Recorders are per-run objects, never module-level state.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path

from dendro_inspector.observability.events import (
    GRAPH_VERSION,
    NodeEvent,
    NodeStatus,
    PromptMetadata,
    ProviderCallRecord,
    RunTrace,
)
from dendro_inspector.schemas.taxon import Confidence, Resolution


class TraceRecorder:
    """Mutable during a run, immutable afterwards."""

    def __init__(self, case_id: str) -> None:
        self._case_id = case_id
        self._events: list[NodeEvent] = []
        self._pending_calls: list[ProviderCallRecord] = []
        self._providers: dict[str, str] = {}
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
        """Attach a model call to the node event that is about to be recorded."""
        self._pending_calls.append(record)

    def record_node(
        self,
        node: str,
        *,
        status: NodeStatus = NodeStatus.OK,
        detail: str | None = None,
        duration_ms: float | None = None,
    ) -> None:
        calls = tuple(self._pending_calls)
        self._pending_calls.clear()
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
        return RunTrace(
            case_id=self._case_id,
            graph_version=GRAPH_VERSION,
            domain_prompt=self._prompt,
            providers=dict(self._providers),
            events=tuple(self._events),
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
