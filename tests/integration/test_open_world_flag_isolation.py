"""With open-world discovery off, an uncovered proposal changes nothing at all.

Every public evaluation case is run twice with the flag off: once as scripted, and once with
an uncovered taxon injected at rank 1 of every candidate set, citing the same evidence as
that set's original leader. The verdicts and their composition records must be identical.

This is the backward-compatibility contract the flag promises, tested over the whole
public corpus rather than one hand-picked scenario. A version of this branch broke it:
open-world candidates were retained, verified and allowed to cap the verdict regardless of
the flag.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from dendro_inspector.config import AppConfig, GraphConfig
from dendro_inspector.evaluation.runner import load_cases
from dendro_inspector.runner import CaseRunResult, run_case
from dendro_inspector.schemas.input import CaseInput
from tests.conftest import make_config

REPO_ROOT = Path(__file__).resolve().parents[2]
INJECTED = "zelkova_carpinifolia"


def _cases_with_a_generator() -> list[tuple[str, str, CaseInput]]:
    cases = []
    for case in load_cases(REPO_ROOT / "evals" / "public"):
        fixture = REPO_ROOT / "evals" / "fixtures" / f"{case.scenario}.json"
        responses = json.loads(fixture.read_text(encoding="utf-8"))["responses"]
        if "primary:candidate_generator" in responses:
            cases.append((case.case_id, case.scenario, case.input))
    return cases


CASES = _cases_with_a_generator()


def _inject(scenario: str, target: Path) -> None:
    """Copy a fixture with an uncovered taxon ranked above every scripted candidate."""
    source = REPO_ROOT / "evals" / "fixtures" / f"{scenario}.json"
    data: dict[str, Any] = json.loads(source.read_text(encoding="utf-8"))
    for candidate_set in data["responses"]["primary:candidate_generator"]["sets"]:
        candidates = candidate_set["candidates"]
        if not candidates:
            continue
        leader = min(candidates, key=lambda candidate: candidate["rank"])
        for candidate in candidates:
            candidate["rank"] += 1
        candidates.insert(
            0,
            {
                "taxon": INJECTED,
                "resolution": "species",
                "supporting_evidence_ids": list(leader["supporting_evidence_ids"]),
                "contradicting_evidence_ids": [],
                "missing_decisive_features": [],
                "score": "strong",
                "rank": 1,
            },
        )
    target.mkdir(parents=True, exist_ok=True)
    (target / f"{scenario}.json").write_text(json.dumps(data), encoding="utf-8")


def _run(case: CaseInput, scenario: str, *, fixtures: Path, open_world: bool) -> CaseRunResult:
    config: AppConfig = make_config(scenario).model_copy(
        update={
            "fixtures_root": fixtures,
            "graph": GraphConfig(open_world_discovery=open_world),
        }
    )
    return asyncio.run(run_case(case, config=config, root=REPO_ROOT))


@pytest.mark.parametrize(("case_id", "scenario", "case"), CASES, ids=[c[0] for c in CASES])
def test_flag_off_an_injected_uncovered_leader_changes_nothing(case_id, scenario, case, tmp_path):
    _inject(scenario, tmp_path)

    scripted = _run(case, scenario, fixtures=REPO_ROOT / "evals" / "fixtures", open_world=False)
    injected = _run(case, scenario, fixtures=tmp_path, open_world=False)

    assert injected.state.open_world_candidates == ()
    assert injected.state.decisions == scripted.state.decisions
    assert injected.trace.decision_derivations == scripted.trace.decision_derivations


def test_the_injection_is_live_when_the_flag_is_on(tmp_path):
    """Guard against a vacuous pass: the same injection does reach the decisions when on."""
    retained = 0
    for _case_id, scenario, case in CASES:
        _inject(scenario, tmp_path)
        result = _run(case, scenario, fixtures=tmp_path, open_world=True)
        retained += sum(
            candidate.taxon == INJECTED
            for decision in result.state.decisions
            for candidate in decision.open_world_candidates
        )
    assert retained > 0
