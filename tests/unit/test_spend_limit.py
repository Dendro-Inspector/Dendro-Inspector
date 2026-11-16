"""A spend limit leaves out a stage the run may add; it never fails the run or hides why.

The stages a limit may leave out are a correction retry and the arbiter. The first pass
always runs. A run that reaches its limit answers from what it has, through the paths a spent
retry budget and a suppressed arbiter already take, and both the trace and the answer say
which stage was not run.
"""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path

import pytest

from dendro_inspector.config import GraphConfig, load_config
from dendro_inspector.evaluation.runner import load_cases
from dendro_inspector.graph.budget import SPEND_LIMIT_REACHED, Stage, affords, stage_size
from dendro_inspector.graph.definition import NodeName
from dendro_inspector.graph.routing import route_internal_gate
from dendro_inspector.graph.state import GraphState
from dendro_inspector.observability.events import ProviderCallRecord
from dendro_inspector.observability.trace import RunSpend, TraceRecorder
from dendro_inspector.providers.base import GRAPH_RETRY_INDEX
from dendro_inspector.runner import run_case
from dendro_inspector.schemas.evaluation import EvalCase
from dendro_inspector.schemas.input import CaseInput, ImageRef
from dendro_inspector.schemas.reviews import ReviewSynthesis
from tests.conftest import make_config

ROUND = {
    "evidence_extractor": 30_000,
    "candidate_generator": 25_000,
    "botanical_reviewer": 20_000,
    "confusion_reviewer": 28_000,
    "confidence_reviewer": 18_000,
}
FIRST_PASS = RunSpend(requests=6, tokens=135_000, latest_tokens={"planner": 14_000, **ROUND})
SECOND_LOOK = "review asked for a second look at the evidence and it was not run"
NO_ARBITER = "an independent review was due and was not run"


def _limits(**limits: int) -> GraphConfig:
    return GraphConfig.model_validate(limits)


class TestWhatFits:
    def test_without_a_limit_every_stage_fits(self):
        assert affords(_limits(), FIRST_PASS, Stage.CORRECTION_RETRY)
        assert affords(_limits(), FIRST_PASS, Stage.ARBITRATION)

    def test_a_retry_is_the_five_calls_it_repeats(self):
        assert stage_size(Stage.CORRECTION_RETRY, FIRST_PASS) == (5, sum(ROUND.values()))

    def test_the_arbiter_is_one_call_the_size_of_the_largest_review(self):
        assert stage_size(Stage.ARBITRATION, FIRST_PASS) == (1, 28_000)

    @pytest.mark.parametrize(("limit", "retry", "arbiter"), [(6, False, False), (7, False, True)])
    def test_requests_are_counted_before_the_stage_starts(self, limit, retry, arbiter):
        config = _limits(max_model_calls=limit)
        assert affords(config, FIRST_PASS, Stage.CORRECTION_RETRY) is retry
        assert affords(config, FIRST_PASS, Stage.ARBITRATION) is arbiter

    def test_a_retry_fits_when_all_five_of_its_calls_do(self):
        assert not affords(_limits(max_model_calls=10), FIRST_PASS, Stage.CORRECTION_RETRY)
        assert affords(_limits(max_model_calls=11), FIRST_PASS, Stage.CORRECTION_RETRY)

    def test_tokens_use_what_the_last_round_cost(self):
        # 135,000 spent; a retry is expected to cost 121,000 more and the arbiter 28,000.
        assert not affords(_limits(max_tokens=255_999), FIRST_PASS, Stage.CORRECTION_RETRY)
        assert affords(_limits(max_tokens=256_000), FIRST_PASS, Stage.CORRECTION_RETRY)
        assert not affords(_limits(max_tokens=162_999), FIRST_PASS, Stage.ARBITRATION)
        assert affords(_limits(max_tokens=163_000), FIRST_PASS, Stage.ARBITRATION)

    def test_either_limit_is_enough_to_leave_a_stage_out(self):
        config = _limits(max_model_calls=100, max_tokens=140_000)
        assert not affords(config, FIRST_PASS, Stage.ARBITRATION)

    def test_a_provider_that_reports_no_usage_is_not_bounded_by_tokens(self):
        silent = RunSpend(requests=6, tokens=0, latest_tokens={})
        assert affords(_limits(max_tokens=1), silent, Stage.CORRECTION_RETRY)


