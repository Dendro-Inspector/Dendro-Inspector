"""Provider boundary: structured-output repair, failure classification, and no live calls."""

from __future__ import annotations

import asyncio
import io
import itertools
import json
import logging
import urllib.error
import urllib.request
from typing import Any

import pytest

from dendro_inspector.config import Adapter, AppConfig, ProviderConfig, Role, load_config
from dendro_inspector.observability.trace import TraceRecorder
from dendro_inspector.providers.base import (
    OUTPUT_EVIDENCE_IDS,
    OUTPUT_SUBJECT_IDS,
    USAGE_SINK,
    ProviderError,
    ProviderUnavailableError,
    StructuredOutputError,
    UsageSink,
    request_structured,
)
from dendro_inspector.providers.fake import (
    FakeModelProvider,
    ScenarioNotFoundError,
    UnscriptedCallError,
)
from dendro_inspector.providers.gemini_adapter import GeminiProvider
from dendro_inspector.providers.ollama_adapter import OllamaProvider
from dendro_inspector.providers.openrouter_adapter import OpenRouterProvider
from dendro_inspector.providers.registry import ProviderRegistry, build_provider
from dendro_inspector.schemas.candidates import CandidateProposal
from dendro_inspector.schemas.evidence import EvidencePacket
from dendro_inspector.schemas.reviews import Reviewer, ReviewResult, ReviewStatus


@pytest.fixture
def fixtures_root(repo_root):
    return repo_root / "evals" / "fixtures"


