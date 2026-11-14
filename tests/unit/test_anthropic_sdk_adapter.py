"""The direct Claude API adapter, against a stand-in client (no network, no SDK needed)."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict, Field

from dendro_inspector.config import DEFAULT_KEY_ENV, Adapter, ProviderConfig
from dendro_inspector.providers.anthropic_sdk_adapter import (
    DEFAULT_MODEL,
    FALLBACK_BETA,
    AnthropicSDKProvider,
    _api_key,
)
from dendro_inspector.providers.base import (
    CACHE_PREFIX_CHARS,
    USAGE_SINK,
    ProviderUnavailableError,
    StructuredOutputError,
    UsageSink,
)
from dendro_inspector.providers.registry import build_provider
from dendro_inspector.providers.schema_compat import to_anthropic_schema


class Verdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    taxon: str = Field(min_length=2, pattern=r"^[a-z]+$")
    confidence: int = Field(ge=0, le=100)
    notes: list[str] = Field(default_factory=list, max_length=5)


class OpenMapping(BaseModel):
    scores: dict[str, int]


class _Messages:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self.response


def _client(response: Any) -> SimpleNamespace:
    messages = _Messages(response)
    return SimpleNamespace(messages=messages, beta=SimpleNamespace(messages=messages))


def _response(text: str, *, stop: str = "end_turn", category: str | None = None) -> Any:
    return SimpleNamespace(
        content=[
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="text", text=text),
        ],
        stop_reason=stop,
        stop_details=SimpleNamespace(category=category) if stop == "refusal" else None,
        usage=SimpleNamespace(
            input_tokens=120,
            cache_read_input_tokens=80,
            cache_creation_input_tokens=300,
            output_tokens=30,
        ),
    )


def _run(
    provider: AnthropicSDKProvider, model: type[BaseModel] = Verdict, /, **metadata: Any
) -> Any:
    return asyncio.run(
        provider.generate_structured(
            role="primary",
            prompt="DOMAIN PROMPT\nnode instructions",
            images=(),
            response_model=model,
            metadata={"node": "planner", **metadata},
        )
    )


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> AnthropicSDKProvider:
    for name in ("ANTHROPIC_EFFORT", "ANTHROPIC_FALLBACKS", "ANTHROPIC_TIMEOUT_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    return AnthropicSDKProvider()


class TestRegistration:
    def test_it_is_a_separate_adapter_from_the_bridge_one(self, tmp_path):
        built = build_provider(
            ProviderConfig(adapter=Adapter.ANTHROPIC_SDK), fixtures_root=tmp_path
        )
        bridge = build_provider(ProviderConfig(adapter=Adapter.ANTHROPIC), fixtures_root=tmp_path)
        assert built.adapter_name == "anthropic-sdk"
        assert bridge.adapter_name == "anthropic"
        assert built.model == DEFAULT_MODEL

    def test_reads_its_own_key_variable_by_default(self):
        assert DEFAULT_KEY_ENV[Adapter.ANTHROPIC_SDK] == "ANTHROPIC_SDK_API_KEY"

    def test_missing_key_is_a_config_error(self, provider, monkeypatch):
        for name in ("ANTHROPIC_SDK_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"):
            monkeypatch.delenv(name, raising=False)
        with pytest.raises(ProviderUnavailableError, match="ANTHROPIC_SDK_API_KEY"):
            _run(provider)

    def test_falls_back_to_the_conventional_key_outside_a_bridge_pod(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_SDK_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-local")
        assert _api_key("ANTHROPIC_SDK_API_KEY") == "sk-ant-local"

    def test_never_takes_the_bridge_placeholder(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_SDK_API_KEY", raising=False)
        monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:8799")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "bridge-local-placeholder")
        assert _api_key("ANTHROPIC_SDK_API_KEY") is None

    def test_its_own_key_wins_inside_a_bridge_pod(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:8799")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "bridge-local-placeholder")
        monkeypatch.setenv("ANTHROPIC_SDK_API_KEY", "sk-ant-real")
        assert _api_key("ANTHROPIC_SDK_API_KEY") == "sk-ant-real"


class TestRequest:
    def test_native_structured_output_effort_and_fallback(self, provider):
        client = _client(_response(json.dumps({"taxon": "pinus", "confidence": 70})))
        provider._client = client
        _run(provider)

        [call] = client.messages.calls
        assert call["model"] == DEFAULT_MODEL
        assert call["output_config"]["effort"] == "high"
        assert call["output_config"]["format"]["type"] == "json_schema"
        assert call["betas"] == [FALLBACK_BETA]
        assert call["fallbacks"] == "default"
        assert "thinking" not in call  # always on for current models; effort is the dial
        text = call["messages"][0]["content"][-1]["text"]
        assert "Required output" not in text  # the schema travels as output_config, not prose

    def test_effort_and_fallbacks_are_configurable(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_EFFORT", "xhigh")
        monkeypatch.setenv("ANTHROPIC_FALLBACKS", "off")
        provider = AnthropicSDKProvider()
        client = _client(_response(json.dumps({"taxon": "pinus", "confidence": 70})))
        provider._client = client
        _run(provider)

        [call] = client.messages.calls
        assert call["output_config"]["effort"] == "xhigh"
        assert "fallbacks" not in call and "betas" not in call

    def test_cache_breakpoint_sits_after_the_domain_prompt(self, provider):
        client = _client(_response(json.dumps({"taxon": "pinus", "confidence": 70})))
        provider._client = client
        _run(provider, **{CACHE_PREFIX_CHARS: len("DOMAIN PROMPT\n")})

        blocks = client.messages.calls[0]["messages"][0]["content"]
        assert blocks[0] == {
            "type": "text",
            "text": "DOMAIN PROMPT\n",
            "cache_control": {"type": "ephemeral"},
        }
        assert blocks[1]["text"] == "node instructions"

    def test_an_inexpressible_schema_falls_back_to_prompt_instructions(self, provider):
        client = _client(_response(json.dumps({"scores": {"pinus": 3}})))
        provider._client = client
        _run(provider, OpenMapping)

        [call] = client.messages.calls
        assert "format" not in call["output_config"]
        assert "Required output" in call["messages"][0]["content"][-1]["text"]


class TestResponse:
    def test_valid_json_is_validated_and_usage_recorded(self, provider):
        provider._client = _client(_response(json.dumps({"taxon": "pinus", "confidence": 70})))
        sink = UsageSink()
        result = _run(provider, **{USAGE_SINK: sink})

        assert result == Verdict(taxon="pinus", confidence=70)
        assert (sink.input_tokens, sink.cached_input_tokens, sink.output_tokens) == (500, 80, 30)
        assert sink.cache_write_input_tokens == 300

    def test_constraints_dropped_from_the_schema_are_still_enforced(self, provider):
        provider._client = _client(_response(json.dumps({"taxon": "Pinus!", "confidence": 70})))
        with pytest.raises(StructuredOutputError, match="unparseable"):
            _run(provider)

    def test_a_refusal_is_a_structured_output_error_naming_the_category(self, provider):
        provider._client = _client(_response("", stop="refusal", category="bio"))
        with pytest.raises(StructuredOutputError, match="category=bio"):
            _run(provider)

    def test_truncated_output_is_reported_as_such(self, provider):
        provider._client = _client(_response('{"taxon": "pin', stop="max_tokens"))
        with pytest.raises(StructuredOutputError, match="output budget"):
            _run(provider)


class TestSchemaTranslation:
    def test_unsupported_constraints_are_dropped_and_objects_closed(self):
        schema = to_anthropic_schema(Verdict.model_json_schema())
        assert schema is not None
        dumped = json.dumps(schema)
        for keyword in ("minLength", "pattern", "minimum", "maximum", "maxItems", "default"):
            assert f'"{keyword}"' not in dumped
        assert schema["additionalProperties"] is False
        assert schema["required"] == ["taxon", "confidence"]

    def test_open_mappings_cannot_be_expressed(self):
        assert to_anthropic_schema(OpenMapping.model_json_schema()) is None


class TestSchemaRejection:
    def test_a_schema_the_api_refuses_falls_back_to_the_prompt(self, provider, monkeypatch):
        """A 400 on the schema must not fail every call for that contract."""

        class FakeBadRequestError(Exception):
            pass

        from dendro_inspector.providers import anthropic_sdk_adapter

        monkeypatch.setattr(
            anthropic_sdk_adapter, "_sdk_errors", lambda: ((), (), (FakeBadRequestError,))
        )
        good = _response(json.dumps({"taxon": "pinus", "confidence": 70}))
        calls: list[dict[str, Any]] = []

        async def create(**kwargs: Any) -> Any:
            calls.append(kwargs)
            if "format" in kwargs["output_config"]:
                raise FakeBadRequestError("schema too complex")
            return good

        messages = SimpleNamespace(create=create)
        provider._client = SimpleNamespace(
            messages=messages, beta=SimpleNamespace(messages=messages)
        )

        assert _run(provider) == Verdict(taxon="pinus", confidence=70)
        assert _run(provider) == Verdict(taxon="pinus", confidence=70)
        # First call: native, refused, then prompt-only. Second call goes prompt-only at once.
        assert ["format" in c["output_config"] for c in calls] == [True, False, False]
