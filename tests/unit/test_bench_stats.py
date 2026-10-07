"""Regression tests for the local latency and cost table writers."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def _load_script(name: str) -> ModuleType:
    path = Path(__file__).parents[2] / "scripts" / "bench" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"bench_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_trace_table_distinguishes_unreported_metrics_from_zero(capsys) -> None:
    trace_stats = _load_script("trace_stats")
    traces = [
        {
            "events": [
                {
                    "node": "planner",
                    "provider_calls": [
                        {
                            "input_tokens": 12,
                            "cached_input_tokens": None,
                            "output_tokens": 0,
                            "reported_cost_usd": None,
                        }
                    ],
                }
            ]
        }
    ]

    trace_stats.token_table(traces)

    row = capsys.readouterr().out.splitlines()[-1]
    assert row.split() == ["planner", "1", "12", "n/a", "0", "n/a"]


def test_bridge_table_distinguishes_unreported_metrics_from_zero(capsys) -> None:
    bridge_stats = _load_script("bridge_stats")
    rows = [
        {
            "route": "local",
            "duration": 1.25,
            "prompt_chars": 100,
            "output_tokens": 0,
            "cost": None,
        }
    ]

    bridge_stats.grouped_table(rows, "route", "per route")

    row = capsys.readouterr().out.splitlines()[-1]
    assert row.split() == ["local", "1", "1.2", "1.2", "100", "0", "n/a"]


def test_trace_table_reports_arbiter_value_per_trigger(capsys) -> None:
    trace_stats = _load_script("trace_stats")
    traces = [
        {
            "escalation_reasons": ["high_confidence_proposed", "bark_only_input"],
            "arbiter_changed_status": False,
            "arbiter_changed_taxon": True,
            "arbiter_changed_resolution": False,
            "arbiter_changed_confidence": False,
        },
        {
            "escalation_reasons": ["high_confidence_proposed"],
            "arbiter_changed_status": False,
            "arbiter_changed_taxon": False,
            "arbiter_changed_resolution": False,
            "arbiter_changed_confidence": False,
        },
        {
            "escalation_reasons": ["high_confidence_proposed"],
            "arbiter_changed_status": None,
            "arbiter_changed_taxon": None,
            "arbiter_changed_resolution": None,
            "arbiter_changed_confidence": None,
        },
    ]

    trace_stats.arbiter_value_table(traces)

    rows = {line.split()[0]: line.split()[1:] for line in capsys.readouterr().out.splitlines()[2:]}
    assert rows["high_confidence_proposed"] == ["2", "1", "50.0%", "0", "1", "0", "0"]
    assert rows["bark_only_input"] == ["1", "1", "100.0%", "0", "1", "0", "0"]
