"""Structured-output failures from the Golden-100 Sonnet 5.5 run (2026-11-10).

Three photographs died at the parser, each after two attempts:

* golden-048 — `image_limitations.0.lighting` / `white_balance` and six `context_limitations`
  entries were sentences ("green cast, so bark colour is unreliable", "location unknown");
* golden-046 — the same lighting prose, plus an image observation with no `image_id`;
* golden-003 — a reviewer's `missing_decisive_features` held "bark.peeling macro".

The fix is not to widen the token pattern until prose passes: the values here are copied
from the archived errors and must still be rejected. What changed is what the model is told
— the rule now travels as a schema description that survives dialects which drop `pattern`,
and the repair prompt names each bad field, the value it got and the rule in words.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from dendro_inspector.observability.events import ProviderCallRecord
from dendro_inspector.observability.trace import TraceRecorder
from dendro_inspector.providers.base import (
    ImageInput,
    StructuredOutputError,
    repair_feedback,
    request_structured,
)
from dendro_inspector.providers.schema_compat import to_anthropic_schema
from dendro_inspector.schemas.base import VALUE_TOKEN_RULE
from dendro_inspector.schemas.candidates import Candidate
from dendro_inspector.schemas.evidence import GeneratedEvidencePacket
from dendro_inspector.schemas.taxon import Resolution

FIXTURE = Path(__file__).resolve().parents[2] / "evals" / "fixtures" / "abstention-visible.json"


def _valid_packet() -> dict[str, Any]:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))["responses"]
    packet: dict[str, Any] = json.loads(json.dumps(payload["primary:evidence_extractor"]))
    return packet


def _golden_048(packet: dict[str, Any]) -> dict[str, Any]:
    """The archived golden-048 values, verbatim."""
    packet["image_limitations"][0]["lighting"] = "diffuse shaded forest light with a green cast"
    packet["image_limitations"][0]["white_balance"] = "green cast, so bark colour is unreliable"
    packet["context_limitations"] = [
        "leaf margin and arrangement unresolved",
        "no fruit, seed or cross-section visible",
        "location unknown",
        "season unknown",
    ]
    return packet


def _golden_046(packet: dict[str, Any]) -> dict[str, Any]:
    packet["image_limitations"][0]["white_balance"] = "strong green cast, bark colour unreliable"
    packet["observations"][0].pop("image_id", None)
    return packet


class TestTheContractIsNotRelaxed:
    def test_golden_048_values_are_still_rejected(self):
        with pytest.raises(ValidationError):
            GeneratedEvidencePacket.model_validate(_golden_048(_valid_packet()))

    def test_golden_003_missing_feature_is_still_rejected(self):
        with pytest.raises(ValidationError):
            Candidate(
                taxon="betula",
                resolution=Resolution.GENUS,
                rank=1,
                missing_decisive_features=("bark.peeling macro",),
            )

    def test_the_repaired_tokens_validate(self):
        packet = _golden_048(_valid_packet())
        packet["image_limitations"][0].update(lighting="dappled", white_balance="green_cast")
        packet["image_limitations"][0]["notes"] = "Green cast; bark colour unreliable."
        packet["context_limitations"] = ["location_unknown", "season_unknown"]
        GeneratedEvidencePacket.model_validate(packet)


class TestTheRuleReachesTheModel:
    """`pattern` is dropped by the Anthropic dialect; the description is not."""

    def test_token_fields_carry_the_rule_in_the_provider_schema(self):
        schema = to_anthropic_schema(GeneratedEvidencePacket.model_json_schema())
        assert schema is not None
        dumped = json.dumps(schema)
        assert '"pattern"' not in dumped
        limitation = schema["$defs"]["ImageLimitation"]["properties"]
        assert "overcast_flat" in limitation["lighting"]["description"]
        assert "neutral" in limitation["white_balance"]["description"]
        assert "location_unknown" in schema["properties"]["context_limitations"]["description"]
        assert VALUE_TOKEN_RULE in dumped

    def test_missing_decisive_features_says_feature_not_photo(self):
        schema = Candidate.model_json_schema()
        description = schema["properties"]["missing_decisive_features"]["description"]
        assert "bark.peeling" in description
        assert "never `bark.peeling macro`" in description


class TestRepairFeedback:
    def _error(self, payload: dict[str, Any]) -> StructuredOutputError:
        try:
            GeneratedEvidencePacket.model_validate(payload)
        except ValidationError as exc:
            wrapped = StructuredOutputError("adapter could not parse the response")
            wrapped.__cause__ = exc
            return wrapped
        raise AssertionError("expected a validation error")

    def test_each_bad_field_is_named_with_its_value_and_rule(self):
        text, codes = repair_feedback(self._error(_golden_048(_valid_packet())))

        assert "`image_limitations.0.lighting` [format]" in text
        assert "'diffuse shaded forest light with a green cast'" in text
        assert "`context_limitations.2` [format]" in text
        assert VALUE_TOKEN_RULE in text
        assert "image_limitations.0.white_balance:string_pattern_mismatch" in codes
        assert "do not invent" in text

    def test_a_missing_image_id_is_reported_as_semantic(self):
        text, codes = repair_feedback(self._error(_golden_046(_valid_packet())))

        assert "[semantic]" in text
        assert "image_id" in text
        assert any(code.endswith(":value_error") for code in codes)

    def test_an_error_without_a_validation_cause_falls_back_to_its_message(self):
        text, codes = repair_feedback(StructuredOutputError("not JSON at all"))
        assert text == "not JSON at all"
        assert codes == ("unparseable:StructuredOutputError",)


class _Recorder(TraceRecorder):
    """Keeps every provider call it is handed, for inspection."""

    def __init__(self, case_id: str) -> None:
        super().__init__(case_id)
        self.provider_calls: list[ProviderCallRecord] = []

    def record_provider_call(self, record: ProviderCallRecord) -> None:
        self.provider_calls.append(record)
        super().record_provider_call(record)


class _ScriptedProvider:
    """Returns the archived bad packet first, then whatever the repair prompt earned."""

    adapter_name = "scripted"
    model = "scripted-model"

    def __init__(self, *responses: dict[str, Any]) -> None:
        self._responses = list(responses)
        self.prompts: list[str] = []

    async def generate_structured(
        self,
        *,
        role: str,
        prompt: str,
        images: Sequence[ImageInput],
        response_model: type[Any],
        metadata: Mapping[str, Any],
    ) -> Any:
        self.prompts.append(prompt)
        payload = self._responses.pop(0)
        try:
            return response_model.model_validate(payload)
        except ValidationError as exc:
            raise StructuredOutputError("unparseable") from exc


def test_golden_048_recovers_on_a_targeted_retry_and_the_failure_is_recorded():
    bad = _golden_048(_valid_packet())
    fixed = _valid_packet()
    provider = _ScriptedProvider(bad, fixed)
    recorder = _Recorder("golden-048")

    result = asyncio.run(
        request_structured(
            provider=provider,
            role="primary",
            node="evidence_extractor",
            prompt="PROMPT",
            images=(),
            response_model=GeneratedEvidencePacket,
            recorder=recorder,
        )
    )

    assert isinstance(result, GeneratedEvidencePacket)
    retry_prompt = provider.prompts[1]
    assert retry_prompt.startswith("PROMPT")
    assert "`image_limitations.0.white_balance` [format]" in retry_prompt
    [call] = recorder.provider_calls
    assert call.attempts == 2
    assert call.validation_failures == 1
    assert "attempt-1:image_limitations.0.lighting:string_pattern_mismatch" in (
        call.validation_errors
    )


def _request(provider: _ScriptedProvider, recorder: TraceRecorder) -> Any:
    return asyncio.run(
        request_structured(
            provider=provider,
            role="primary",
            node="evidence_extractor",
            prompt="PROMPT",
            images=(),
            response_model=GeneratedEvidencePacket,
            recorder=recorder,
        )
    )


def test_a_second_formatting_failure_earns_one_more_targeted_attempt():
    """Cheaper than the whole-case retry golden-003 paid three times over."""
    provider = _ScriptedProvider(
        _golden_048(_valid_packet()), _golden_048(_valid_packet()), _valid_packet()
    )
    recorder = _Recorder("golden-048")

    assert isinstance(_request(provider, recorder), GeneratedEvidencePacket)
    [call] = recorder.provider_calls
    assert call.attempts == 3
    assert call.validation_failures == 2


def test_an_unrepaired_response_still_fails_closed_with_its_errors_recorded():
    """A model that writes the sentence every time must not pass as an uncertain tree."""
    bad = [_golden_048(_valid_packet()) for _ in range(3)]
    provider = _ScriptedProvider(*bad)
    recorder = _Recorder("golden-048")

    with pytest.raises(StructuredOutputError, match="after 3 attempt"):
        _request(provider, recorder)

    [call] = recorder.provider_calls
    assert call.attempts == 3
    assert call.validation_failures == 3
    assert any(code.startswith("attempt-3:") for code in call.validation_errors)


def test_a_semantic_failure_gets_no_extra_attempt():
    """An image observation without its image_id is not a typo the budget should absorb."""
    semantic = _valid_packet()
    semantic["observations"][0].pop("image_id", None)
    provider = _ScriptedProvider(semantic, json.loads(json.dumps(semantic)))
    recorder = _Recorder("golden-046")

    with pytest.raises(StructuredOutputError, match="after 2 attempt"):
        _request(provider, recorder)
    [call] = recorder.provider_calls
    assert call.attempts == 2


def test_a_dropped_length_limit_is_restated_for_the_model():
    """The Sonnet gate retried a reviewer in all nine cases: summaries over 400 characters.

    `maxLength` is dropped by the dialect, so the model never saw the limit it was failing.
    """
    from dendro_inspector.schemas.reviews import ReviewResult

    schema = to_anthropic_schema(ReviewResult.model_json_schema())
    assert schema is not None
    summary = schema["$defs"]["ReviewFinding"]["properties"]["summary"]
    assert "maxLength" not in summary
    assert "at most 400 characters" in summary["description"]


def test_the_short_text_limit_is_stated_in_words_in_the_undialected_schema():
    """The OpenAI adapter sends Pydantic's schema as written, with ``strict: False``.

    ``maxLength`` is then advisory, and an arbiter overran `findings.0.summary` on its
    first attempt in three smoke cases out of three. A description reaches the model on
    every dialect, so the limit has to be in one.
    """
    from dendro_inspector.schemas.base import SHORT_TEXT_MAX
    from dendro_inspector.schemas.reviews import ReviewResult

    summary = ReviewResult.model_json_schema()["$defs"]["ReviewFinding"]["properties"]["summary"]
    assert summary["maxLength"] == SHORT_TEXT_MAX
    assert f"at most {SHORT_TEXT_MAX} characters" in summary["description"]
