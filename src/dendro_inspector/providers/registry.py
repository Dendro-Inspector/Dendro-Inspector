"""Logical role to concrete adapter binding.

This is the only module that knows a commercial model exists. Swapping ``primary`` from
one vendor to another is a configuration change here, not an edit to any node.
"""

from __future__ import annotations

from pathlib import Path
from typing import assert_never

from dendro_inspector.config import DEFAULT_KEY_ENV, Adapter, AppConfig, ProviderConfig, Role
from dendro_inspector.observability.logging import get_logger
from dendro_inspector.providers.base import ModelProvider, ProviderUnavailableError
from dendro_inspector.providers.fake import FakeModelProvider
from dendro_inspector.providers.image_files import SDKImageFiles


def build_provider(
    config: ProviderConfig,
    *,
    fixtures_root: Path,
    image_files: SDKImageFiles | None = None,
) -> ModelProvider:
    """Instantiate one adapter. Vendor SDKs are imported only when actually selected."""
    match config.adapter:
        case Adapter.FAKE:
            return FakeModelProvider(
                scenario=config.scenario or "primary-pass",
                fixtures_root=fixtures_root,
                model=config.model,
            )
        case Adapter.OPENAI:
            from dendro_inspector.providers.openai_adapter import OpenAIProvider

            return OpenAIProvider(
                model=config.model,
                api_key_env=config.api_key_env or "OPENAI_API_KEY",
                image_files=image_files,
            )
        case Adapter.ANTHROPIC:
            from dendro_inspector.providers.anthropic_adapter import AnthropicProvider

            return AnthropicProvider(
                model=config.model,
                api_key_env=config.api_key_env or "ANTHROPIC_API_KEY",
            )
        case Adapter.ANTHROPIC_SDK:
            from dendro_inspector.providers.anthropic_sdk_adapter import AnthropicSDKProvider

            return AnthropicSDKProvider(
                model=config.model,
                api_key_env=config.api_key_env or "ANTHROPIC_SDK_API_KEY",
                image_files=image_files,
            )
        case Adapter.OLLAMA:
            from dendro_inspector.providers.ollama_adapter import OllamaProvider

            return OllamaProvider(model=config.model)
        case Adapter.GEMINI:
            from dendro_inspector.providers.gemini_adapter import GeminiProvider

            return GeminiProvider(
                model=config.model,
                api_key_env=config.api_key_env or "GEMINI_API_KEY",
            )
        case Adapter.NVIDIA:
            from dendro_inspector.providers.nvidia_adapter import NvidiaProvider

            return NvidiaProvider(
                model=config.model,
                api_key_env=config.api_key_env or "NVIDIA_API_KEY",
            )
        case Adapter.OPENROUTER:
            from dendro_inspector.providers.openrouter_adapter import OpenRouterProvider

            return OpenRouterProvider(
                model=config.model,
                api_key_env=config.api_key_env or "OPENROUTER_API_KEY",
            )
        case _:  # pragma: no cover - exhaustiveness check, fails at type-check time
            assert_never(config.adapter)


class ProviderRegistry:
    """Role-keyed provider lookup, built once per run."""

    def __init__(self, providers: dict[Role, ModelProvider]) -> None:
        self._providers = providers

    @classmethod
    def from_config(cls, config: AppConfig, *, root: Path | None = None) -> ProviderRegistry:
        base = root or Path.cwd()
        fixtures_root = (
            config.fixtures_root
            if config.fixtures_root.is_absolute()
            else base / config.fixtures_root
        )
        caches: dict[tuple[Adapter, str], SDKImageFiles] = {}
        providers: dict[Role, ModelProvider] = {}
        for role in Role:
            selected = config.provider_for(role)
            cache = None
            if selected.adapter in {Adapter.OPENAI, Adapter.ANTHROPIC_SDK}:
                key = (selected.adapter, selected.api_key_env or DEFAULT_KEY_ENV[selected.adapter])
                if key not in caches:
                    caches[key] = SDKImageFiles(
                        "openai" if selected.adapter is Adapter.OPENAI else "anthropic"
                    )
                cache = caches[key]
            providers[role] = build_provider(
                selected, fixtures_root=fixtures_root, image_files=cache
            )
        return cls(providers)

    async def aclose(self) -> None:
        """Release optional provider resources after all graph tasks have finished."""
        seen: set[int] = set()
        for provider in self._providers.values():
            if id(provider) in seen:
                continue
            seen.add(id(provider))
            close = getattr(provider, "aclose", None)
            if callable(close):
                try:
                    await close()
                except Exception:
                    get_logger("providers").warning(
                        "provider_cleanup_failed", extra={"provider": provider.adapter_name}
                    )

    def get(self, role: Role) -> ModelProvider:
        try:
            return self._providers[role]
        except KeyError as exc:
            msg = f"no provider bound to role {role.value!r}"
            raise ProviderUnavailableError(msg) from exc

    def describe(self) -> dict[str, str]:
        """role -> 'adapter:model'. Never a credential."""
        return {
            role.value: f"{provider.adapter_name}:{provider.model}"
            for role, provider in self._providers.items()
        }
