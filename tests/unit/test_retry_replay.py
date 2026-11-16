"""A recorded retry must consume its own evidence, proposals and reviewer outputs."""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path

import pytest

from dendro_inspector.config import Adapter, ProviderConfig, Role
from dendro_inspector.evaluation.trace_replay import (
    ReplayUnavailableError,
    StoredRecord,
    config_as_recorded,
    fixture_from_trace,
    replay,
    replay_gaps,
)
from dendro_inspector.providers.base import GRAPH_RETRY_INDEX, StructuredOutputError
from dendro_inspector.providers.fake import FakeModelProvider, UnscriptedCallError
from dendro_inspector.runner import run_case
from dendro_inspector.schemas.input import CaseInput, ImageRef
from dendro_inspector.schemas.reviews import ReviewResult


def _provider(tmp_path, payload):
    (tmp_path / "sequence.json").write_text(json.dumps(payload), encoding="utf-8")
    return FakeModelProvider(scenario="sequence", fixtures_root=tmp_path)


def _call(provider, node="confidence_reviewer", retry=0):
    return asyncio.run(
        provider.generate_structured(
            role="reviewer",
            prompt="",
            images=(),
            response_model=ReviewResult,
            metadata={"node": node, GRAPH_RETRY_INDEX: retry},
        )
    )


PASS = {"reviewer": "confidence", "status": "pass"}
KEY = "reviewer:confidence_reviewer"


def test_sequence_exhaustion_never_reuses_static_or_final_answer(tmp_path):
    provider = _provider(
        tmp_path,
        {
            "responses": {KEY: PASS},
            "response_sequences": {KEY: [{"response": {**PASS, "recommended_confidence": "low"}}]},
        },
    )
    assert _call(provider).recommended_confidence == "low"
    with pytest.raises(UnscriptedCallError, match="exhausted"):
        _call(provider)


def test_wrong_retry_cannot_borrow_another_rounds_answer_or_consume_it(tmp_path):
    provider = _provider(
        tmp_path,
        {"response_sequences": {KEY: [{"response": PASS, "metadata": {GRAPH_RETRY_INDEX: 1}}]}},
    )
    with pytest.raises(UnscriptedCallError, match="expects graph_retry_index=1"):
        _call(provider)
    assert _call(provider, retry=1).status == "pass"


def test_sequences_are_independent_between_concurrent_reviewers(tmp_path):
    provider = _provider(
        tmp_path,
        {
            "response_sequences": {
                KEY: [{"response": PASS}, {"response": {**PASS, "recommended_confidence": "low"}}],
                "reviewer:botanical_reviewer": [{"response": {**PASS, "reviewer": "botanical"}}],
            }
        },
    )

    async def fanout():
        return await asyncio.gather(
            *(
                provider.generate_structured(
                    role="reviewer",
                    prompt="",
                    images=(),
                    response_model=ReviewResult,
                    metadata={"node": node},
                )
                for node in ("confidence_reviewer", "botanical_reviewer")
            )
        )

    assert [r.reviewer for r in asyncio.run(fanout())] == ["confidence", "botanical"]
    assert _call(provider).recommended_confidence == "low"


def test_synthetic_repair_does_not_consume_the_first_graph_response(tmp_path):
    provider = _provider(
        tmp_path,
        {"malformed_once": [KEY], "response_sequences": {KEY: [{"response": PASS}]}},
    )
    with pytest.raises(StructuredOutputError, match="scripted malformed"):
        _call(provider)
    assert _call(provider).status == "pass"
    with pytest.raises(UnscriptedCallError, match="exhausted"):
        _call(provider)


def test_actual_malformed_entry_can_be_followed_by_a_repair(tmp_path):
    provider = _provider(
        tmp_path, {"response_sequences": {KEY: [{"response": {}}, {"response": PASS}]}}
    )
    with pytest.raises(StructuredOutputError, match="does not satisfy"):
        _call(provider)
    assert _call(provider).status == "pass"


