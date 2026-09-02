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