class _FakeResponse:
    """Minimal stand-in for the context manager `urlopen` returns."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


class TestFakeProvider:
    def test_it_is_deterministic(self, fixtures_root, simple_case, run_scenario):
        first = run_scenario(simple_case, "primary-pass")
        second = run_scenario(simple_case, "primary-pass")
        assert first.state.decisions == second.state.decisions

    def test_unknown_scenario_fails_with_the_available_list(self, fixtures_root):
        with pytest.raises(ScenarioNotFoundError, match="Available:"):
            FakeModelProvider(scenario="no-such-scenario", fixtures_root=fixtures_root)

    def test_unscripted_call_fails_loudly_rather_than_improvising(self, fixtures_root):
        """A fake that quietly invents data is worse than no fake at all."""
        provider = FakeModelProvider(scenario="primary-insufficient", fixtures_root=fixtures_root)
        with pytest.raises(UnscriptedCallError, match="no scripted response"):
            asyncio.run(
                provider.generate_structured(
                    role="primary",
                    prompt="",
                    images=(),
                    response_model=EvidencePacket,
                    metadata={"node": "candidate_generator"},
                )
            )


class TestStructuredRepair:
    def test_malformed_output_is_repaired_on_one_retry(self, fixtures_root):
        provider = FakeModelProvider(scenario="malformed-retry", fixtures_root=fixtures_root)
        recorder = TraceRecorder("t")
        packet = asyncio.run(
            request_structured(
                provider=provider,
                role="primary",
                node="evidence_extractor",
                prompt="original prompt",
                images=(),
                response_model=EvidencePacket,
                recorder=recorder,
                max_retries=1,
            )
        )
        assert packet.subjects
        assert provider.calls == ["primary:evidence_extractor"] * 2

    def test_exhausting_the_repair_budget_raises_rather_than_degrading(self, fixtures_root):
        """A broken model must never masquerade as an uncertain tree."""
        provider = FakeModelProvider(scenario="malformed-retry", fixtures_root=fixtures_root)
        with pytest.raises(StructuredOutputError, match="unusable structured output"):
            asyncio.run(
                request_structured(
                    provider=provider,
                    role="primary",
                    node="evidence_extractor",
                    prompt="p",
                    images=(),
                    response_model=EvidencePacket,
                    max_retries=0,
                )
            )

    def test_repair_attempt_is_recorded_in_the_trace(self, fixtures_root):
        provider = FakeModelProvider(scenario="malformed-retry", fixtures_root=fixtures_root)
        recorder = TraceRecorder("t")
        asyncio.run(
            request_structured(
                provider=provider,
                role="primary",
                node="evidence_extractor",
                prompt="p",
                images=(),
                response_model=EvidencePacket,
                recorder=recorder,
                max_retries=1,
            )
        )
        recorder.record_node("evidence_extractor")
        trace = recorder.build()
        call = trace.events[0].provider_calls[0]
        assert call.attempts == 2
        assert call.validation_failures == 1

    def test_the_graph_survives_a_malformed_extraction(self, simple_case, run_scenario):
        result = run_scenario(simple_case, "malformed-retry")
        assert result.state.decisions[0].selected_taxon == "pinus"


class TestRegistry:
    def test_roles_resolve_to_the_configured_adapters(self, config, repo_root):
        registry = ProviderRegistry.from_config(config, root=repo_root)
        described = registry.describe()
        assert set(described) == {"primary", "reviewer", "arbiter"}
        assert all(value.startswith("fake") for value in described.values())

    def test_reviewer_falls_back_to_primary_for_released_two_role_configs(self):
        primary = ProviderConfig(adapter=Adapter.FAKE, scenario="primary-pass")
        config = AppConfig(
            providers={
                Role.PRIMARY: primary,
                Role.ARBITER: ProviderConfig(adapter=Adapter.FAKE, scenario="primary-pass"),
            }
        )

        assert config.provider_for(Role.REVIEWER) is primary

    def test_environment_can_bind_all_three_roles_independently(self, monkeypatch):
        monkeypatch.setenv("DENDRO_PRIMARY_PROVIDER", "anthropic")
        monkeypatch.setenv("DENDRO_PRIMARY_MODEL", "claude-main")
        monkeypatch.setenv("DENDRO_REVIEWER_PROVIDER", "openrouter")
        monkeypatch.setenv("DENDRO_REVIEWER_MODEL", "ox-factory")
        monkeypatch.setenv("DENDRO_ARBITER_PROVIDER", "openai")
        monkeypatch.setenv("DENDRO_ARBITER_MODEL", "sol-judge")

        config = load_config()

        assert config.provider_for(Role.PRIMARY).model == "claude-main"
        assert config.provider_for(Role.REVIEWER).model == "ox-factory"
        assert config.provider_for(Role.ARBITER).model == "sol-judge"

    def test_describe_never_leaks_a_credential(self, config, repo_root):
        described = ProviderRegistry.from_config(config, root=repo_root).describe()
        joined = " ".join(described.values()).lower()
        assert "key" not in joined
        assert "sk-" not in joined

    def test_vendor_adapters_fail_clearly_without_a_credential(self, fixtures_root, monkeypatch):
        """Selecting a real adapter with no key is a config error, not a runtime surprise."""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        provider = build_provider(
            ProviderConfig(adapter=Adapter.OPENAI, api_key_env="OPENAI_API_KEY"),
            fixtures_root=fixtures_root,
        )
        with pytest.raises(ProviderUnavailableError, match="OPENAI_API_KEY"):
            asyncio.run(
                provider.generate_structured(
                    role="primary",
                    prompt="p",
                    images=(),
                    response_model=EvidencePacket,
                    metadata={"node": "planner"},
                )
            )

    def test_ollama_adapter_fails_clearly_when_unreachable(self, fixtures_root, monkeypatch):
        """No credential to check, but an unreachable server is still a config error."""
        monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:1")
        provider = build_provider(
            ProviderConfig(adapter=Adapter.OLLAMA),
            fixtures_root=fixtures_root,
        )
        with pytest.raises(ProviderUnavailableError, match="could not reach Ollama"):
            asyncio.run(
                provider.generate_structured(
                    role="primary",
                    prompt="p",
                    images=(),
                    response_model=EvidencePacket,
                    metadata={"node": "planner"},
                )
            )

    def test_gemini_reads_its_own_credential_not_the_role_default(self, fixtures_root, monkeypatch):
        """`arbiter` bound to Gemini must not go looking for ANTHROPIC_API_KEY."""
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        provider = build_provider(
            ProviderConfig(adapter=Adapter.GEMINI),
            fixtures_root=fixtures_root,
        )
        with pytest.raises(ProviderUnavailableError, match="GEMINI_API_KEY"):
            asyncio.run(
                provider.generate_structured(
                    role="arbiter",
                    prompt="p",
                    images=(),
                    response_model=EvidencePacket,
                    metadata={"node": "planner"},
                )
            )

    def test_gemini_waits_the_delay_the_api_asks_for_then_succeeds(self, monkeypatch):
        """A per-minute cap is transient: honour the server's own retry delay."""
        monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
        slept: list[float] = []
        provider = GeminiProvider(model="m", rate_limit_retries=3, sleep=slept.append)
        calls = itertools.count()

        def _urlopen(*args, **kwargs):
            if next(calls) == 0:
                raise urllib.error.HTTPError(
                    url="https://example.invalid",
                    code=429,
                    msg="Too Many Requests",
                    hdrs=None,  # type: ignore[arg-type]
                    fp=io.BytesIO(
                        b'{"error":{"code":429,"message":"rate limited","details":'
                        b'[{"@type":"type.googleapis.com/google.rpc.RetryInfo",'
                        b'"retryDelay":"7s"}]}}'
                    ),
                )
            packet = EvidencePacket().model_dump_json()
            return _FakeResponse(
                json.dumps({"candidates": [{"content": {"parts": [{"text": packet}]}}]}).encode()
            )

        monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
        result = asyncio.run(
            provider.generate_structured(
                role="primary",
                prompt="p",
                images=(),
                response_model=EvidencePacket,
                metadata={"node": "planner"},
            )
        )
        assert isinstance(result, EvidencePacket)
        assert slept == [7.0]

    def test_gemini_retries_a_peer_connection_reset_then_succeeds(self, monkeypatch):
        """A transient Windows HTTPS reset must not abort the full review fan-out."""
        monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
        slept: list[float] = []
        provider = GeminiProvider(model="m", rate_limit_retries=1, sleep=slept.append)
        calls = itertools.count()

        def _urlopen(*args, **kwargs):
            if next(calls) == 0:
                raise ConnectionResetError(10054, "connection reset by peer")
            packet = EvidencePacket().model_dump_json()
            return _FakeResponse(
                json.dumps({"candidates": [{"content": {"parts": [{"text": packet}]}}]}).encode()
            )

        monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
        result = asyncio.run(
            provider.generate_structured(
                role="primary",
                prompt="p",
                images=(),
                response_model=EvidencePacket,
                metadata={"node": "planner"},
            )
        )
        assert isinstance(result, EvidencePacket)
        assert slept == [5.0]

    def test_gemini_binds_reviewer_subject_ids_to_code_owned_values(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
        request_payload: dict[str, object] = {}
        provider = GeminiProvider(model="m")

        def _urlopen(request, **kwargs):
            del kwargs
            request_payload.update(json.loads(request.data))
            review = ReviewResult(
                reviewer=Reviewer.CONFUSION,
                status=ReviewStatus.PASS,
                subject_id="standing_tree_1",
            )
            return _FakeResponse(
                json.dumps(
                    {"candidates": [{"content": {"parts": [{"text": review.model_dump_json()}]}}]}
                ).encode()
            )

        monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
        result = asyncio.run(
            provider.generate_structured(
                role="reviewer",
                prompt="p",
                images=(),
                response_model=ReviewResult,
                metadata={"node": "confusion_reviewer", OUTPUT_SUBJECT_IDS: ("standing_tree_1",)},
            )
        )

        assert result.subject_id == "standing_tree_1"
        generation = request_payload["generationConfig"]
        assert isinstance(generation, dict)
        schema = generation["responseSchema"]
        assert isinstance(schema, dict)
        assert schema["properties"]["subject_id"]["enum"] == ["standing_tree_1"]
        finding = schema["properties"]["findings"]["items"]
        assert finding["properties"]["subject_id"]["enum"] == ["standing_tree_1"]

    @pytest.mark.parametrize(
        ("message", "expected"),
        [
            (b"Please retry in 7s.", 7.0),
            # A nearly-clear rolling window reports milliseconds; read as seconds this
            # would sleep 115s for a 0.1s wait.
            (b"Please retry in 115.787057ms.", 0.115787057),
            (b"no delay stated", 5.0),
        ],
    )
    def test_gemini_reads_the_unit_on_the_retry_delay(self, monkeypatch, message, expected):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
        slept: list[float] = []
        provider = GeminiProvider(model="m", rate_limit_retries=1, sleep=slept.append)

        def _raise(*args, **kwargs):
            raise urllib.error.HTTPError(
                url="https://example.invalid",
                code=429,
                msg="Too Many Requests",
                hdrs=None,  # type: ignore[arg-type]
                fp=io.BytesIO(b'{"error":{"message":"' + message + b'"}}'),
            )

        monkeypatch.setattr(urllib.request, "urlopen", _raise)
        with pytest.raises(ProviderError):
            asyncio.run(
                provider.generate_structured(
                    role="primary",
                    prompt="p",
                    images=(),
                    response_model=EvidencePacket,
                    metadata={"node": "planner"},
                )
            )
        assert slept == [pytest.approx(expected)]

    def test_gemini_does_not_sleep_on_a_quota_that_waiting_cannot_clear(self, monkeypatch):
        """`limit: 0` is a billing state — retrying three times just delays the error."""
        monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
        slept: list[float] = []
        provider = GeminiProvider(model="m", rate_limit_retries=3, sleep=slept.append)

        def _raise(*args, **kwargs):
            raise urllib.error.HTTPError(
                url="https://example.invalid",
                code=429,
                msg="Too Many Requests",
                hdrs=None,  # type: ignore[arg-type]
                fp=io.BytesIO(b'{"error":{"message":"Quota exceeded, limit: 0, model: pro"}}'),
            )

        monkeypatch.setattr(urllib.request, "urlopen", _raise)
        with pytest.raises(ProviderError, match="limit: 0"):
            asyncio.run(
                provider.generate_structured(
                    role="primary",
                    prompt="p",
                    images=(),
                    response_model=EvidencePacket,
                    metadata={"node": "planner"},
                )
            )
        assert slept == []

    def test_openrouter_requires_private_parameter_compatible_routing(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-not-real")
        monkeypatch.delenv("OPENROUTER_DATA_COLLECTION", raising=False)
        request_payload: dict[str, object] = {}
        provider = OpenRouterProvider(model="google/gemma-4-31b-it:free")

        def _urlopen(request, **kwargs):
            del kwargs
            request_payload.update(json.loads(request.data))
            return _FakeResponse(
                json.dumps(
                    {"choices": [{"message": {"content": EvidencePacket().model_dump_json()}}]}
                ).encode()
            )

        monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
        asyncio.run(
            provider.generate_structured(
                role="primary",
                prompt="p",
                images=(),
                response_model=EvidencePacket,
                metadata={"node": "evidence_extractor"},
            )
        )

        assert request_payload["provider"] == {
            "data_collection": "deny",
            "require_parameters": True,
        }
        assert request_payload["response_format"] == {"type": "json_object"}
        prompt = request_payload["messages"][0]["content"][0]["text"]  # type: ignore[index]
        assert "## Required output" in prompt
        assert '"additionalProperties":false' in prompt

    def test_openrouter_data_collection_requires_explicit_opt_in(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-not-real")
        monkeypatch.setenv("OPENROUTER_DATA_COLLECTION", "allow")
        request_payload: dict[str, object] = {}
        provider = OpenRouterProvider(model="google/gemma-4-31b-it:free")

        def _urlopen(request, **kwargs):
            del kwargs
            request_payload.update(json.loads(request.data))
            return _FakeResponse(
                json.dumps(
                    {"choices": [{"message": {"content": EvidencePacket().model_dump_json()}}]}
                ).encode()
            )

        monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
        asyncio.run(
            provider.generate_structured(
                role="primary",
                prompt="p",
                images=(),
                response_model=EvidencePacket,
                metadata={"node": "evidence_extractor"},
            )
        )

        assert request_payload["provider"] == {
            "data_collection": "allow",
            "require_parameters": True,
        }

    def test_gemini_names_the_quota_rather_than_the_credential_on_429(self, monkeypatch):
        """A 429 is not a bad key — saying so would send the reader to rotate a good one."""
        monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
        provider = GeminiProvider(model="gemini-3.6-flash")

        def _raise(*args, **kwargs):
            raise urllib.error.HTTPError(
                url="https://generativelanguage.googleapis.com/v1beta",
                code=429,
                msg="Too Many Requests",
                hdrs=None,  # type: ignore[arg-type]
                fp=io.BytesIO(b'{"error":{"message":"You exceeded your current quota"}}'),
            )

        monkeypatch.setattr(urllib.request, "urlopen", _raise)
        with pytest.raises(ProviderError, match="quota") as caught:
            asyncio.run(
                provider.generate_structured(
                    role="primary",
                    prompt="p",
                    images=(),
                    response_model=EvidencePacket,
                    metadata={"node": "planner"},
                )
            )
        assert not isinstance(caught.value, ProviderUnavailableError)

    def test_ollama_http_error_is_not_reported_as_an_unreachable_server(self, monkeypatch):
        """A server that answers 404 needs the model pulled, not restarting."""
        provider = OllamaProvider(model="not-pulled:1b")

        def _raise(*args, **kwargs):
            raise urllib.error.HTTPError(
                url="http://localhost:11434/api/chat",
                code=404,
                msg="Not Found",
                hdrs=None,  # type: ignore[arg-type]
                fp=io.BytesIO(b'{"error":"model \'not-pulled:1b\' not found"}'),
            )

        monkeypatch.setattr(urllib.request, "urlopen", _raise)
        with pytest.raises(ProviderUnavailableError, match=r"ollama pull not-pulled:1b"):
            asyncio.run(
                provider.generate_structured(
                    role="primary",
                    prompt="p",
                    images=(),
                    response_model=EvidencePacket,
                    metadata={"node": "planner"},
                )
            )


class TestVertexRoute:
    """The same protocol on Google's other host, which authenticates differently.

    Vertex / Agent Platform is the route a Cloud billing account and its credits apply to.
    It speaks the body and the response shape the Generative Language API does, so only the
    credential, the thinking control and the token accounting differ — and every one of
    those is a way to be quietly wrong rather than loudly broken.
    """

    endpoint = "https://aiplatform.googleapis.com/v1/projects/p/locations/global/publishers/google"

    @staticmethod
    def _answer(**usage_metadata: object) -> bytes:
        payload: dict[str, object] = {
            "candidates": [{"content": {"parts": [{"text": EvidencePacket().model_dump_json()}]}}]
        }
        if usage_metadata:
            payload["usageMetadata"] = usage_metadata
        return json.dumps(payload).encode()

    def _run(self, provider, metadata=None):
        return asyncio.run(
            provider.generate_structured(
                role="primary",
                prompt="p",
                images=(),
                response_model=EvidencePacket,
                metadata=metadata if metadata is not None else {"node": "planner"},
            )
        )

    def test_a_bearer_token_replaces_the_api_key_header(self, monkeypatch):
        """Vertex rejects `x-goog-api-key`; sending both would spend the key for nothing."""
        monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
        monkeypatch.setenv("GOOGLE_ACCESS_TOKEN", "test-token-not-real")
        provider = GeminiProvider(model="m", endpoint=self.endpoint)
        seen: dict[str, str] = {}

        def _urlopen(request, **kwargs):
            del kwargs
            seen.update(request.headers)
            return _FakeResponse(self._answer())

        monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
        self._run(provider)

        assert seen["Authorization"] == "Bearer test-token-not-real"
        assert not any(key.lower() == "x-goog-api-key" for key in seen)

    def test_a_vertex_endpoint_without_a_token_fails_before_the_request(self, monkeypatch):
        """Falling back to the API key here buys a 401 that names the wrong problem."""
        monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
        monkeypatch.delenv("GOOGLE_ACCESS_TOKEN", raising=False)
        provider = GeminiProvider(model="m", endpoint=self.endpoint)

        def _urlopen(*args, **kwargs):
            raise AssertionError("no request may leave without a usable credential")

        monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
        with pytest.raises(ProviderUnavailableError, match="GOOGLE_ACCESS_TOKEN"):
            self._run(provider)

    def test_the_api_key_still_serves_the_generative_language_host(self, monkeypatch):
        """Adding the Vertex route must not move the default one."""
        monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
        monkeypatch.delenv("GOOGLE_ACCESS_TOKEN", raising=False)
        provider = GeminiProvider(model="m")
        seen: dict[str, str] = {}

        def _urlopen(request, **kwargs):
            del kwargs
            seen.update(request.headers)
            return _FakeResponse(self._answer())

        monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
        self._run(provider)

        assert seen["X-goog-api-key"] == "test-key-not-real"
        assert "Authorization" not in seen

    def test_the_thinking_level_is_sent_only_when_one_is_configured(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_ACCESS_TOKEN", "test-token-not-real")
        monkeypatch.delenv("GEMINI_THINKING_LEVEL", raising=False)
        bodies: list[dict[str, Any]] = []

        def _urlopen(request, **kwargs):
            del kwargs
            bodies.append(json.loads(request.data))
            return _FakeResponse(self._answer())

        monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
        self._run(GeminiProvider(model="m", endpoint=self.endpoint))
        self._run(GeminiProvider(model="m", endpoint=self.endpoint, thinking_level="HIGH"))

        assert "thinkingConfig" not in bodies[0]["generationConfig"]
        assert bodies[1]["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "HIGH"}

    def test_an_unknown_thinking_level_is_left_for_the_api_to_reject(self, monkeypatch):
        """The API answers 400 naming the field. A local allow-list would only rot."""
        monkeypatch.setenv("GOOGLE_ACCESS_TOKEN", "test-token-not-real")
        provider = GeminiProvider(model="m", endpoint=self.endpoint, thinking_level="BOGUS")
        sent: list[dict[str, Any]] = []

        def _urlopen(request, **kwargs):
            del kwargs
            sent.append(json.loads(request.data))
            raise urllib.error.HTTPError(
                url=self.endpoint,
                code=400,
                msg="Bad Request",
                hdrs=None,  # type: ignore[arg-type]
                fp=io.BytesIO(
                    b'{"error":{"message":"Invalid value at '
                    b'generation_config.thinking_config.thinking_level"}}'
                ),
            )

        monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
        with pytest.raises(ProviderError, match="thinking_level"):
            self._run(provider)
        assert sent[0]["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "BOGUS"}

    def test_thinking_tokens_are_counted_as_the_output_they_are_billed_as(self, monkeypatch):
        """Measured on a live call: 1048 thinking tokens beside 36 of answer.

        Recording only `candidatesTokenCount` would report 3 % of the billable output, and
        would break the fit between output length and elapsed time that the latency work in
        `docs/specs/latency-and-cost.md` rests on.
        """
        monkeypatch.setenv("GOOGLE_ACCESS_TOKEN", "test-token-not-real")
        provider = GeminiProvider(model="m", endpoint=self.endpoint)
        usage = UsageSink()

        def _urlopen(request, **kwargs):
            del request, kwargs
            return _FakeResponse(
                self._answer(
                    promptTokenCount=1981,
                    candidatesTokenCount=36,
                    thoughtsTokenCount=1048,
                    totalTokenCount=3065,
                )
            )

        monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
        self._run(provider, metadata={"node": "planner", USAGE_SINK: usage})

        assert usage.input_tokens == 1981
        assert usage.output_tokens == 36 + 1048
        assert usage.reasoning_output_tokens == 1048

    def test_an_answer_without_thinking_reports_the_answer_alone(self, monkeypatch):
        """At LOW the field is absent entirely; absent must not become answer-plus-zero."""
        monkeypatch.setenv("GOOGLE_ACCESS_TOKEN", "test-token-not-real")
        provider = GeminiProvider(model="m", endpoint=self.endpoint)
        usage = UsageSink()

        def _urlopen(request, **kwargs):
            del request, kwargs
            return _FakeResponse(
                self._answer(promptTokenCount=1981, candidatesTokenCount=119, totalTokenCount=2100)
            )

        monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
        self._run(provider, metadata={"node": "planner", USAGE_SINK: usage})

        assert usage.output_tokens == 119
        assert usage.reasoning_output_tokens is None

    def test_a_provider_reporting_no_usage_at_all_records_nothing(self, monkeypatch):
        """`None` means unreported, which is not the same as zero."""
        monkeypatch.setenv("GOOGLE_ACCESS_TOKEN", "test-token-not-real")
        provider = GeminiProvider(model="m", endpoint=self.endpoint)
        usage = UsageSink()

        def _urlopen(request, **kwargs):
            del request, kwargs
            return _FakeResponse(self._answer())

        monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
        self._run(provider, metadata={"node": "planner", USAGE_SINK: usage})

        assert usage.output_tokens is None

    def test_usage_that_does_not_reconcile_is_reported(self, monkeypatch, caplog):
        """A counter this adapter does not read is the bug it already shipped once.

        The reported total is defined as the sum of the parts, so a gap means either a new
        counter or a misread one. Either way the spend figures are wrong, and wrong quietly.
        """
        monkeypatch.setenv("GOOGLE_ACCESS_TOKEN", "test-token-not-real")
        provider = GeminiProvider(model="m", endpoint=self.endpoint)
        usage = UsageSink()

        def _urlopen(request, **kwargs):
            del request, kwargs
            return _FakeResponse(
                self._answer(
                    promptTokenCount=1981,
                    candidatesTokenCount=36,
                    thoughtsTokenCount=1048,
                    # 400 tokens the adapter cannot name — a counter it does not read yet.
                    totalTokenCount=3465,
                )
            )

        monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
        with caplog.at_level(logging.WARNING):
            self._run(provider, metadata={"node": "planner", USAGE_SINK: usage})

        assert "gemini_usage_does_not_reconcile" in caplog.text
        assert any(getattr(r, "unaccounted", None) == 400 for r in caplog.records)

    def test_usage_that_reconciles_stays_quiet(self, monkeypatch, caplog):
        monkeypatch.setenv("GOOGLE_ACCESS_TOKEN", "test-token-not-real")
        provider = GeminiProvider(model="m", endpoint=self.endpoint)

        def _urlopen(request, **kwargs):
            del request, kwargs
            return _FakeResponse(
                self._answer(
                    promptTokenCount=1981,
                    candidatesTokenCount=36,
                    thoughtsTokenCount=1048,
                    totalTokenCount=3065,
                )
            )

        monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
        with caplog.at_level(logging.WARNING):
            self._run(provider, metadata={"node": "planner", USAGE_SINK: UsageSink()})

        assert "gemini_usage_does_not_reconcile" not in caplog.text

    def test_a_repaired_call_counts_the_reasoning_of_every_attempt(self, monkeypatch):
        """A discarded attempt was still thought about, and still billed.

        `duration_ms` already spans both generations. Counting the reasoning of only the
        attempt that validated would make a node that had to be repaired look cheaper than
        one that got it right first time — the exact inversion of what happened.
        """
        monkeypatch.setenv("GOOGLE_ACCESS_TOKEN", "test-token-not-real")
        provider = GeminiProvider(model="m", endpoint=self.endpoint)
        recorder = TraceRecorder("vertex-repair-case")
        bodies = itertools.chain(
            [
                json.dumps(
                    {
                        "candidates": [{"content": {"parts": [{"text": "{"}]}}],
                        "usageMetadata": {
                            "promptTokenCount": 1981,
                            "candidatesTokenCount": 4,
                            "thoughtsTokenCount": 903,
                            "totalTokenCount": 2888,
                        },
                    }
                )
            ],
            itertools.repeat(
                json.dumps(
                    {
                        "candidates": [
                            {"content": {"parts": [{"text": EvidencePacket().model_dump_json()}]}}
                        ],
                        "usageMetadata": {
                            "promptTokenCount": 2140,
                            "candidatesTokenCount": 36,
                            "thoughtsTokenCount": 1048,
                            "totalTokenCount": 3224,
                        },
                    }
                )
            ),
        )

        def _urlopen(request, **kwargs):
            del request, kwargs
            return _FakeResponse(next(bodies).encode())

        monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
        asyncio.run(
            request_structured(
                provider=provider,
                role="primary",
                node="candidate_generator",
                prompt="p",
                images=(),
                response_model=EvidencePacket,
                recorder=recorder,
            )
        )
        recorder.record_node("candidate_generator")

        call = recorder.build().events[0].provider_calls[0]
        assert call.attempts == 2
        assert call.input_tokens == 1981 + 2140
        assert call.output_tokens == (4 + 903) + (36 + 1048)
        assert call.reasoning_output_tokens == 903 + 1048
        # The decomposition the trace is read through, on a node that had to be repaired.
        assert call.output_tokens - call.reasoning_output_tokens == 4 + 36

    def test_a_rejected_bearer_token_names_the_token_not_the_api_key(self, monkeypatch):
        """Told to rotate GEMINI_API_KEY, a reader would rotate a key that was never sent."""
        monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
        monkeypatch.setenv("GOOGLE_ACCESS_TOKEN", "expired-token-not-real")
        provider = GeminiProvider(model="m", endpoint=self.endpoint)

        def _raise(*args, **kwargs):
            raise urllib.error.HTTPError(
                url=self.endpoint,
                code=401,
                msg="Unauthorized",
                hdrs=None,  # type: ignore[arg-type]
                fp=io.BytesIO(b'{"error":{"message":"Invalid authentication credential"}}'),
            )

        monkeypatch.setattr(urllib.request, "urlopen", _raise)
        with pytest.raises(ProviderUnavailableError, match="GOOGLE_ACCESS_TOKEN"):
            self._run(provider)


class TestOutputIdentifierBinding:
    """Gemini accepts `pattern` in a response schema and does not enforce it.

    `enum` it does enforce, which is why binding the identifier spaces this code owns is
    the only prevention available. Without it the sole surviving constraint is Pydantic on
    the way back in, where a malformed reference is discovered far too late to be cheap.
    """

    endpoint = "https://aiplatform.googleapis.com/v1/projects/p/locations/global/publishers/google"

    @staticmethod
    def _schema_sent(monkeypatch, metadata: dict[str, Any]) -> dict[str, Any]:
        monkeypatch.setenv("GOOGLE_ACCESS_TOKEN", "test-token-not-real")
        provider = GeminiProvider(model="m", endpoint=TestOutputIdentifierBinding.endpoint)
        sent: list[dict[str, Any]] = []

        def _urlopen(request, **kwargs):
            del kwargs
            sent.append(json.loads(request.data))
            return _FakeResponse(
                json.dumps(
                    {
                        "candidates": [
                            {
                                "content": {
                                    "parts": [{"text": CandidateProposal().model_dump_json()}]
                                }
                            }
                        ]
                    }
                ).encode()
            )

        monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
        asyncio.run(
            provider.generate_structured(
                role="primary",
                prompt="p",
                images=(),
                response_model=CandidateProposal,
                metadata={"node": "candidate_generator", **metadata},
            )
        )
        schema = sent[0]["generationConfig"]["responseSchema"]
        assert isinstance(schema, dict)
        return schema

    @staticmethod
    def _find(node: Any, name: str) -> list[dict[str, Any]]:
        """Every property called `name`, wherever the inlined schema put it."""
        found: list[dict[str, Any]] = []
        if isinstance(node, list):
            for item in node:
                found.extend(TestOutputIdentifierBinding._find(item, name))
        elif isinstance(node, dict):
            properties = node.get("properties")
            if isinstance(properties, dict) and isinstance(properties.get(name), dict):
                found.append(properties[name])
            for value in node.values():
                found.extend(TestOutputIdentifierBinding._find(value, name))
        return found

    def test_evidence_reference_arrays_are_bound_to_the_ids_the_run_produced(self, monkeypatch):
        schema = self._schema_sent(monkeypatch, {OUTPUT_EVIDENCE_IDS: ["obs-1", "obs-2", "inf-1"]})

        for name in ("supporting_evidence_ids", "contradicting_evidence_ids"):
            arrays = self._find(schema, name)
            assert arrays, f"{name} is missing from the response schema"
            for array in arrays:
                assert array["items"]["enum"] == ["obs-1", "obs-2", "inf-1"]

    def test_subject_ids_are_bound_on_the_same_call(self, monkeypatch):
        schema = self._schema_sent(monkeypatch, {OUTPUT_SUBJECT_IDS: ["tree_1"]})

        subjects = self._find(schema, "subject_id")
        assert subjects
        for subject in subjects:
            assert subject["enum"] == ["tree_1"]

    def test_an_empty_or_malformed_id_list_binds_nothing(self, monkeypatch):
        """A run with no observations must not send `enum: []`, which admits no value."""
        schema = self._schema_sent(
            monkeypatch, {OUTPUT_EVIDENCE_IDS: [], OUTPUT_SUBJECT_IDS: [None, 1]}
        )

        for array in self._find(schema, "supporting_evidence_ids"):
            assert "enum" not in array["items"]
        for subject in self._find(schema, "subject_id"):
            assert "enum" not in subject


class TestUsageAccounting:
    """Provider-reported token accounting reaches the trace, and nothing is invented.

    The latency work in `docs/specs/latency-and-cost.md` rests on the relationship between
    output length and elapsed time, so the numbers have to come from the provider and have
    to sit beside a duration that covers the same attempts.
    """

    def test_a_reported_usage_block_reaches_the_call_record(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-not-real")
        provider = OpenRouterProvider(model="some/other-model")
        recorder = TraceRecorder("usage-case")

        def _urlopen(request, **kwargs):
            del request, kwargs
            return _FakeResponse(
                json.dumps(
                    {
                        "choices": [{"message": {"content": EvidencePacket().model_dump_json()}}],
                        "usage": {
                            "prompt_tokens": 1200,
                            "completion_tokens": 340,
                            "prompt_tokens_details": {"cached_tokens": 900},
                        },
                    }
                ).encode()
            )

        monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
        asyncio.run(
            request_structured(
                provider=provider,
                role="primary",
                node="evidence_extractor",
                prompt="p",
                images=(),
                response_model=EvidencePacket,
                recorder=recorder,
            )
        )
        recorder.record_node("evidence_extractor")

        call = recorder.build().events[0].provider_calls[0]
        assert call.input_tokens == 1200
        assert call.cached_input_tokens == 900
        assert call.output_tokens == 340
        assert call.reported_cost_usd is None

    def test_a_provider_that_reports_nothing_leaves_the_fields_empty(self, monkeypatch):
        """Absent accounting is `None`, never zero. Zero is a claim; this is silence."""
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-not-real")
        provider = OpenRouterProvider(model="some/other-model")
        recorder = TraceRecorder("silent-case")

        def _urlopen(request, **kwargs):
            del request, kwargs
            return _FakeResponse(
                json.dumps(
                    {"choices": [{"message": {"content": EvidencePacket().model_dump_json()}}]}
                ).encode()
            )

        monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
        asyncio.run(
            request_structured(
                provider=provider,
                role="primary",
                node="planner",
                prompt="p",
                images=(),
                response_model=EvidencePacket,
                recorder=recorder,
            )
        )
        recorder.record_node("planner")

        call = recorder.build().events[0].provider_calls[0]
        assert call.input_tokens is None
        assert call.output_tokens is None

    def test_a_repaired_call_counts_both_attempts(self, monkeypatch):
        """`duration_ms` spans both generations, so the token counts beside it must too."""
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-not-real")
        provider = OpenRouterProvider(model="some/other-model")
        recorder = TraceRecorder("repair-case")
        bodies = itertools.chain(
            [
                json.dumps(
                    {
                        "choices": [{"message": {"content": "{"}}],
                        "usage": {"completion_tokens": 7},
                    }
                )
            ],
            itertools.repeat(
                json.dumps(
                    {
                        "choices": [{"message": {"content": EvidencePacket().model_dump_json()}}],
                        "usage": {"completion_tokens": 40},
                    }
                )
            ),
        )

        def _urlopen(request, **kwargs):
            del request, kwargs
            return _FakeResponse(next(bodies).encode())

        monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
        asyncio.run(
            request_structured(
                provider=provider,
                role="primary",
                node="candidate_generator",
                prompt="p",
                images=(),
                response_model=EvidencePacket,
                recorder=recorder,
            )
        )
        recorder.record_node("candidate_generator")

        call = recorder.build().events[0].provider_calls[0]
        assert call.attempts == 2
        assert call.output_tokens == 47
