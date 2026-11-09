from __future__ import annotations

import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "batch_summary", Path(__file__).parents[2] / "scripts/batch/summarize_run.py"
)
assert spec is not None and spec.loader is not None
summary = importlib.util.module_from_spec(spec)
spec.loader.exec_module(summary)


def record(calls, *, exit_code=0, seconds=1):
    return {
        "exit_code": exit_code,
        "wall_seconds": seconds,
        "result": {
            "response": {"decisions": []},
            "trace": {"events": [{"provider_calls": calls}]},
        },
    }


def test_missing_counters_stay_unknown_and_measured_zero_is_preserved():
    missing = summary.case_row(record([{"input_tokens": 20}]))
    zero = summary.case_row(record([{"input_tokens": 5, "cached_input_tokens": 0}]))
    assert missing["cached_input_tokens"] is None
    assert zero["cached_input_tokens"] == 0
    assert summary.summarize([missing])["tokens"]["cached_input_tokens"] is None
    combined = summary.summarize([missing, zero])
    assert combined["tokens"]["input_tokens"] == 25
    assert combined["tokens"]["cached_input_tokens"] == 0
    assert combined["token_measured_cases"]["cached_input_tokens"] == 1


def test_failed_case_usage_is_included_without_counting_it_as_success():
    rows = [
        summary.case_row(record([{"input_tokens": 10}], seconds=2)),
        summary.case_row(record([{"input_tokens": 30}], exit_code=1, seconds=100)),
    ]
    result = summary.summarize(rows)
    assert result["tokens"]["input_tokens"] == 40
    assert result["succeeded"] == 1
    assert result["failed"] == 1
    assert result["wall_seconds"]["median"] == 2


def test_even_batch_duration_uses_arithmetic_median():
    rows = [summary.case_row(record([], seconds=n)) for n in (1, 3, 7, 20)]
    assert summary.summarize(rows)["wall_seconds"]["median"] == 5
    assert summary.summarize(rows)["tokens"]["input_tokens"] is None
    assert summary.summarize([])["wall_seconds"]["median"] is None
