"""The offline archive audit (scripts/eval/audit_archive.py) on archived record shapes."""

from __future__ import annotations

import importlib.util
import json
import sys
import zipfile
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "eval" / "audit_archive.py"


@pytest.fixture(scope="module")
def audit() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_audit_archive", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _birch(*, fixed: bool) -> dict[str, Any]:
    """golden-001 as archived (pre-fix) or as the fixed pipeline now renders it."""
    band = "95–100/100"
    limitation = (
        "Only bark is resolvable. The betula card declares bark.pattern = "
        "white_papery_with_black_marks diagnostic up to genus level."
        if fixed
        else "Nothing above bark level is resolvable. Bark texture ... supports a candidate "
        "but cannot confirm one."
    )
    verdict = f"Identified: Betula (genus), confidence {band}."
    opener = (
        "Assessment. Let us go through it feature by feature."
        if fixed
        else "Assessment. I would not claim 95+ here."
    )
    return {
        "case_id": "golden-001",
        "exit_code": 0,
        "wall_seconds": 140.0,
        "response": {
            "results": [
                {
                    "subject": "standing_tree",
                    "verdict": verdict,
                    "taxonomic_resolution": "genus",
                    "confidence": "high",
                    "confidence_band": band,
                    "ruled_out": ["populus: none recorded"],
                    "limitations": [limitation],
                }
            ],
            "decisions": [
                {
                    "subject_id": "standing_tree",
                    "selected_taxon": "betula",
                    "resolution": "genus",
                    "confidence": "high",
                    "confidence_band": band,
                    "status": "identified",
                    "evidence_tier": 3,
                    "supporting_evidence": ["bark.pattern = white_papery_with_black_marks"],
                }
            ],
            "human_readable": f"{opener}\n\n**Verdict.** {verdict}",
            "tone_mode": "measured" if fixed else "cautious",
        },
    }


_GOLDEN_048_ERROR = """StructuredOutputError: anthropic-sdk returned unusable structured output
GeneratedEvidencePacket
image_limitations.0.lighting
  String should match pattern '^[a-z0-9][a-z0-9_.\\-]*$'
[type=string_pattern_mismatch, input_value='diffuse shaded forest light', input_type=str]
context_limitations.3
  String should match pattern '^[a-z0-9][a-z0-9_.\\-]*$'
[type=string_pattern_mismatch, input_value='location unknown', input_type=str]
"""


def test_the_archived_birch_contradictions_are_counted(audit):
    case = audit.audit_case("archive", _birch(fixed=False), "")

    assert "restraint_voice_with_decisive_band:standing_tree" in case.contradictions
    assert "bark_denial_on_high_confidence:standing_tree" in case.contradictions
    assert "high_from_bark_tier:standing_tree" in case.policy_flags
    assert "high_with_unaddressed_alternative:standing_tree" in case.policy_flags


def test_the_fixed_rendering_has_no_contradiction_but_keeps_its_policy_flags(audit):
    """Flags are for review, not defects: a policy-compliant birch is still worth a look."""
    case = audit.audit_case("archive", _birch(fixed=True), "")

    assert case.contradictions == []
    assert "high_from_bark_tier:standing_tree" in case.policy_flags


def test_a_parser_failure_is_classified_with_its_fields(audit):
    record = {"case_id": "golden-048", "exit_code": 1, "error": _GOLDEN_048_ERROR}
    case = audit.audit_case("archive", record, "")

    assert not case.completed
    assert case.failure_class == "format"
    assert case.failure_fields == (
        "image_limitations.0.lighting:string_pattern_mismatch",
        "context_limitations.3:string_pattern_mismatch",
    )


def test_a_no_claim_verdict_is_not_required_to_carry_a_band(audit):
    record = _birch(fixed=True)
    decision = record["response"]["decisions"][0]
    result = record["response"]["results"][0]
    decision.update(status="insufficient_evidence", confidence="low", confidence_band="<50/100")
    result.update(
        confidence="low",
        confidence_band="<50/100",
        verdict="Insufficient evidence — this photograph cannot honestly carry a taxon.",
    )
    assert audit.audit_case("archive", record, "").contradictions == []


def test_tokens_cost_and_several_runs_of_one_photo_in_a_zip(audit, tmp_path):
    archive = tmp_path / "runs.zip"
    events = {
        "tables": [
            {
                "columns": [
                    {"name": "name"},
                    {"name": "customDimensions"},
                    {"name": "customMeasurements"},
                ],
                "rows": [
                    [
                        "dendro.case_succeeded",
                        json.dumps({"case_id": "golden-001"}),
                        json.dumps(
                            {
                                "input_tokens": 1_000_000,
                                "cached_input_tokens": 0,
                                "output_tokens": 100_000,
                            }
                        ),
                    ]
                ],
            }
        ]
    }
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("run-a/golden-001.json", json.dumps(_birch(fixed=False)))
        handle.writestr("run-b/golden-001.json", json.dumps(_birch(fixed=True)))
        handle.writestr("run-a/events.json", json.dumps(events))

    audits = [audit.audit_case(*item) for item in audit._records([archive])]
    summary = audit.summarise(audits, input_price=2.0, cached_price=0.2, output_price=10.0)

    assert summary["cases"] == 2
    assert summary["cases_with_contradictions"] == 1
    assert summary["estimated_cost_usd"] == 6.0  # both runs read the zip-wide events
