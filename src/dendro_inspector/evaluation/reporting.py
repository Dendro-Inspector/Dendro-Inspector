"""Evaluation reporting.

Two renderings of the same report: a terse console table for humans and JSON for CI
artifacts. Both show denominators next to rates.
"""

from __future__ import annotations

import json

from dendro_inspector.schemas.evaluation import AssertionOutcome, EvalReport

_MARK = {
    AssertionOutcome.PASS: "PASS",
    AssertionOutcome.FAIL: "FAIL",
    AssertionOutcome.SKIP: "SKIP",
}


def render_text(report: EvalReport, *, verbose: bool = False) -> str:
    """Render a console summary."""
    lines = [
        f"Suite: {report.suite}",
        f"Cases: {report.metrics.cases}   Passed: {report.passed}   Failed: {report.failed}",
        "",
    ]

    for outcome in report.outcomes:
        status = "PASS" if outcome.passed else "FAIL"
        taxon = outcome.selected_taxon or "-"
        resolution = outcome.resolution.value if outcome.resolution else "-"
        confidence = outcome.confidence.value if outcome.confidence else "-"
        arbiter = "  (arbiter)" if outcome.arbiter_used else ""
        retries = f"  retries={outcome.retries}" if outcome.retries else ""
        lines.append(
            f"  [{status}] {outcome.case_id}: "
            f"{taxon} / {resolution} / {confidence}{arbiter}{retries}"
        )
        if outcome.error:
            lines.append(f"        error: {outcome.error}")
        for assertion in outcome.assertions:
            if verbose or assertion.outcome is AssertionOutcome.FAIL:
                detail = f" — {assertion.detail}" if assertion.detail else ""
                lines.append(f"        {_MARK[assertion.outcome]} {assertion.name}{detail}")

    metrics = report.metrics
    rows: tuple[tuple[str, float, int], ...] = (
        ("top-1 accuracy", metrics.top_1_accuracy, metrics.scored_top_1),
        ("top-3 recall", metrics.top_3_recall, metrics.scored_top_3),
        ("correct resolution", metrics.correct_resolution_rate, metrics.scored_resolution),
        ("overconfidence rate", metrics.overconfidence_rate, metrics.cases),
        ("abstention quality", metrics.abstention_quality, metrics.scored_abstention),
        ("schema validity", metrics.schema_validity, metrics.cases),
        ("escalation precision", metrics.escalation_precision, metrics.escalations_observed),
        ("escalation recall", metrics.escalation_recall, metrics.escalations_expected),
        ("unnecessary arbiter calls", metrics.unnecessary_arbiter_call_rate, metrics.cases),
    )
    lines.extend(["", "Metrics (rate [scored]):"])
    lines.extend(f"  {name:<28}{rate}  [{scored}]" for name, rate, scored in rows)
    return "\n".join(lines)


def render_json(report: EvalReport) -> str:
    """Render the report as JSON for CI artifacts."""
    return json.dumps(report.model_dump(mode="json"), indent=2, ensure_ascii=False)
