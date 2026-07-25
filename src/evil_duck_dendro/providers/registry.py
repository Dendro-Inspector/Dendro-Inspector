"""Logical role to concrete adapter binding.

This is the only module that knows a commercial model exists. Swapping ``primary`` from
one vendor to another is a configuration change here, not an edit to any node.
"""

from __future__ import annotations

from pathlib import Path
from typing import assert_never

from evil_duck_dendro.config import Adapter, AppConfig, ProviderConfig, Role
from evil_duck_dendro.providers.base import ModelProvider, ProviderUnavailableError
from evil_duck_dendro.providers.fake import FakeModelProvider


def build_provider(
    config: ProviderConfig,
    *,
    fixtures_root: Path,
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
            from evil_duck_dendro.providers.openai_adapter import OpenAIProvider

            return OpenAIProvider(
                model=config.model,
                api_key_env=config.api_key_env or "OPENAI_API_KEY",
            )
        case Adapter.ANTHROPIC:
            from evil_duck_dendro.providers.anthropic_adapter import AnthropicProvider

            return AnthropicProvider(
                model=config.model,
                api_key_env=config.api_key_env or "ANTHROPIC_API_KEY",
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
        return cls(
            {
                role: build_provider(config.provider_for(role), fixtures_root=fixtures_root)
                for role in (Role.PRIMARY, Role.ARBITER)
            }
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
