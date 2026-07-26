"""Evaluation metrics.

Rates are reported alongside the counts they were computed from. A "100% top-1 accuracy"
over the two cases that happened to declare an expected taxon is not a fact about the
system, and a metric that hides its denominator invites exactly that misreading.
"""

from __future__ import annotations

from dataclasses import dataclass

from evil_duck_dendro.schemas.decisions import DecisionStatus
from evil_duck_dendro.schemas.evaluation import CaseOutcome, EvalCase, EvalMetrics
from evil_duck_dendro.schemas.taxon import Confidence, Resolution, confidence_rank, resolution_rank


@dataclass(frozen=True, slots=True)
class ScoredCase:
    """One case paired with the outcome it produced."""

    case: EvalCase
    outcome: CaseOutcome
    top_3_taxa: frozenset[str]


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def compute(scored: tuple[ScoredCase, ...]) -> EvalMetrics:
    """Compute the suite metrics."""
    top_1_hits = top_1_total = 0
    top_3_hits = top_3_total = 0
    resolution_hits = resolution_total = 0
    overconfident = 0
    abstention_correct = abstention_total = 0
    schema_valid = 0
    escalation_expected = escalation_observed = escalation_correct = 0

    for item in scored:
        expect = item.case.expect
        outcome = item.outcome
        schema_valid += int(outcome.schema_valid)

        if expect.expected_taxon is not None:
            top_1_total += 1
            top_1_hits += int(outcome.selected_taxon == expect.expected_taxon)

        if expect.expected_taxon_in_top_3 is not None:
            top_3_total += 1
            top_3_hits += int(expect.expected_taxon_in_top_3 in item.top_3_taxa)

        if expect.expected_resolution is not None:
            resolution_total += 1
            resolution_hits += int(outcome.resolution is expect.expected_resolution)

        # Overconfidence: a claim narrower or stronger than the case permits. Resolution
        # takes precedence — claiming a species that was capped at genus is the worse error.
        if expect.max_resolution is not None and outcome.resolution is not None:
            overconfident += int(
                resolution_rank(outcome.resolution) > resolution_rank(expect.max_resolution)
            )
        elif expect.max_confidence is not None and outcome.confidence is not None:
            overconfident += int(
                confidence_rank(outcome.confidence) > confidence_rank(expect.max_confidence)
            )

        # Abstention quality: cases that should abstain, and did, without a bare shrug.
        if expect.expected_status is DecisionStatus.INSUFFICIENT_EVIDENCE:
            abstention_total += 1
            abstention_correct += int(
                outcome.resolution in (None, Resolution.UNKNOWN)
                and any(
                    assertion.name == "require_next_photo" and assertion.outcome.value == "pass"
                    for assertion in outcome.assertions
                )
            )

        if expect.require_escalation is True:
            escalation_expected += 1
        if outcome.arbiter_used:
            escalation_observed += 1
        if (
            expect.require_escalation is not None
            and outcome.arbiter_used is expect.require_escalation
        ):
            escalation_correct += 1

    unnecessary = sum(
        1
        for item in scored
        if item.outcome.arbiter_used and item.case.expect.require_escalation is False
    )
    total = len(scored)

    return EvalMetrics(
        cases=total,
        top_1_accuracy=_rate(top_1_hits, top_1_total),
        top_3_recall=_rate(top_3_hits, top_3_total),
        correct_resolution_rate=_rate(resolution_hits, resolution_total),
        overconfidence_rate=_rate(overconfident, total),
        abstention_quality=_rate(abstention_correct, abstention_total),
        schema_validity=_rate(schema_valid, total),
        escalation_precision=_rate(
            sum(
                1
                for item in scored
                if item.outcome.arbiter_used and item.case.expect.require_escalation is True
            ),
            escalation_observed,
        ),
        escalation_recall=_rate(
            sum(
                1
                for item in scored
                if item.outcome.arbiter_used and item.case.expect.require_escalation is True
            ),
            escalation_expected,
        ),
        unnecessary_arbiter_call_rate=_rate(unnecessary, total),
        scored_top_1=top_1_total,
        scored_top_3=top_3_total,
        scored_resolution=resolution_total,
        scored_abstention=abstention_total,
        escalations_expected=escalation_expected,
        escalations_observed=escalation_observed,
        escalation_decisions_correct=escalation_correct,
    )


def confidence_ceiling(metrics: EvalMetrics) -> Confidence:
    """A crude suite-level health read used by the CLI summary."""
    if metrics.overconfidence_rate > 0:
        return Confidence.LOW
    if metrics.schema_validity < 1.0:
        return Confidence.LOW
    return Confidence.MEDIUM
