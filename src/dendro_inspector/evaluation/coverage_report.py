"""``dendro coverage-audit``: stored run records against today's knowledge and rules.

For each record: every observation's coverage status (:mod:`knowledge.coverage_audit`),
every proposal's admission outcome with its exact reason, and optionally a replay of the
recorded evidence through today's deterministic decision (:mod:`evaluation.trace_replay`).

Labels, when given, are compared at genus. They are reported per record *and* per distinct
case, because several runs of one photograph are one sample: five photographs through four
model configurations are five samples, not twenty.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from dendro_inspector.config import AppConfig
from dendro_inspector.evaluation.trace_replay import (
    StoredRecord,
    load_record,
    packet_from_trace,
    proposals_from_trace,
    replay,
    replay_fidelity,
)
from dendro_inspector.knowledge.coverage_audit import CoverageAudit, audit_packet
from dendro_inspector.knowledge.loader import KnowledgeBase
from dendro_inspector.schemas.candidates import CandidateSet
from dendro_inspector.schemas.taxon import Resolution


@dataclass(frozen=True, slots=True)
class DecisionRow:
    subject_id: str
    taxon: str | None
    genus: str | None
    resolution: str
    confidence: str
    status: str


@dataclass(frozen=True, slots=True)
class RecordReport:
    case_id: str
    source: str
    audit: CoverageAudit
    fidelity_mismatches: tuple[str, ...]
    stored: tuple[DecisionRow, ...]
    replayed: tuple[DecisionRow, ...] = ()
    label: str | None = None


def load_labels(path: Path | None) -> dict[str, str]:
    """``{case_id: genus}``, from either a flat mapping or ``{"labels": {id: {"genus": g}}}``."""
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    labels = payload.get("labels", payload) if isinstance(payload, dict) else {}
    return {
        str(case): str(value.get("genus") if isinstance(value, dict) else value).lower()
        for case, value in labels.items()
    }


def _genus(knowledge: KnowledgeBase, taxon: str | None, resolution: str) -> str | None:
    """The genus a named answer commits to, or ``None`` when it names no genus."""
    if taxon is None or resolution not in ("genus", "species"):
        return None
    for taxon_id in knowledge.available_taxon_ids():
        card = knowledge.try_taxon(taxon_id)
        if card is None:
            continue
        identities = (card.native_identity, *card.broader_identities)
        if any(identity.taxon_id == taxon for identity in identities):
            genus = card.identity_at_or_broader(Resolution.GENUS)
            return genus.taxon_id if genus is not None else None
    return taxon


def _stored_rows(record: StoredRecord, knowledge: KnowledgeBase) -> tuple[DecisionRow, ...]:
    rows = []
    for decision in record.decisions:
        taxon = decision.get("selected_taxon")
        resolution = str(decision.get("resolution") or "unknown")
        rows.append(
            DecisionRow(
                str(decision.get("subject_id")),
                taxon,
                _genus(knowledge, taxon, resolution),
                resolution,
                str(decision.get("confidence") or "low"),
                str(decision.get("status") or ""),
            )
        )
    return tuple(rows)


def _proposals(record: StoredRecord) -> tuple[CandidateSet, ...]:
    return tuple(
        CandidateSet.model_validate(proposal) for proposal in proposals_from_trace(record.trace)
    )


def collect_paths(paths: Iterable[Path]) -> tuple[Path, ...]:
    found: list[Path] = []
    for path in paths:
        found.extend(sorted(path.rglob("*.json")) if path.is_dir() else [path])
    return tuple(found)


async def build_reports(
    paths: Iterable[Path],
    knowledge: KnowledgeBase,
    *,
    config: AppConfig,
    root: Path,
    labels: Mapping[str, str] | None = None,
    with_replay: bool = False,
) -> tuple[RecordReport, ...]:
    reports = []
    for path in collect_paths(paths):
        try:
            record = load_record(path)
        except (ValueError, UnicodeDecodeError):
            continue
        if record is None:
            continue
        packet = packet_from_trace(record.trace)
        replayed: tuple[DecisionRow, ...] = ()
        if with_replay:
            state = await replay(record, config=config, root=root)
            replayed = tuple(
                DecisionRow(
                    decision.subject_id,
                    decision.selected_taxon,
                    _genus(knowledge, decision.selected_taxon, decision.resolution.value),
                    decision.resolution.value,
                    decision.confidence.value,
                    decision.status.value,
                )
                for decision in state.decisions
            )
        reports.append(
            RecordReport(
                case_id=record.case_id,
                source=record.source,
                audit=audit_packet(packet, knowledge, _proposals(record)),
                fidelity_mismatches=replay_fidelity(record),
                stored=_stored_rows(record, knowledge),
                replayed=replayed,
                label=(labels or {}).get(record.case_id),
            )
        )
    return tuple(reports)


def _leading(rows: tuple[DecisionRow, ...]) -> DecisionRow | None:
    """The first named subject, else the first subject: the answer a reader sees first."""
    named = [row for row in rows if row.genus is not None]
    return named[0] if named else (rows[0] if rows else None)


def _grade(row: DecisionRow | None, label: str) -> str:
    if row is None or row.genus is None:
        return "abstained"
    return "correct" if row.genus == label else "wrong"


def summarise(reports: tuple[RecordReport, ...]) -> dict[str, Any]:
    """Totals, with label outcomes per record and per distinct case kept apart."""
    statuses: Counter[str] = Counter()
    uncovered: Counter[str] = Counter()
    outcomes: Counter[str] = Counter()
    rejected: Counter[str] = Counter()
    for report in reports:
        for observation in report.audit.observations:
            statuses[observation.status.value] += 1
            if observation.status.value == "valid_but_uncovered":
                uncovered[f"{observation.feature}={observation.value}"] += 1
        for candidate in report.audit.candidates:
            outcomes[candidate.outcome.value] += 1
            for _, reason in candidate.dropped:
                rejected[reason.split(":", 1)[0]] += 1

    def grades(key: str) -> dict[str, Any]:
        per_record: Counter[str] = Counter()
        per_case: dict[str, Counter[str]] = {}
        for report in reports:
            if report.label is None:
                continue
            rows = report.stored if key == "stored" else report.replayed
            if key == "replayed" and not rows:
                continue
            grade = _grade(_leading(rows), report.label)
            per_record[grade] += 1
            per_case.setdefault(report.case_id, Counter())[grade] += 1
        return {
            "records": dict(per_record),
            "distinct_cases": len(per_case),
            "cases_with_any_wrong_genus": sum(1 for c in per_case.values() if c["wrong"]),
            "cases_correct_on_every_record": sum(
                1
                for c in per_case.values()
                if c["correct"] and not c["wrong"] and not c["abstained"]
            ),
        }

    total = sum(statuses.values()) or 1
    return {
        "records": len(reports),
        "observations": sum(statuses.values()),
        "observation_status": dict(statuses),
        "supported_ratio": round(statuses["valid_and_supported"] / total, 3),
        "candidate_outcomes": dict(outcomes),
        "dropped_support_reasons": dict(rejected),
        "top_uncovered": uncovered.most_common(15),
        "labelled_stored": grades("stored"),
        "labelled_replayed": grades("replayed"),
        "replay_fidelity_mismatches": sum(1 for r in reports if r.fidelity_mismatches),
    }


def render_text(reports: tuple[RecordReport, ...]) -> str:
    lines = []
    for report in reports:
        counts = Counter(o.status.value for o in report.audit.observations)
        lines.append(f"{report.case_id}  [{report.source}]")
        lines.append("  observations: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
        for observation in report.audit.observations:
            if observation.status.value != "valid_and_supported":
                lines.append(
                    f"    {observation.observation_id} {observation.feature}={observation.value}"
                    f": {observation.status.value} ({observation.reason})"
                )
            elif len(observation.supporting_genera) > 1:
                lines.append(
                    f"    {observation.observation_id} {observation.feature}={observation.value}"
                    f": shared by {', '.join(observation.supporting_genera)} - discriminates none"
                )
        for candidate in report.audit.candidates:
            reason = candidate.detail or "; ".join(f"{i} {r}" for i, r in candidate.dropped)
            lines.append(f"  {candidate.taxon}: {candidate.outcome.value} {reason}".rstrip())
        for label, rows in (("live", report.stored), ("replay", report.replayed)):
            for row in rows:
                lines.append(
                    f"  {label}: {row.subject_id} -> {row.taxon or '-'} / {row.resolution} / "
                    f"{row.confidence} / {row.status}"
                )
        if report.label:
            lines.append(f"  label genus: {report.label}")
        if report.fidelity_mismatches:
            lines.append(f"  replay fidelity: trust differs for {report.fidelity_mismatches}")
    lines.append(json.dumps(summarise(reports), indent=1, ensure_ascii=False))
    return "\n".join(lines)


def render_json(reports: tuple[RecordReport, ...]) -> str:
    return json.dumps(
        {"summary": summarise(reports), "records": [asdict(report) for report in reports]},
        indent=1,
        ensure_ascii=False,
        default=str,
    )
