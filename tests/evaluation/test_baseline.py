"""Regression guard against a frozen evaluation baseline.

The suite passing is not the same as the suite behaving the same way. A change can keep all
all cases green while quietly moving a genus, spending an arbiter call it did not need, or
raising a confidence nobody authorised. The baseline catches that drift.

It is deliberately not a golden-file equality check: metrics that should only improve are
compared directionally, and per-case *decisions* are compared exactly, because a changed
verdict is always worth a human looking at it.

To adopt a new baseline after an intentional change:

    dendro eval --suite public --json --out evals/baselines/public-v<next>.json

then trim it to this file's shape and say why in CHANGELOG.md.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from dendro_inspector.evaluation.runner import run_suite

pytestmark = pytest.mark.evaluation

BASELINE = Path("evals/baselines/public-v0.7.0.json")

#: Metrics that must never get worse. True means higher is better.
_DIRECTION: dict[str, bool] = {
    "top_1_accuracy": True,
    "top_3_recall": True,
    "correct_resolution_rate": True,
    "abstention_quality": True,
    "schema_validity": True,
    "escalation_precision": True,
    "escalation_recall": True,
    "overconfidence_rate": False,
    "unnecessary_arbiter_call_rate": False,
}


@pytest.fixture(scope="module")
def baseline(request) -> dict[str, Any]:
    path = request.config.rootpath / BASELINE
    loaded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return loaded


@pytest.fixture(scope="module")
def current(request):
    return run_suite("public", root=request.config.rootpath)


def test_the_baseline_artifact_is_present(baseline):
    assert baseline["baseline_version"] == "0.7.0"
    assert len(baseline["cases"]) == 19


def test_no_case_disappeared(baseline, current):
    """Deleting a failing case is not a way to make the suite green."""
    observed = {outcome.case_id for outcome in current.outcomes}
    missing = sorted(set(baseline["cases"]) - observed)
    assert missing == [], f"cases present in the baseline but no longer in the suite: {missing}"


@pytest.mark.parametrize("metric", sorted(_DIRECTION))
def test_metrics_do_not_regress(baseline, current, metric):
    expected = baseline["metrics"][metric]
    actual = getattr(current.metrics, metric)
    if _DIRECTION[metric]:
        assert actual >= expected, f"{metric} fell from {expected} to {actual}"
    else:
        assert actual <= expected, f"{metric} rose from {expected} to {actual}"


def test_no_decision_changed_silently(baseline, current):
    """A different verdict may well be an improvement — but never an unnoticed one."""
    drift: list[str] = []
    by_id = {outcome.case_id: outcome for outcome in current.outcomes}

    for case_id, frozen in baseline["cases"].items():
        outcome = by_id.get(case_id)
        if outcome is None:
            continue
        observed = {
            "selected_taxon": outcome.selected_taxon,
            "selected_taxon_display_name": outcome.selected_taxon_display_name,
            "resolution": outcome.resolution.value if outcome.resolution else None,
            "confidence": outcome.confidence.value if outcome.confidence else None,
            "evidence_tier": outcome.evidence_tier,
            "admitted_rerank_finding_ids": list(outcome.admitted_rerank_finding_ids),
            "arbiter_used": outcome.arbiter_used,
            "retries": outcome.retries,
        }
        for field, expected in frozen.items():
            if observed[field] != expected:
                drift.append(f"{case_id}.{field}: {expected!r} -> {observed[field]!r}")

    assert drift == [], (
        "decisions moved against the frozen baseline:\n  "
        + "\n  ".join(drift)
        + f"\nIf intended, refresh {BASELINE.as_posix()} and record the change in CHANGELOG.md."
    )
