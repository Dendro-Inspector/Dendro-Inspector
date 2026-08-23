from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest


def _load_module() -> ModuleType:
    script = Path(__file__).parents[2] / "scripts" / "agent-provider" / "run_photo_ledger.py"
    spec = importlib.util.spec_from_file_location("run_photo_ledger", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LEDGER = _load_module()


def _manifest(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "dataset_version": 1,
                "photos": [
                    {
                        "id": "photo-001",
                        "filename": "one.jpg",
                        "sha256": "0" * 64,
                        "capture_group": None,
                        "reference_label": {
                            "status": "unlabelled",
                            "taxon": None,
                            "rank": None,
                            "source": None,
                            "notes": None,
                        },
                        "runs": [],
                        "notes": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def _run(run_id: str = "batch-001") -> dict[str, object]:
    return {
        "run_id": run_id,
        "configuration": "claude_ox_sol_v1",
        "status": "completed",
        "result": {
            "taxon": None,
            "rank": "unknown",
            "confidence": "low",
            "decision": "insufficient_evidence",
        },
        "escalated": False,
        "abstained": True,
        "reviewer_disagreement": False,
        "repair_count": 0,
        "duration_seconds": 1.0,
        "model_calls": 6,
        "estimated_cost_usd": None,
        "trace_path": "runs/batch-001/batch-001.trace.json",
        "notes": [],
    }


def test_append_run_is_append_only_and_rejects_duplicate(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    _manifest(manifest)

    LEDGER.append_run(manifest, photo_id="photo-001", run=_run())
    recorded = LEDGER.load_manifest(manifest)["photos"][0]["runs"]
    assert recorded == [_run()]

    with pytest.raises(LEDGER.LedgerError, match="already exists and is immutable"):
        LEDGER.append_run(manifest, photo_id="photo-001", run=_run())
    assert LEDGER.load_manifest(manifest)["photos"][0]["runs"] == [_run()]


def test_configuration_requires_versioned_stable_id(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    _manifest(manifest)
    run = _run()
    run["configuration"] = "some prose"

    with pytest.raises(LEDGER.LedgerError, match="stable versioned ID"):
        LEDGER.append_run(manifest, photo_id="photo-001", run=run)


def test_markdown_is_a_generated_view(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    _manifest(manifest)
    LEDGER.append_run(manifest, photo_id="photo-001", run=_run())

    rendered = LEDGER.render_markdown(LEDGER.load_manifest(manifest))

    assert "> Generated from manifest.json. Read-only. Do not edit manually." in rendered
    assert "| photo-001 | one.jpg |" in rendered
    assert "| 1 |" in rendered


def test_combined_escalation_reason_does_not_invent_disagreement() -> None:
    trace = {"escalation_reasons": ["reviewers_disagree_or_critical_finding"]}

    assert LEDGER.reviewer_disagreement(trace) is None


def test_repair_count_uses_provider_validation_failures() -> None:
    trace = {
        "retries": 0,
        "events": [
            {"provider_calls": [{"attempts": 2, "validation_failures": 1}]},
            {"provider_calls": [{"attempts": 1, "validation_failures": 0}]},
        ],
    }

    assert LEDGER.structured_repair_count(trace) == 1


def test_photo_selector_preserves_original_ordinal(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    _manifest(manifest_path)
    manifest = LEDGER.load_manifest(manifest_path)

    assert LEDGER.select_photo_records(manifest, "photo-001") == [(1, manifest["photos"][0])]
    with pytest.raises(LEDGER.LedgerError, match="found 0"):
        LEDGER.select_photo_records(manifest, "photo-064")
