"""Trace recording.

A recorder accumulates events during a run and freezes them into an immutable
:class:`RunTrace`. Recorders are per-run objects, never module-level state.
"""

from __future__ import annotations

import json
import subprocess
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
    ReviewerProjectionRecord,
    RunTrace,
)
from dendro_inspector.observability.logging import get_logger
from dendro_inspector.schemas.decisions import AuthorityCheckStatus
from dendro_inspector.schemas.review_context import ReviewProjection
from dendro_inspector.schemas.taxon import Confidence, Resolution

if TYPE_CHECKING:
    from dendro_inspector.schemas.decisions import AuthorityCheckTrace, FinalDecision
    from dendro_inspector.schemas.evidence import EvidencePacket


def _decision_field_changed(
    before: tuple[FinalDecision, ...],
    after: tuple[FinalDecision, ...],
    field: str,
) -> bool | None:
    if not before:
        return None
    missing = object()
    prior = {decision.subject_id: getattr(decision, field) for decision in before}
    current = {decision.subject_id: getattr(decision, field) for decision in after}
    return any(
        prior.get(subject_id, missing) != current.get(subject_id, missing)
        for subject_id in prior.keys() | current.keys()
    )


class TraceRecorder:
    """Mutable during a run, immutable afterwards."""

    def __init__(self, case_id: str, *, root: Path | None = None) -> None:
        self._case_id = case_id
        self._code_commit_sha, self._code_dirty = _discover_code_revision(root or Path.cwd())
        self._events: list[NodeEvent] = []
        self._pending_calls: list[ProviderCallRecord] = []
        self._pending_review_projections: dict[str, ReviewerProjectionRecord] = {}
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

    def record_review_projection(self, node: str, projection: ReviewProjection) -> None:
        """Record IDs and countable inputs without storing prompt text or private metadata."""
        self._pending_review_projections[node] = ReviewerProjectionRecord(
            reviewer=projection.reviewer.value,
            evidence_ids=projection.evidence_ids,
            image_ids=projection.image_ids,
            candidate_subject_ids=projection.candidate_subject_ids,
            taxon_ids=projection.taxon_ids,
            include_comparison_cards=projection.include_comparison_cards,
            include_regional_pack=projection.include_regional_pack,
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
        projection = self._pending_review_projections.pop(node, None)
        self._events.append(
            NodeEvent(
                node=node,
                status=status,
                sequence=len(self._events),
                detail=detail,
                duration_ms=duration_ms,
                provider_calls=calls,
                reviewer_projection=projection,
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
        pre_correction_decisions: tuple[FinalDecision, ...] = (),
        provisional_decisions: tuple[FinalDecision, ...] = (),
        final_decisions: tuple[FinalDecision, ...] = (),
        authority_checks: tuple[AuthorityCheckTrace, ...] = (),
        concurrent_nodes: tuple[str, ...] = (),
    ) -> RunTrace:
        finished_at = datetime.now(UTC)
        if self._pending_review_projections:
            # Same reasoning as unattributed provider calls below: a projection built for a
            # node that never recorded an event would leave the audit trail claiming the
            # reviewer saw the full state.
            get_logger("trace").warning(
                "unattributed_review_projections",
                extra={
                    "case_id": self._case_id,
                    "nodes": sorted(self._pending_review_projections),
                },
            )
            self._pending_review_projections.clear()
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
        correction_changes = {
            field: _decision_field_changed(
                pre_correction_decisions,
                final_decisions,
                field,
            )
            for field in ("status", "selected_taxon", "resolution", "confidence")
        }
        changed_values = tuple(value for value in correction_changes.values() if value is not None)
        arbiter_changes = {
            field: (
                _decision_field_changed(provisional_decisions, final_decisions, field)
                if self._arbiter_used
                else None
            )
            for field in ("status", "selected_taxon", "resolution", "confidence")
        }
        return RunTrace(
            case_id=self._case_id,
            graph_version=GRAPH_VERSION,
            code_commit_sha=self._code_commit_sha,
            code_dirty=self._code_dirty,
            domain_prompt=self._prompt,
            providers=dict(self._providers),
            events=tuple(self._events),
            component_projections=self._component_projections,
            retries=self._retries,
            graph_retry_count=self._retries,
            correction_changed_outcome=(any(changed_values) if changed_values else None),
            correction_changed_status=correction_changes["status"],
            correction_changed_taxon=correction_changes["selected_taxon"],
            correction_changed_resolution=correction_changes["resolution"],
            correction_changed_confidence=correction_changes["confidence"],
            provisional_decisions=provisional_decisions,
            arbiter_changed_status=arbiter_changes["status"],
            arbiter_changed_taxon=arbiter_changes["selected_taxon"],
            arbiter_changed_resolution=arbiter_changes["resolution"],
            arbiter_changed_confidence=arbiter_changes["confidence"],
            authority_checks=authority_checks,
            evidence_authority_sensitive=any(
                check.status is AuthorityCheckStatus.SENSITIVE for check in authority_checks
            ),
            escalation_triggered=self._escalation_triggered,
            escalation_reasons=self._escalation_reasons,
            arbiter_used=self._arbiter_used,
            final_resolution=final_resolution,
            final_confidence=final_confidence,
            started_at=self._started_at,
            finished_at=finished_at,
            duration_ms=(time.perf_counter() - self._started_perf) * 1000.0,
            critical_path_ms=_critical_path_ms(tuple(self._events), frozenset(concurrent_nodes)),
        )


def _critical_path_ms(events: tuple[NodeEvent, ...], concurrent: frozenset[str]) -> float | None:
    """Serial node time plus the slowest member of each fan-out round.

    Rounds are runs of consecutive events from ``concurrent``, so a retry that fans out a
    second time is counted twice — it really did happen twice. The caller names the
    concurrent nodes because this module deliberately knows nothing about the graph; with
    no names supplied every node is serial, which is the honest reading of "not told".
    """
    if not events:
        return None
    total = 0.0
    round_max = 0.0
    in_round = False
    for event in events:
        duration = event.duration_ms or 0.0
        if event.node in concurrent:
            in_round = True
            round_max = max(round_max, duration)
            continue
        if in_round:
            total += round_max
            round_max = 0.0
            in_round = False
        total += duration
    return total + round_max if in_round else total


def _discover_code_revision(root: Path) -> tuple[str | None, bool | None]:
    """Return the repository commit and dirty state without making Git a runtime dependency.

    Release versions identify the deterministic policy. The VCS revision distinguishes two
    experiment builds made between releases, while ``dirty`` prevents an uncommitted patch
    from borrowing the identity of its parent commit. Installed wheels and source archives
    legitimately have no ``.git`` directory, so inability to discover either value is
    represented explicitly rather than guessed.
    """

    def git(*arguments: str) -> subprocess.CompletedProcess[str] | None:
        try:
            return subprocess.run(
                ("git", "-C", str(root), *arguments),
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return None

    revision = git("rev-parse", "--verify", "HEAD")
    if revision is None or revision.returncode != 0:
        return None, None
    commit_sha = revision.stdout.strip().lower()
    if not (40 <= len(commit_sha) <= 64) or any(
        character not in "0123456789abcdef" for character in commit_sha
    ):
        return None, None

    status = git("status", "--porcelain=v1", "--untracked-files=normal")
    dirty = None if status is None or status.returncode != 0 else bool(status.stdout.strip())
    return commit_sha, dirty


def write_trace(trace: RunTrace, directory: Path) -> Path:
    """Write a trace as JSON and return the path."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{trace.case_id}.trace.json"
    path.write_text(
        json.dumps(trace.model_dump(mode="json"), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return path
