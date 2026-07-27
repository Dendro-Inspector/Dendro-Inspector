"""Shared test fixtures.

Every test runs against the fake provider. Nothing here reaches a network or a credential,
by construction rather than by convention.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

import pytest

from dendro_inspector.config import (
    Adapter,
    AppConfig,
    KnowledgeConfig,
    PromptConfig,
    ProviderConfig,
    Role,
)
from dendro_inspector.graph.executor import NodeContext
from dendro_inspector.knowledge.loader import KnowledgeBase
from dendro_inspector.observability.trace import TraceRecorder
from dendro_inspector.prompts.library import PromptLibrary
from dendro_inspector.providers.registry import ProviderRegistry
from dendro_inspector.runner import CaseRunResult, run_case
from dendro_inspector.schemas.evidence import (
    AttachmentStatus,
    WoodSurface,
    requires_wood_surface,
)
from dendro_inspector.schemas.input import CaseInput, DeclaredObjectType, ImageRef


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """The repository root, so tests do not depend on the working directory."""
    return Path(__file__).resolve().parents[1]


@pytest.fixture
def config() -> AppConfig:
    """Default offline configuration pinned to the passing scenario."""
    return AppConfig(
        providers={
            Role.PRIMARY: ProviderConfig(adapter=Adapter.FAKE, scenario="primary-pass"),
            Role.ARBITER: ProviderConfig(adapter=Adapter.FAKE, scenario="primary-pass"),
        },
        prompts=PromptConfig(),
        knowledge=KnowledgeConfig(),
    )


def make_config(scenario: str) -> AppConfig:
    """Configuration bound to a specific fixture scenario."""
    return AppConfig(
        providers={
            Role.PRIMARY: ProviderConfig(adapter=Adapter.FAKE, scenario=scenario),
            Role.ARBITER: ProviderConfig(adapter=Adapter.FAKE, scenario=scenario),
        }
    )


@pytest.fixture
def scenario_config() -> Callable[[str], AppConfig]:
    """Factory fixture: build a config pinned to a named fixture scenario."""
    return make_config


@pytest.fixture
def run_scenario(repo_root: Path) -> Callable[[CaseInput, str], CaseRunResult]:
    """Run a case against a named scenario, synchronously."""

    def _run(case: CaseInput, scenario: str) -> CaseRunResult:
        return asyncio.run(run_case(case, config=make_config(scenario), root=repo_root))

    return _run


@pytest.fixture
def knowledge(repo_root: Path) -> KnowledgeBase:
    return KnowledgeBase(KnowledgeConfig(), root=repo_root)


@pytest.fixture
def node_context(config: AppConfig, repo_root: Path) -> NodeContext:
    """A node context wired to fakes, for testing nodes in isolation."""
    return NodeContext(
        config=config,
        providers=ProviderRegistry.from_config(config, root=repo_root),
        knowledge=KnowledgeBase(config.knowledge, root=repo_root),
        prompts=PromptLibrary(config.prompts, root=repo_root),
        recorder=TraceRecorder("test-case"),
    )


@pytest.fixture
def simple_case() -> CaseInput:
    return CaseInput(
        case_id="test-case",
        images=(ImageRef(image_id="img-1", path=Path("examples/log.jpg")),),
        user_text="What is this?",
        location="Kyiv Oblast, Ukraine",
        declared_object_type=DeclaredObjectType.LOG,
    )


DETACHABLE_FAMILIES = (
    "leaf",
    "leaflet",
    "needles",
    "bud",
    "branch",
    "fruit",
    "seed",
    "cones",
    "acorn",
    "nut",
    "samara",
    "catkin",
    "pod",
)


def _attachment(feature: str, attached: bool) -> AttachmentStatus | None:
    """Attachment status a test observation needs, or None for fixed features."""
    if feature.split(".")[0] not in DETACHABLE_FAMILIES:
        return None
    return AttachmentStatus.CONFIRMED_ATTACHED if attached else AttachmentStatus.UNKNOWN


def _wood_surface(
    feature: str,
    surface: WoodSurface = WoodSurface.PREPARED_END_GRAIN,
) -> WoodSurface | None:
    """Surface provenance a test observation needs, or None for non-wood features."""
    return surface if requires_wood_surface(feature) else None
