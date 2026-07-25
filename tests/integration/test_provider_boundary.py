"""Provider boundary: structured-output repair, failure classification, and no live calls."""

from __future__ import annotations

import asyncio

import pytest

from evil_duck_dendro.config import Adapter, ProviderConfig
from evil_duck_dendro.observability.trace import TraceRecorder
from evil_duck_dendro.providers.base import (
    ProviderUnavailableError,
    StructuredOutputError,
    request_structured,
)
from evil_duck_dendro.providers.fake import (
    FakeModelProvider,
    ScenarioNotFoundError,
    UnscriptedCallError,
)
from evil_duck_dendro.providers.registry import ProviderRegistry, build_provider
from evil_duck_dendro.schemas.evidence import EvidencePacket


@pytest.fixture
def fixtures_root(repo_root):
    return repo_root / "evals" / "fixtures"


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
        assert set(described) == {"primary", "arbiter"}
        assert all(value.startswith("fake") for value in described.values())

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
