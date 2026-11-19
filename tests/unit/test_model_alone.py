"""Arm M (the model alone): its answers become stored cases every reader and scorer can use."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dendro_inspector.evaluation import model_alone
from dendro_inspector.evaluation.label_scoring import Outcome, ReferenceLabel
from dendro_inspector.providers.base import UsageSink


def subject(**extra):
    base: dict[str, object] = {
        "subject": "the tree",
        "genus": "Quercus",
        "species": None,
        "family": "Fagaceae",
        "confidence": 80,
        "cannot_determine_reason": None,
        "evidence": [],
    }
    return {**base, **extra}


def answer(*subjects):
    return model_alone.ModelAloneAnswer.model_validate({"subjects": list(subjects) or [subject()]})


def stored(parsed):
    usage = UsageSink()
    usage.record(input_tokens=1000, output_tokens=50)
    return model_alone.record(
        answer=parsed,
        usage=usage,
        wall_seconds=2.5,
        adapter="fake",
        model="m",
        unit_id="u1",
        photos=[{"name": "a.jpg", "sha256": "0" * 64}],
        field_context=True,
        domain_prompt_sha256="d" * 64,
        image_max_edge_px=8192,
        code_commit_sha="c" * 40,
        started_at="2026-11-19T20:00:00+00:00",
    )


def test_the_prompt_is_the_domain_prompt_then_the_request():
    composed = model_alone.compose_prompt("DOMAIN", field_context=False)
    assert composed.startswith("DOMAIN" + model_alone.PROMPT_SEPARATOR + model_alone.REQUEST)
    assert model_alone.FIELD_CONTEXT not in composed
    assert model_alone.compose_prompt("DOMAIN", field_context=True).endswith(
        model_alone.FIELD_CONTEXT
    )


def test_cannot_be_determined_is_an_explicit_null_not_a_missing_field():
    assert answer(subject(genus=None, family=None)).subjects[0].genus is None
    missing = subject()
    del missing["genus"]
    with pytest.raises(ValidationError):
        answer(missing)


@pytest.mark.parametrize("name", ["Quercus robur", "quercus!", "", "Q"])
def test_a_genus_is_one_latin_word(name):
    with pytest.raises(ValidationError):
        answer(subject(genus=name))


def test_answers_become_decisions_at_the_rank_they_commit_to():
    found = model_alone.decisions(
        answer(
            subject(species="robur"),
            subject(genus=None),
            subject(genus=None, family=None, confidence=60),
        )
    )
    assert [(d["selected_taxon"], d["resolution"]) for d in found] == [
        ("quercus_robur", "species"),
        ("fagaceae", "family"),
        (None, "unknown"),
    ]
    assert [d["confidence"] for d in found] == ["medium", "medium", "low"]
    assert found[2]["abstained"] is True


def test_a_record_reads_like_a_stored_case():
    record = stored(answer())
    assert record["exit_code"] == 0
    assert record["models"] == {"primary": "fake:m"}
    call = record["result"]["trace"]["events"][0]["provider_calls"][0]
    assert (call["input_tokens"], call["output_tokens"]) == (1000, 50)
    arm = record["result"]["model_alone"]
    assert arm["request_sha256"] == model_alone.REQUEST_SHA256
    assert arm["schema_sha256"] == model_alone.SCHEMA_SHA256
    assert record["result"]["response"]["decisions"][0]["selected_taxon"] == "quercus"


def test_a_failed_call_is_a_failed_case_without_a_response():
    record = model_alone.record(
        answer=None,
        usage=None,
        wall_seconds=0.0,
        adapter="fake",
        model="m",
        unit_id="u1",
        photos=[],
        field_context=False,
        domain_prompt_sha256="d" * 64,
        image_max_edge_px=None,
        code_commit_sha=None,
        started_at="2026-11-19T20:00:00+00:00",
        error="boom",
    )
    assert record["exit_code"] == 1 and record["error"] == "boom"
    assert "response" not in record["failure"]
    assert model_alone.subject_answers(record) == ()


def test_grading_uses_the_graph_scorer(knowledge):
    oak = ReferenceLabel("quercus")
    assert model_alone.grade(stored(answer()), oak, knowledge).outcome is Outcome.CORRECT
    beech = stored(answer(subject(genus="Fagus")))
    assert model_alone.grade(beech, oak, knowledge).outcome is Outcome.WRONG
    family_only = stored(answer(subject(genus=None)))
    assert model_alone.grade(family_only, oak, knowledge).outcome is Outcome.BROADER_ONLY
    nothing = stored(answer(subject(genus=None, family=None)))
    assert model_alone.grade(nothing, oak, knowledge).outcome is Outcome.NO_NAME
    indeterminate = ReferenceLabel(None, "fagaceae")
    assert model_alone.grade(beech, indeterminate, knowledge).outcome is Outcome.UNSUPPORTED_GENUS