class TestWhatIsCounted:
    @staticmethod
    def _call(
        node: str, *, attempts: int = 1, tokens: tuple[int | None, int | None]
    ) -> ProviderCallRecord:
        return ProviderCallRecord(
            role="reviewer",
            adapter="fake",
            node=node,
            response_model="ReviewResult",
            attempts=attempts,
            input_tokens=tokens[0],
            output_tokens=tokens[1],
        )

    def test_repair_attempts_and_unclaimed_calls_count(self):
        recorder = TraceRecorder("spend")
        recorder.record_provider_call(self._call("planner", tokens=(10_000, 500)))
        recorder.record_node("planner")
        recorder.record_provider_call(
            self._call("botanical_reviewer", attempts=2, tokens=(40_000, 3_000))
        )
        spend = recorder.spend()
        assert spend.requests == 3
        assert spend.tokens == 53_500
        assert spend.latest_tokens == {"planner": 10_500, "botanical_reviewer": 43_000}

    def test_a_later_call_replaces_the_size_of_an_earlier_one(self):
        recorder = TraceRecorder("spend")
        for size in (20_000, 30_000):
            recorder.record_provider_call(self._call("evidence_extractor", tokens=(size, 0)))
            recorder.record_node("evidence_extractor")
        assert recorder.spend().latest_tokens == {"evidence_extractor": 30_000}

    def test_usage_a_provider_did_not_report_counts_as_nothing(self):
        recorder = TraceRecorder("spend")
        recorder.record_provider_call(self._call("planner", tokens=(None, None)))
        assert recorder.spend() == RunSpend(requests=1, tokens=0, latest_tokens={"planner": 0})


class TestSettings:
    def test_no_limit_by_default(self, monkeypatch):
        monkeypatch.delenv("DENDRO_MAX_MODEL_CALLS", raising=False)
        monkeypatch.delenv("DENDRO_MAX_TOKENS", raising=False)
        graph = load_config().graph
        assert graph.max_model_calls is None
        assert graph.max_tokens is None

    def test_the_limits_are_read_and_survive_the_image_override(self, monkeypatch):
        monkeypatch.setenv("DENDRO_MAX_MODEL_CALLS", "7")
        monkeypatch.setenv("DENDRO_MAX_TOKENS", "200000")
        monkeypatch.setenv("DENDRO_IMAGE_MAX_EDGE_PX", "2048")
        graph = load_config().graph
        assert (graph.max_model_calls, graph.max_tokens) == (7, 200_000)

    def test_a_limit_below_one_is_refused(self):
        with pytest.raises(ValueError, match="max_model_calls"):
            _limits(max_model_calls=0)


class TestRouting:
    @staticmethod
    def _state(*stops: str) -> GraphState:
        return GraphState(
            case=CaseInput(case_id="routing", images=()),
            synthesis=ReviewSynthesis(retry_required=True),
            budget_stops=stops,
        )

    def test_a_retry_with_budget_left_is_taken(self):
        assert route_internal_gate(self._state(), GraphConfig()) is NodeName.CORRECTION_WORKER

    def test_a_retry_the_limit_left_out_abstains_instead(self):
        state = self._state(Stage.CORRECTION_RETRY.value)
        assert route_internal_gate(state, GraphConfig()) is NodeName.ABSTAIN

    def test_a_stop_on_the_arbiter_does_not_close_the_retry(self):
        state = self._state(Stage.ARBITRATION.value)
        assert route_internal_gate(state, GraphConfig()) is NodeName.CORRECTION_WORKER


def _run(case: CaseInput, scenario: str, repo_root, fixtures=None, **limits: int):
    base = make_config(scenario)
    update: dict[str, object] = {"graph": base.graph.model_copy(update=limits)}
    if fixtures is not None:
        update["fixtures_root"] = fixtures
    return asyncio.run(run_case(case, config=base.model_copy(update=update), root=repo_root))


def _requests(result) -> int:
    return sum(call.attempts for event in result.trace.events for call in event.provider_calls)


def _limitations(result) -> list[str]:
    return [item for answer in result.response.results for item in answer.limitations]


