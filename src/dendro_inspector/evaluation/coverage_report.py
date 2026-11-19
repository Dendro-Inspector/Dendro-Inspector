"""``dendro coverage-audit``: stored run records against today's knowledge and rules.

For each record: every observation's coverage status (:mod:`knowledge.coverage_audit`),
every proposal's admission outcome with its exact reason, and optionally a replay of the
recorded evidence through today's deterministic decision (:mod:`evaluation.trace_replay`).

Labels, when given, are graded at genus by :mod:`evaluation.label_scoring`: only a record's
target subjects are graded, and a record whose target is not known is reported as unscoped
instead of being graded on whichever subject was named first. Grades are reported per
record *and* per distinct case, because several runs of one photograph are one sample: five
photographs through four model configurations are five samples, not twenty.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from dendro_inspector.config import AppConfig
from dendro_inspector.evaluation.label_scoring import (
    Grade,
    Outcome,
    ReferenceLabel,
    SubjectAnswer,
    grade_record,
    parse_names,
    summarise_grades,
)
from dendro_inspector.evaluation.trace_replay import (
    ReplaySettings,
    ReplayUnavailableError,
    ReviewReplay,
    StoredRecord,
    load_record,
    packet_from_trace,
    proposals_from_trace,
    replay,
    replay_fidelity,
    replay_gaps,
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
    #: The labelled genus, and the full reference it came from.
    label: str | None = None
    reference: ReferenceLabel | None = None
    stored_grade: Grade | None = None
    #: None when the record was not replayed, or could not be.
    replayed_grade: Grade | None = None
    #: How the replay answered for the reviewers; None when the record was not replayed.
    replay_reviews: str | None = None
    #: What the replay reconstructed or went without, as ``what: why`` entries.
    replay_gaps: tuple[str, ...] = ()
    #: The trace lacks what the requested replay needs, so no replayed verdict is given.
    replay_unavailable: bool = False
    #: Why, as the replay stated it; None when the record replayed or was not replayed.
    replay_unavailable_reason: str | None = None
    #: Whose settings the replay ran under; None when the record was not replayed.
    replay_settings: str | None = None
    #: The replay's last review plan, when it ran with adaptive routing on: the route, and
    #: the reviewer models that plan did not call (or, in shadow mode, would not have).
    replay_route: str | None = None
    replay_route_applied: bool = False
    replay_skipped_reviewers: tuple[str, ...] = ()


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


def _family(knowledge: KnowledgeBase, genus: str) -> str | None:
    """The family the genus card places it in, for a label that does not state one."""
    card = knowledge.try_taxon(genus)
    family = card.identity_at_or_broader(Resolution.FAMILY) if card is not None else None
    if family is None or family.resolution is not Resolution.FAMILY:
        return None
    return family.taxon_id


def _reference(knowledge: KnowledgeBase, label: ReferenceLabel | None) -> ReferenceLabel | None:
    if label is None or label.family is not None or label.genus is None:
        return label
    return replace(label, family=_family(knowledge, label.genus))


def _answers(rows: tuple[DecisionRow, ...]) -> tuple[SubjectAnswer, ...]:
    """Each verdict as the grader reads it: a genus, a broader name, or nothing."""
    answers = []
    for row in rows:
        genera: tuple[str, ...] = ()
        broader: tuple[str, ...] = ()
        if row.genus is not None:
            genera, broader = parse_names(row.genus)
        elif row.taxon and row.resolution != "unknown":
            broader = (row.taxon.lower(),)
        answers.append(SubjectAnswer(row.subject_id, genera, broader))
    return tuple(answers)


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
    labels: Mapping[str, ReferenceLabel] | None = None,
    with_replay: bool = False,
    reviews: ReviewReplay = "neutral",
    settings: ReplaySettings = "configured",
    graph_overrides: Mapping[str, object] | None = None,
) -> tuple[RecordReport, ...]:
    """One report per stored record; ``reviews`` chooses how a replay answers for reviewers.

    ``settings`` chooses whose escalation and graph settings each replay runs under: the
    given ``config``, or the record's own with ``graph_overrides`` applied on top.

    A record whose trace cannot support the requested replay is reported as such, with no
    replayed verdict. It is never replayed another way instead: a neutral verdict filed
    under ``recorded`` would be counted as what review did to the claim.
    """
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
        unavailable = False
        reason: str | None = None
        plan = None
        if with_replay:
            try:
                state = await replay(
                    record,
                    config=config,
                    root=root,
                    reviews=reviews,
                    settings=settings,
                    graph_overrides=graph_overrides,
                )
            except ReplayUnavailableError as exc:
                unavailable = True
                reason = str(exc)
            else:
                plan = state.routing_assessment
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
        stored = _stored_rows(record, knowledge)
        reference = _reference(knowledge, (labels or {}).get(record.case_id))
        reports.append(
            RecordReport(
                case_id=record.case_id,
                source=record.source,
                audit=audit_packet(packet, knowledge, _proposals(record)),
                fidelity_mismatches=replay_fidelity(record),
                stored=stored,
                replayed=replayed,
                label=reference.genus if reference is not None else None,
                reference=reference,
                stored_grade=(
                    grade_record(_answers(stored), reference) if reference is not None else None
                ),
                replayed_grade=(
                    grade_record(_answers(replayed), reference)
                    if reference is not None and with_replay and not unavailable
                    else None
                ),
                replay_reviews=reviews if with_replay else None,
                replay_gaps=replay_gaps(record.trace, reviews) if with_replay else (),
                replay_unavailable=unavailable,
                replay_unavailable_reason=reason,
                replay_settings=settings if with_replay else None,
                replay_route=plan.route.value if plan is not None else None,
                replay_route_applied=plan is not None and plan.applied,
                replay_skipped_reviewers=(
                    tuple(reviewer.value for reviewer in plan.skipped_reviewers)
                    if plan is not None
                    else ()
                ),
            )
        )
    return tuple(reports)


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
        legacy: Counter[str] = Counter()
        per_case: dict[str, Counter[str]] = {}
        graded: list[tuple[str, Grade, ReferenceLabel]] = []
        for report in reports:
            grade = report.stored_grade if key == "stored" else report.replayed_grade
            if grade is None or report.reference is None:
                continue
            graded.append((report.case_id, grade, report.reference))
            per_record[grade.outcome.value] += 1
            legacy[
                grade.outcome.value
                if grade.outcome
                in (
                    Outcome.CORRECT,
                    Outcome.WRONG,
                    Outcome.UNSUPPORTED_GENUS,
                    Outcome.UNSUPPORTED_ALTERNATIVES,
                )
                else "abstained"
            ] += 1
            per_case.setdefault(report.case_id, Counter())[grade.outcome.value] += 1
        return {
            # Keep the former coarse counters for existing report readers. New readers
            # should use outcomes: family, ambiguous and unscoped are not abstentions.
            "records": dict(legacy),
            "outcomes": dict(per_record),
            "distinct_cases": len(per_case),
            "cases_with_any_wrong_genus": sum(
                1 for c in per_case.values() if c[Outcome.WRONG.value]
            ),
            "cases_correct_on_every_record": sum(
                1 for c in per_case.values() if c[Outcome.CORRECT.value] == sum(c.values())
            ),
            "grading": summarise_grades(graded),
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
        "labelled_movements": [
            {
                "case_id": report.case_id,
                "source": report.source,
                "stored": report.stored_grade.outcome.value,
                "replayed": report.replayed_grade.outcome.value,
            }
            for report in reports
            if report.stored_grade is not None and report.replayed_grade is not None
        ],
        "replay_fidelity_mismatches": sum(1 for r in reports if r.fidelity_mismatches),
        "replay_unavailable": sum(1 for r in reports if r.replay_unavailable),
        # The first clause of each reason, so one cause repeated across records reads as one.
        "replay_unavailable_reasons": dict(
            Counter(
                r.replay_unavailable_reason.split(";", 1)[0]
                for r in reports
                if r.replay_unavailable_reason
            )
        ),
        # Empty unless the replay ran with adaptive routing on. Calls are counted as
        # skipped only where the plan was applied; in shadow mode none were.
        "replay_routes": dict(Counter(r.replay_route for r in reports if r.replay_route)),
        "replay_reviewer_calls_skipped": sum(
            len(r.replay_skipped_reviewers) for r in reports if r.replay_route_applied
        ),
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
        elif report.reference is not None:
            lines.append("  label genus: indeterminate")
        for label, grade in (("live", report.stored_grade), ("replay", report.replayed_grade)):
            if grade is not None:
                possible = " or ".join(outcome.value for outcome in grade.possible_outcomes)
                lines.append(
                    f"  {label} grade: {grade.outcome.value}"
                    + (f" ({possible})" if possible else "")
                    + f" [{grade.scope.value}]"
                )
                if grade.missing_target_ids:
                    lines.append("    missing targets: " + ", ".join(grade.missing_target_ids))
                for subject, genus in grade.context_names:
                    lines.append(f"    context: {subject} -> {genus}")
        if report.fidelity_mismatches:
            lines.append(f"  replay fidelity: trust differs for {report.fidelity_mismatches}")
        if report.replay_unavailable:
            lines.append(f"  replay: not available with {report.replay_reviews} reviews")
            if report.replay_unavailable_reason:
                lines.append(f"  replay unavailable because: {report.replay_unavailable_reason}")
        if report.replay_route is not None:
            skipped = ", ".join(report.replay_skipped_reviewers) or "none"
            mode = "applied" if report.replay_route_applied else "shadow"
            lines.append(
                f"  replay route: {report.replay_route} ({mode}; reviewer models not called: "
                f"{skipped})"
            )
        lines.extend(f"  replay gap: {gap}" for gap in report.replay_gaps)
    lines.append(json.dumps(summarise(reports), indent=1, ensure_ascii=False))
    return "\n".join(lines)


def render_json(reports: tuple[RecordReport, ...]) -> str:
    return json.dumps(
        {"summary": summarise(reports), "records": [asdict(report) for report in reports]},
        indent=1,
        ensure_ascii=False,
        default=str,
    )