@pytest.fixture
def retried_run(tmp_path, repo_root, config):
    payload = json.loads(
        (repo_root / "evals/fixtures/primary-pass.json").read_text(encoding="utf-8")
    )
    responses = payload["responses"]
    sequences = {}
    for key, response in responses.items():
        if key == "primary:planner":
            continue
        sequences[key] = [
            {"response": copy.deepcopy(response), "metadata": {GRAPH_RETRY_INDEX: index}}
            for index in range(2)
        ]
    first = sequences["primary:evidence_extractor"][0]
    first["response"] = json.loads(json.dumps(first["response"]).replace('"img-1"', '"img-early"'))
    first["response"]["observations"][1]["notes"] = "First extraction, before correction."
    proposal = sequences["primary:candidate_generator"][0]["response"]
    proposal["sets"][0]["candidates"][0]["missing_decisive_features"] = ["needle_attachment_macro"]
    review = sequences[KEY][0]["response"]
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
    payload = {
        "responses": {"primary:planner": responses["primary:planner"]},
        "response_sequences": sequences,
    }
    (tmp_path / "retry.json").write_text(json.dumps(payload), encoding="utf-8")
    fake = ProviderConfig(adapter=Adapter.FAKE, scenario="retry")
    live_config = config.model_copy(
        update={"providers": dict.fromkeys(Role, fake), "fixtures_root": tmp_path}
    )
    case = CaseInput(
        case_id="recorded-retry",
        images=tuple(
            ImageRef(image_id=name, path=Path(f"{name}.jpg")) for name in ("img-early", "img-1")
        ),
    )
    live = asyncio.run(run_case(case, config=live_config, root=repo_root))
    assert live.state.retries == 1
    assert len(live.trace.evidence_history) == 2
    return live


def _record(live, trace=None):
    return StoredRecord(
        "recorded-retry", "inline", trace or live.trace.model_dump(mode="json"), (), False
    )


def test_every_retry_replays_its_evidence_proposals_and_reviews(retried_run, config, repo_root):
    record = _record(retried_run)
    fixture = fixture_from_trace(record.trace, reviews="recorded")
    sequences = fixture["response_sequences"]
    assert set(fixture["responses"]) == {"primary:planner"}
    for key in ("primary:evidence_extractor", "primary:candidate_generator", KEY):
        assert len(sequences[key]) == 2
        assert [entry["metadata"][GRAPH_RETRY_INDEX] for entry in sequences[key]] == [0, 1]
        assert sequences[key][0]["response"] != sequences[key][1]["response"]
    assert sequences["arbiter:arbiter"] == []

    state = asyncio.run(replay(record, config=config, root=repo_root, reviews="recorded"))
    assert state.retries == retried_run.state.retries == 1
    assert state.evidence == retried_run.state.evidence
    assert state.candidate_sets == retried_run.state.candidate_sets
    assert state.decisions == retried_run.state.decisions
    assert state.pre_correction_decisions == retried_run.state.pre_correction_decisions
    assert not any("only the last" in gap for gap in replay_gaps(record.trace, "recorded"))


def test_neutral_replay_still_uses_final_extraction(retried_run, config, repo_root):
    record = _record(retried_run)
    fixture = fixture_from_trace(record.trace)
    assert "response_sequences" not in fixture
    state = asyncio.run(replay(record, config=config, root=repo_root))
    assert state.retries == 0
    assert state.evidence == retried_run.state.evidence
    assert any("only the final" in gap for gap in replay_gaps(record.trace))


def test_shorter_route_is_unavailable_instead_of_claiming_a_complete_replay(
    retried_run, config, repo_root
):
    config = config.model_copy(
        update={"graph": config.graph.model_copy(update={"retry_budget": 0})}
    )
    with pytest.raises(ReplayUnavailableError, match="replay_route: consumed 1 of 2"):
        asyncio.run(replay(_record(retried_run), config=config, root=repo_root, reviews="recorded"))


