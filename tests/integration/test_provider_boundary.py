"""Provider boundary: structured-output repair, failure classification, and no live calls."""

from __future__ import annotations

import asyncio
import io
import itertools
import json
import urllib.error
import urllib.request

import pytest

from dendro_inspector.config import Adapter, AppConfig, ProviderConfig, Role, load_config
from dendro_inspector.observability.trace import TraceRecorder
from dendro_inspector.providers.base import (
    ProviderError,
    ProviderUnavailableError,
    StructuredOutputError,
    request_structured,
)
from dendro_inspector.providers.fake import (
    FakeModelProvider,
    ScenarioNotFoundError,
    UnscriptedCallError,
)
from dendro_inspector.providers.gemini_adapter import GeminiProvider
from dendro_inspector.providers.ollama_adapter import OllamaProvider
from dendro_inspector.providers.registry import ProviderRegistry, build_provider
from dendro_inspector.schemas.evidence import EvidencePacket


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
