"""Case runner.

One place that assembles a :class:`NodeContext` and executes the graph, so the CLI, the
evaluation runner and integration tests all exercise the same wiring rather than three
subtly different versions of it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from evil_duck_dendro.config import AppConfig, Role, load_config
from evil_duck_dendro.graph.executor import NodeContext, run_graph
from evil_duck_dendro.graph.state import GraphState
from evil_duck_dendro.knowledge.loader import KnowledgeBase
from evil_duck_dendro.nodes import build_registry
from evil_duck_dendro.observability.events import RunTrace
from evil_duck_dendro.observability.trace import TraceRecorder
from evil_duck_dendro.prompts.library import PromptLibrary
from evil_duck_dendro.providers.registry import ProviderRegistry
from evil_duck_dendro.schemas.decisions import CaseResponse
from evil_duck_dendro.schemas.input import CaseInput


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
    providers = ProviderRegistry.from_config(resolved_config, root=base)
    recorder = TraceRecorder(case_id)
    prompts = PromptLibrary(resolved_config.prompts, root=base)

    recorder.set_prompt_metadata(prompts.domain.metadata())
    for role in (Role.PRIMARY, Role.ARBITER):
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