def test_a_route_this_replay_does_not_follow_names_the_settings_that_differ(
    retried_run, config, repo_root
):
    config = config.model_copy(
        update={"graph": config.graph.model_copy(update={"retry_budget": 0})}
    )
    with pytest.raises(ReplayUnavailableError, match=r"other settings.*graph\.retry_budget"):
        asyncio.run(replay(_record(retried_run), config=config, root=repo_root, reviews="recorded"))


def test_recorded_settings_follow_the_recorded_route(retried_run, config, repo_root):
    """The run retried once; a replay configured for no retry still reproduces it."""
    config = config.model_copy(
        update={"graph": config.graph.model_copy(update={"retry_budget": 0})}
    )
    state = asyncio.run(
        replay(
            _record(retried_run),
            config=config,
            root=repo_root,
            reviews="recorded",
            settings="recorded",
        )
    )
    assert state.retries == retried_run.state.retries == 1
    assert state.decisions == retried_run.state.decisions


def test_an_override_wins_over_the_recorded_setting(retried_run, config):
    trace = retried_run.trace.model_dump(mode="json")
    recorded = config_as_recorded(trace, config)
    assert (
        recorded.graph.retry_budget
        == retried_run.trace.runtime_identity.config["graph"]["retry_budget"]
    )
    overridden = config_as_recorded(trace, config, {"retry_budget": 0})
    assert overridden.graph.retry_budget == 0
    # Never taken from a recording: an extra call that decides nothing, and a wall clock.
    assert overridden.graph.vision_shadow is False
    assert overridden.graph.deadline_seconds is None


def test_a_trace_without_settings_is_not_replayed_under_the_callers(retried_run, config, repo_root):
    trace = retried_run.trace.model_dump(mode="json")
    trace["runtime_identity"] = None
    with pytest.raises(ReplayUnavailableError, match="settings: the trace records no run"):
        asyncio.run(
            replay(
                _record(retried_run, trace),
                config=config,
                root=repo_root,
                reviews="recorded",
                settings="recorded",
            )
        )


def test_recorded_settings_this_version_cannot_read_are_refused(retried_run, config):
    trace = retried_run.trace.model_dump(mode="json")
    trace["runtime_identity"]["config"]["graph"]["setting_from_another_version"] = True
    with pytest.raises(ReplayUnavailableError, match="setting_from_another_version"):
        config_as_recorded(trace, config)


def test_unrecorded_reviewer_call_is_unavailable(retried_run, config, repo_root):
    trace = retried_run.trace.model_dump(mode="json")
    trace["review_rounds"][1]["reviewers"] = [
        reviewer
        for reviewer in trace["review_rounds"][1]["reviewers"]
        if reviewer["reviewer"] != "botanical"
    ]
    with pytest.raises(ReplayUnavailableError, match=r"exhausted.*botanical_reviewer"):
        asyncio.run(
            replay(_record(retried_run, trace), config=config, root=repo_root, reviews="recorded")
        )


@pytest.mark.parametrize("missing", ["raw", "candidate_proposal", "revision", "findings"])
def test_incomplete_history_never_substitutes_a_neutral_review(retried_run, missing):
    trace = retried_run.trace.model_dump(mode="json")
    if missing == "raw":
        trace["evidence_history"][0]["raw"] = None
        trace["evidence_history"][0]["raw_sha256"] = None
    elif missing == "candidate_proposal":
        trace["evidence_history"][0]["candidate_proposal"] = None
    elif missing == "revision":
        trace["review_rounds"][0]["evidence_revision"] = None
    else:
        trace["review_rounds"][0]["reviewers"][0]["findings_omitted"] = 1
    with pytest.raises(ReplayUnavailableError):
        fixture_from_trace(trace, reviews="recorded")
    assert len(replay_gaps(trace, "recorded")) > 1
