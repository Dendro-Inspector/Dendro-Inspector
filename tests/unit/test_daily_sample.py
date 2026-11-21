"""The daily OpenAI sample: which units it starts, under the day's token allowance.

2026-11-21: the owner asked for a scheduled AKS job with a hard stop on the token limits,
running random photographs of the known data set every day to see how results change.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from dendro_inspector.providers.daily_budget import LIMITS, remaining, reserve

REPO = Path(__file__).resolve().parents[2]


def _sample() -> ModuleType:
    path = REPO / "scripts" / "batch" / "run_daily_sample.py"
    spec = importlib.util.spec_from_file_location("_daily_sample", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


daily = _sample()


def _unit(unit_id: str, photos: int) -> dict[str, Any]:
    return {
        "unit_id": unit_id,
        "genus": "pinus",
        "photos": [{"file": f"{unit_id}/{n}.jpg", "sha256": "0" * 64} for n in range(photos)],
    }


def test_the_default_plan_names_only_the_allowed_models():
    plan = daily.parse_plan(None)
    assert [step.model for step in plan] == ["gpt-5.4", "gpt-5.4-mini"]
    with pytest.raises(ValueError, match="not an allowed model"):
        daily.parse_plan(json.dumps([{"model": "gpt-5.5", "tag": "x"}]))


def test_order_is_random_but_fixed_by_day_and_model():
    units = [_unit(f"u{n}", 3) for n in range(20)]
    first = [u["unit_id"] for u in daily.unit_order(units, "20261122", "gpt-5.4-mini")]
    again = [u["unit_id"] for u in daily.unit_order(units, "20261122", "gpt-5.4-mini")]
    other = [u["unit_id"] for u in daily.unit_order(units, "20261123", "gpt-5.4-mini")]
    assert first == again and first != other and sorted(first) == sorted(other)


def test_estimate_grows_with_photos_and_calls():
    assert daily.estimate(_unit("a", 3), 6) == 6 * (20_000 + 3 * 2_000)
    assert daily.estimate(_unit("a", 12), 12) > daily.estimate(_unit("a", 3), 12)
    # One single-pass gpt-5.4 unit of three photos fits the day; twelve photos do not.
    assert daily.estimate(_unit("a", 3), 6) <= LIMITS["gpt-5.4"]
    assert daily.estimate(_unit("a", 12), 6) > LIMITS["gpt-5.4"]


def test_run_ids_fit_the_control_api():
    run_id = daily.unit_run_id("20261122", "m", "Beech-Firewood-C_long_name_beyond_the_limit")
    assert run_id.startswith("od20261122-m-beech-firewood-c")
    assert len(run_id) <= 41 and run_id == run_id.lower() and not run_id.endswith("-")


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        (
            {"exit_code": 0, "result": {"response": {"decisions": [{"selected_taxon": "pinus"}]}}},
            "correct",
        ),
        (
            {
                "exit_code": 0,
                "result": {"response": {"decisions": [{"selected_taxon": "juglans_regia"}]}},
            },
            "other_name",
        ),
        (
            {"exit_code": 0, "result": {"response": {"decisions": [{"selected_taxon": None}]}}},
            "no_name",
        ),
        ({"exit_code": 1, "error": "Daily token limit reached for gpt-5.4"}, "stopped_by_cap"),
        ({"exit_code": 1, "error": "something else"}, "failed"),
    ],
)
def test_outcome_is_a_rough_genus_check(record, expected):
    assert daily.outcome(record, "pinus")[0] == expected


def _dataset(tmp_path: Path, sizes: dict[str, int]) -> Path:
    dataset = tmp_path / "_datasets" / "known-units"
    units = []
    for unit_id, photos in sizes.items():
        files = []
        for n in range(photos):
            data = f"{unit_id}-{n}".encode()
            path = dataset / unit_id / f"{n}.jpg"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            files.append({"file": f"{unit_id}/{n}.jpg", "sha256": hashlib.sha256(data).hexdigest()})
        units.append({"unit_id": unit_id, "genus": "pinus", "field_context": True, "photos": files})
    (dataset / "units.json").write_text(json.dumps(units), encoding="utf-8")
    return dataset


def test_only_units_that_fit_the_remaining_allowance_start(tmp_path, monkeypatch):
    _dataset(tmp_path, {"small": 3, "large": 12})
    ledger = tmp_path / "usage.sqlite"
    # gpt-5.4: 100,000 left, below either unit's single-pass estimate; mini: one small unit
    # plus the 500,000 kept for the day's own inspections.
    reserve(ledger, "gpt-5.4", LIMITS["gpt-5.4"] - 100_000)
    small_mini = daily.estimate({"photos": [0] * 3}, 12)
    reserve(ledger, "gpt-5.4-mini", LIMITS["gpt-5.4-mini"] - 500_000 - small_mini)
    started: list[tuple[str, str]] = []

    def fake_run(results, dataset, unit, step, run_id):
        started.append((step.model, unit["unit_id"]))
        reserve(ledger, step.model, daily.estimate(unit, step.calls))
        return {
            "run_id": run_id,
            "unit_id": unit["unit_id"],
            "model": step.model,
            "outcome": "correct",
            "names": ["pinus"],
            "tokens": 1,
        }

    monkeypatch.setattr(daily, "run_unit", fake_run)
    env = {
        "RESULTS_DIR": str(tmp_path),
        "RUN_ID": "od20261122",
        "DENDRO_OPENAI_BUDGET_DB": str(ledger),
    }
    assert daily.main(env) == 0
    assert started == [("gpt-5.4-mini", "small")]
    summary = json.loads((tmp_path / "od20261122" / "daily.json").read_text())
    assert [s["units"] for s in summary["stops"]] == [0, 1]
    assert remaining(ledger, "gpt-5.4-mini") == 500_000
    history = (tmp_path / "_daily" / "history.jsonl").read_text().splitlines()
    assert [json.loads(line)["unit_id"] for line in history] == ["small"]


def test_no_ledger_no_run(tmp_path):
    _dataset(tmp_path, {"small": 3})
    with pytest.raises(KeyError):
        daily.main({"RESULTS_DIR": str(tmp_path), "RUN_ID": "od20261122"})


def test_a_unit_is_staged_with_checked_photos(tmp_path):
    dataset = _dataset(tmp_path, {"small": 3})
    unit = daily.load_units(dataset)[0]
    daily.stage(dataset, unit, tmp_path / "od20261122-m-small")
    manifest = json.loads(
        (tmp_path / "od20261122-m-small" / "inputs" / "manifest.json").read_text()
    )
    assert manifest["case_id"] == "small" and manifest["field_context"] is True
    assert [p["file_name"] for p in manifest["photos"]] == [
        "photo-1.jpg",
        "photo-2.jpg",
        "photo-3.jpg",
    ]
    unit["photos"][0]["sha256"] = "f" * 64
    with pytest.raises(ValueError, match="SHA-256"):
        daily.stage(dataset, unit, tmp_path / "again")


def test_remaining_counts_used_and_reserved_tokens(tmp_path):
    ledger = tmp_path / "usage.sqlite"
    assert remaining(ledger, "gpt-5.4") == LIMITS["gpt-5.4"]
    reserve(ledger, "gpt-5.4", 100_000)
    assert remaining(ledger, "gpt-5.4") == LIMITS["gpt-5.4"] - 100_000
    assert remaining(ledger, "gpt-5.4-mini") == LIMITS["gpt-5.4-mini"]