class TestTheArbiter:
    """`arbiter-ranking-001` makes six calls and then calls the arbiter."""

    @staticmethod
    def _case(repo_root) -> EvalCase:
        return next(
            case
            for case in load_cases(repo_root / "evals" / "public")
            if case.case_id == "arbiter-ranking-001"
        )

    def test_a_limit_with_room_changes_nothing(self, repo_root):
        case = self._case(repo_root)
        free = _run(case.input, case.scenario, repo_root)
        limited = _run(case.input, case.scenario, repo_root, max_model_calls=7)
        assert free.trace.arbiter_used and limited.trace.arbiter_used
        assert _requests(limited) == _requests(free) == 7
        assert limited.state.decisions == free.state.decisions
        assert limited.state.budget_stops == ()

    def test_a_limit_without_room_answers_without_the_arbiter_and_says_so(self, repo_root):
        case = self._case(repo_root)
        limited = _run(case.input, case.scenario, repo_root, max_model_calls=6)
        assert _requests(limited) == 6
        assert limited.trace.arbiter_used is False
        # The triggers stay on record: a call that was due, and not made.
        assert limited.trace.escalation_reasons
        assert limited.trace.escalation_suppressed_by == (SPEND_LIMIT_REACHED,)
        assert limited.state.budget_stops == (Stage.ARBITRATION.value,)
        assert limited.state.decisions == limited.state.provisional_decisions
        assert any(NO_ARBITER in item for item in _limitations(limited))
        assert limited.trace.runtime_identity.config["graph"]["max_model_calls"] == 6


@pytest.fixture
def retry_fixtures(tmp_path: Path, repo_root: Path) -> Path:
    """`primary-pass`, with a reviewer that asks the first extraction to be done again."""
    payload = json.loads(
        (repo_root / "evals/fixtures/primary-pass.json").read_text(encoding="utf-8")
    )
    responses = payload["responses"]
    sequences = {
        key: [
            {"response": copy.deepcopy(response), "metadata": {GRAPH_RETRY_INDEX: index}}
            for index in range(2)
        ]
        for key, response in responses.items()
        if key != "primary:planner"
    }
    review = sequences["reviewer:confidence_reviewer"][0]["response"]
    review["status"] = "fail_correctable"
    review["findings"] = [
        {
            "finding_id": "check-first-reading",
            "category": "unsupported_claim",
            "severity": "major",
            "summary": "Re-extract the cited fascicle reading before deciding.",
            "evidence_ids": ["obs-2"],
            "subject_id": "foreground_log_1",
            "required_action": "re_extract_evidence",
            "impact": "observation_change",
        }
    ]
    scripted = {
        "responses": {"primary:planner": responses["primary:planner"]},
        "response_sequences": sequences,
    }
    (tmp_path / "retry.json").write_text(json.dumps(scripted), encoding="utf-8")
    return tmp_path


class TestTheRetry:
    """The first pass is six calls; the retry a reviewer asks for is five more."""

    CASE = CaseInput(
        case_id="spend-limit-retry",
        images=(ImageRef(image_id="img-1", path=Path("img-1.jpg")),),
    )

    def test_a_limit_with_room_changes_nothing(self, repo_root, retry_fixtures):
        free = _run(self.CASE, "retry", repo_root, retry_fixtures)
        limited = _run(self.CASE, "retry", repo_root, retry_fixtures, max_model_calls=11)
        assert free.state.retries == limited.state.retries == 1
        assert _requests(limited) == _requests(free) == 11
        assert limited.state.decisions == free.state.decisions

    def test_a_limit_without_room_abstains_and_names_the_limit(self, repo_root, retry_fixtures):
        free = _run(self.CASE, "retry", repo_root, retry_fixtures)
        limited = _run(self.CASE, "retry", repo_root, retry_fixtures, max_model_calls=10)
        assert limited.state.retries == 0
        assert _requests(limited) == 6
        assert limited.state.budget_stops == (Stage.CORRECTION_RETRY.value,)
        assert limited.state.abstained
        # Not "retry_budget_spent": the graph had a retry left and the run had no room for it.
        assert [record.cause for record in limited.trace.abstentions] == [SPEND_LIMIT_REACHED]
        assert any(SECOND_LOOK in item for item in _limitations(limited))
        # The claim is the one abstention leaves, never a stronger one than the full run made.
        assert limited.state.decisions != free.state.decisions
        assert limited.state.decisions[0].abstained

    def test_the_ukrainian_answer_names_the_limit_too(self, repo_root, retry_fixtures):
        case = self.CASE.model_copy(update={"metadata": {"locale": "uk"}})
        limited = _run(case, "retry", repo_root, retry_fixtures, max_model_calls=10)
        assert any("ліміт витрат" in item for item in _limitations(limited))
