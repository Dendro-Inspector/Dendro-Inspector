"""Case runner.

One place that assembles a :class:`NodeContext` and executes the graph, so the CLI, the
evaluation runner and integration tests all exercise the same wiring rather than three
subtly different versions of it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dendro_inspector.config import AppConfig, Role, load_config
from dendro_inspector.graph.executor import NodeContext, run_graph
from dendro_inspector.graph.state import GraphState
from dendro_inspector.knowledge.loader import KnowledgeBase
from dendro_inspector.nodes import build_registry
from dendro_inspector.observability.events import RunTrace
from dendro_inspector.observability.trace import TraceRecorder
from dendro_inspector.prompts.library import PromptLibrary
from dendro_inspector.providers.registry import ProviderRegistry
from dendro_inspector.schemas.decisions import CaseResponse
from dendro_inspector.schemas.input import CaseInput


@dataclass(frozen=True, slots=True)
class CaseRunResult:
    state: GraphState
    trace: RunTrace

    @property
    def response(self) -> CaseResponse | None:
        """The toned response when the tone layer ran, otherwise the factual one."""
        return self.state.final_response or self.state.response


def build_context(
    config: AppConfig | None = None,
    *,
    root: Path | None = None,
    case_id: str = "case",
) -> NodeContext:
    """Assemble a node context. ``root`` is the repository root for relative paths."""
    resolved_config = load_config(config)
    base = root or Path.cwd()
    prompts = PromptLibrary(resolved_config.prompts, root=base)
    prompts.validate_policy()
    providers = ProviderRegistry.from_config(resolved_config, root=base)
    recorder = TraceRecorder(case_id)

    recorder.set_prompt_metadata(prompts.metadata())
    for role in Role:
        provider = providers.get(role)
        recorder.set_provider(role.value, provider.adapter_name, provider.model)

    return NodeContext(
        config=resolved_config,
        providers=providers,
        knowledge=KnowledgeBase(resolved_config.knowledge, root=base),
        prompts=prompts,
        recorder=recorder,
    )


async def run_case(
    case: CaseInput,
    *,
    config: AppConfig | None = None,
    root: Path | None = None,
) -> CaseRunResult:
    """Run one case end to end."""
    ctx = build_context(config, root=root, case_id=case.case_id)
    result = await run_graph(case, ctx, build_registry())
    return CaseRunResult(state=result.state, trace=result.trace)
