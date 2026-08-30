"""Evaluation runner.

Loads YAML cases, runs each one against its declared fake-provider scenario, applies the
assertions and computes metrics. No paid API call is involved and no network is touched, so
the suite is safe to run in CI on every pull request — including pull requests from forks,
which never have repository secrets.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from dendro_inspector.config import Adapter, AppConfig, ProviderConfig, Role, load_config
from dendro_inspector.evaluation.assertions import evaluate
from dendro_inspector.evaluation.metrics import ScoredCase, compute
from dendro_inspector.graph.state import GraphState
from dendro_inspector.prompts.library import PromptPolicyError
from dendro_inspector.runner import run_case
from dendro_inspector.schemas.evaluation import (
    AssertionOutcome,
    CaseOutcome,
    EvalCase,
    EvalReport,
)

DEFAULT_SUITE = "public"


class EvalSuiteError(RuntimeError):
    """Raised when a suite cannot be loaded."""


def load_cases(suite_dir: Path) -> tuple[EvalCase, ...]:
    """Load and validate every case in a suite directory, sorted by filename."""
    if not suite_dir.is_dir():
        msg = f"evaluation suite directory not found: {suite_dir}"
        raise EvalSuiteError(msg)

    cases: list[EvalCase] = []
    for path in sorted(suite_dir.glob("*.yaml")):
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
        try:
            cases.append(EvalCase.model_validate(raw))
        except ValidationError as exc:
            msg = f"invalid evaluation case {path.name}: {exc}"
            raise EvalSuiteError(msg) from exc
    if not cases:
        msg = f"no evaluation cases found in {suite_dir}"
        raise EvalSuiteError(msg)
    return tuple(cases)


def _scenario_config(base: AppConfig, scenario: str) -> AppConfig:
    """Pin all model roles to the fake adapter replaying this case's scenario."""
    return base.model_copy(
        update={
            "providers": {
                Role.PRIMARY: ProviderConfig(adapter=Adapter.FAKE, scenario=scenario),
                Role.REVIEWER: ProviderConfig(adapter=Adapter.FAKE, scenario=scenario),
                Role.ARBITER: ProviderConfig(adapter=Adapter.FAKE, scenario=scenario),
            }
        }
    )


def _top_3(state: GraphState) -> frozenset[str]:
    return frozenset(
        candidate.taxon
        for candidate_set in state.candidate_sets
        for candidate in candidate_set.ordered[:3]
    )


async def score_case(
    case: EvalCase,
    *,
    config: AppConfig,
    root: Path,
) -> tuple[CaseOutcome, frozenset[str]]:
    """Run one evaluation case and score it.

    A case that raises is a failed case, not a failed suite: one broken fixture must not
    hide the results of the other four.
    """
    try:
        result = await run_case(
            case.input,
            config=_scenario_config(config, case.scenario),
            root=root,
        )
    except PromptPolicyError:
        # Deployment-wide incompatibility, not a case-specific fixture failure.
        raise
    except Exception as exc:  # broad by design - see docstring
        return (
            CaseOutcome(
                case_id=case.case_id,
                passed=False,
                error=f"{type(exc).__name__}: {exc}"[:400],
                schema_valid=False,
            ),
            frozenset(),
        )

    state = result.state
    assertions = evaluate(state, case.expect)
    decision = state.decisions[0] if state.decisions else None
    return (
        CaseOutcome(
            case_id=case.case_id,
            passed=all(a.outcome is not AssertionOutcome.FAIL for a in assertions),
            assertions=assertions,
            resolution=decision.resolution if decision else None,
            confidence=decision.confidence if decision else None,
            selected_taxon=decision.selected_taxon if decision else None,
            selected_taxon_display_name=(
                decision.selected_taxon_display_name if decision else None
            ),
            evidence_tier=decision.evidence_tier if decision else None,
            admitted_rerank_finding_ids=tuple(
                rerank.finding_id
                for synthesis in (state.synthesis, state.arbiter_synthesis)
                if synthesis is not None
                for rerank in synthesis.admitted_reranks
            ),
            arbiter_used=state.arbiter_used,
            retries=state.retries,
        ),
        _top_3(state),
    )


async def run_suite_async(
    suite: str = DEFAULT_SUITE,
    *,
    root: Path | None = None,
    config: AppConfig | None = None,
) -> EvalReport:
    """Run every case in a suite."""
    base = root or Path.cwd()
    resolved = load_config(config)
    cases = load_cases(base / "evals" / suite)

    scored: list[ScoredCase] = []
    for case in cases:
        outcome, top_3 = await score_case(case, config=resolved, root=base)
        scored.append(ScoredCase(case=case, outcome=outcome, top_3_taxa=top_3))

    outcomes = tuple(item.outcome for item in scored)
    return EvalReport(
        suite=suite,
        passed=sum(1 for outcome in outcomes if outcome.passed),
        failed=sum(1 for outcome in outcomes if not outcome.passed),
        outcomes=outcomes,
        metrics=compute(tuple(scored)),
    )


def run_suite(
    suite: str = DEFAULT_SUITE,
    *,
    root: Path | None = None,
    config: AppConfig | None = None,
) -> EvalReport:
    """Synchronous wrapper for the CLI and for tests."""
    return asyncio.run(run_suite_async(suite, root=root, config=config))
