"""Shadow failures and proposals must not become production evidence or answers."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
from pydantic import ValidationError

from dendro_inspector.schemas.input import CaseInput, ImageRef
from dendro_inspector.schemas.vision import VisionAssessment
from dendro_inspector.vision_shadow import assess_shadow


def test_hypothesis_cannot_cite_another_subject():
    with pytest.raises(ValidationError, match="foreign observations"):
        VisionAssessment.model_validate(
            {
                "subjects": [
                    {"subject_id": "a", "image_ids": ["img"], "description": "foreground"},
                    {"subject_id": "b", "image_ids": ["img"], "description": "background"},
                ],
                "observations": [
                    {
                        "observation_id": "obs",
                        "subject_id": "b",
                        "image_ids": ["img"],
                        "description": "visible bark",
                    }
                ],
                "hypotheses": [
                    {
                        "subject_id": "a",
                        "taxon": "pinus",
                        "resolution": "genus",
                        "observation_ids": ["obs"],
                    }
                ],
            }
        )


def _context(node_context):
    graph = node_context.config.graph.model_copy(update={"vision_shadow": True})
    return replace(node_context, config=node_context.config.model_copy(update={"graph": graph}))


def test_disabled_shadow_makes_no_call(node_context, monkeypatch):
    async def unexpected(**kwargs):
        raise AssertionError("disabled experiment called a provider")

    monkeypatch.setattr("dendro_inspector.vision_shadow.request_structured", unexpected)
    result = asyncio.run(assess_shadow(CaseInput(case_id="disabled"), node_context))
    assert result.status == "skipped" and result.reason == "disabled"


def test_provider_failure_is_a_record_not_an_exception(node_context, monkeypatch, tmp_path):
    image = tmp_path / "photo.jpg"
    image.write_bytes(b"synthetic-image")
    case = CaseInput(case_id="shadow", images=(ImageRef(image_id="img", path=image),))

    async def failed(**kwargs):
        assert kwargs["max_retries"] == 0
        raise RuntimeError("private diagnostic must not be copied")

    monkeypatch.setattr("dendro_inspector.vision_shadow.request_structured", failed)
    result = asyncio.run(assess_shadow(case, _context(node_context)))
    assert result.status == "failed" and result.reason == "RuntimeError"
    assert "private diagnostic" not in result.model_dump_json()


def test_shadow_receives_only_case_context(node_context, monkeypatch, tmp_path):
    image = tmp_path / "photo.jpg"
    image.write_bytes(b"synthetic-image")
    case = CaseInput(case_id="shadow", images=(ImageRef(image_id="img", path=image),))

    async def record(**kwargs):
        assert "Evidence packet (produced by this system)" not in kwargs["prompt"]
        assert "## Taxon cards" not in kwargs["prompt"]
        assessment = VisionAssessment()
        kwargs["validate_response"](assessment)
        return assessment

    monkeypatch.setattr("dendro_inspector.vision_shadow.request_structured", record)
    result = asyncio.run(assess_shadow(case, _context(node_context)))
    assert result.status == "completed" and result.assessment == VisionAssessment()
