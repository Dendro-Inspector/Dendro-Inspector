"""The public evaluation suite runs in CI on every pull request, including from forks."""

from __future__ import annotations

import pytest

from evil_duck_dendro.evaluation.reporting import render_json, render_text
from evil_duck_dendro.evaluation.runner import run_suite

pytestmark = pytest.mark.evaluation


@pytest.fixture(scope="module")
def report(repo_root_module):
    return run_suite("public", root=repo_root_module)


@pytest.fixture(scope="module")
def repo_root_module(request):
    return request.config.rootpath


class TestSuiteHealth:
    def test_every_case_passes(self, report):
        failures = [
            f"{outcome.case_id}: "
            + "; ".join(
                f"{a.name} ({a.detail})" for a in outcome.assertions if a.outcome.value == "fail"
            )
            + (f" error={outcome.error}" if outcome.error else "")
            for outcome in report.outcomes
            if not outcome.passed
        ]
        assert report.ok, "\n".join(failures)

    def test_the_suite_covers_every_declared_case(self, report):
        ids = {outcome.case_id for outcome in report.outcomes}
        assert {
            # Core mechanics.
            "conifer-log-001",
            "insufficient-bark-001",
            "mixed-taxa-001",
            "colour-overweighting-001",
            "arbiter-ranking-001",
            # Named failure cases from section 13 of the domain prompt.
            "light-trunk-birch-001",
            "rough-bark-oak-claim-001",
            "edge-foliage-001",
            # The counterweight: when evidence is decisive, commit.
            "apple-with-fruit-001",
        } <= ids

    def test_no_case_crashed(self, report):
        assert [outcome.case_id for outcome in report.outcomes if outcome.error] == []


class TestMetrics:
    def test_nothing_is_overconfident(self, report):
        """The metric this project exists to keep at zero."""
        assert report.metrics.overconfidence_rate == 0.0

    def test_every_response_satisfied_its_schema(self, report):
        assert report.metrics.schema_validity == 1.0

    def test_escalation_fires_when_it_should_and_not_otherwise(self, report):
        assert report.metrics.escalation_recall == 1.0
        assert report.metrics.escalation_precision == 1.0
        assert report.metrics.unnecessary_arbiter_call_rate == 0.0

    def test_abstention_is_useful_not_just_present(self, report):
        """Abstaining only counts when a targeted photo request comes with it."""
        assert report.metrics.abstention_quality == 1.0

    def test_metrics_report_their_denominators(self, report):
        metrics = report.metrics
        assert metrics.cases == len(report.outcomes)
        assert metrics.scored_top_1 > 0
        assert metrics.escalations_expected > 0


class TestReporting:
    def test_text_report_shows_counts_and_metrics(self, report):
        text = render_text(report)
        assert "Suite: public" in text
        assert "overconfidence rate" in text

    def test_verbose_report_shows_passing_assertions(self, report):
        assert len(render_text(report, verbose=True)) > len(render_text(report))

    def test_json_report_round_trips(self, report):
        import json

        payload = json.loads(render_json(report))
        assert payload["suite"] == "public"
        assert len(payload["outcomes"]) == report.metrics.cases
