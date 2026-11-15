"""Evidence revisions preserve what a retry or normalization used to overwrite."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from dendro_inspector.evaluation.trace_replay import (
    ReplayUnavailableError,
    StoredRecord,
    evidence_history_from_trace,
    fixture_from_trace,
    packet_from_trace,
    proposals_from_trace,
    replay_fidelity,
    replay_gaps,
)
from dendro_inspector.knowledge.candidate_validation import validate_candidate_set_with_report
from dendro_inspector.observability.events import MAX_LEDGER_OBSERVATIONS, RunTrace
from dendro_inspector.observability.evidence_history import EvidenceRevision, packet_digest
from dendro_inspector.observability.trace import TraceRecorder
from dendro_inspector.schemas.candidates import CandidateProposal
from dendro_inspector.schemas.evidence import GeneratedEvidencePacket
from dendro_inspector.schemas.input import CaseInput


@pytest.fixture
def scripted(repo_root):
    path = repo_root / "evals/fixtures/primary-pass.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    responses = payload["responses"]
    return (
        GeneratedEvidencePacket.model_validate(responses["primary:evidence_extractor"]),
        CandidateProposal.model_validate(responses["primary:candidate_generator"]),
    )


def _record(scripted, knowledge):
    raw, proposal = scripted
    packet = raw.to_evidence_packet()
    recorder = TraceRecorder("history")
    recorder.record_evidence(packet, generated=raw)
    recorder.record_candidate_proposal(proposal)
    for group in proposal.sets:
        recorder.record_candidate_validation(
            group, validate_candidate_set_with_report(group, packet, knowledge)
        )
    return recorder


def test_raw_component_attribution_and_notes_survive_normalization(scripted, knowledge):
    raw, proposal = scripted
    trace = _record(scripted, knowledge).build()
    (revision,) = trace.evidence_history
    assert revision.raw is not None
    assert revision.raw == raw
    assert revision.effective == raw.to_evidence_packet()
    assert revision.candidate_proposal == proposal
    assert revision.raw.observations[1].subject_id == "attached_shoot_1"
    assert revision.effective.observations[1].subject_id == "foreground_log_1"
    assert revision.effective.observations[1].source_component_id == "attached_shoot_1"
    assert revision.raw.observations[1].notes == revision.effective.observations[1].notes
    assert revision.effective.image_limitations[0].notes == raw.image_limitations[0].notes
    assert revision.raw_sha256 == packet_digest(raw)
    assert revision.effective_sha256 == packet_digest(raw.to_evidence_packet())
    assert revision.raw_sha256 != revision.effective_sha256


def test_retry_reuses_local_ids_without_erasing_prior_evidence_or_candidates(scripted, knowledge):
    raw, proposal = scripted
    recorder = _record(scripted, knowledge)
    changed = raw.model_dump(mode="json")
    changed["observations"][1]["notes"] = "Second extraction: a different reading."
    second = GeneratedEvidencePacket.model_validate(changed)

    recorder.record_evidence(second.to_evidence_packet(), generated=second, retry_index=1)
    trace = recorder.build()
    first, last = trace.evidence_history
    assert first.raw == raw
    assert first.candidate_proposal == proposal
    assert last.raw == second
    assert last.candidate_proposal is None
    assert (first.retry_index, last.retry_index) == (0, 1)
    assert first.reference("obs-2") == "evidence:1/obs-2"
    assert last.reference("obs-2") == "evidence:2/obs-2"
    assert first.effective_sha256 != last.effective_sha256
    assert not trace.candidate_validations
    assert trace.candidate_validation_history
    assert all(item.evidence_revision == 1 for item in trace.candidate_validation_history)
    assert len(trace.observation_uses) == len(raw.observations) + len(second.observations)
    assert len({item.evidence_ref for item in trace.observation_uses}) == len(trace.observation_uses)


def test_every_observation_has_a_use_record_even_when_no_candidate_cites_it(scripted, knowledge):
    raw, _ = scripted
    trace = _record(scripted, knowledge).build()
    uses = {item.observation_id: item for item in trace.observation_uses}
    assert set(uses) == {item.observation_id for item in raw.observations}
    assert uses["obs-2"].cited
    assert "pinus" in uses["obs-2"].supporting_taxa
    assert uses["obs-3"].candidate_validation_ran
    assert not uses["obs-3"].cited
    assert not uses["obs-3"].supporting_taxa
    assert not uses["obs-3"].rejected_support


@pytest.mark.parametrize("side", ["raw", "effective"])
def test_modified_artifact_content_is_rejected_instead_of_silently_replayed(
    scripted, knowledge, side
):
    revision = _record(scripted, knowledge).build().evidence_history[0].model_dump(mode="json")
    revision[side]["observations"][0]["value"] = "changed_value"
    with pytest.raises(ValidationError, match="SHA-256"):
        EvidenceRevision.model_validate(revision)


def test_artifacts_and_validation_history_survive_json_round_trip(scripted, knowledge):
    trace = _record(scripted, knowledge).build()
    payload = json.loads(trace.model_dump_json())
    restored = RunTrace.model_validate(payload)
    assert restored.evidence_history == trace.evidence_history
    assert restored.candidate_validation_history == trace.candidate_validation_history
    assert restored.observation_uses == trace.observation_uses
    assert evidence_history_from_trace(payload) == trace.evidence_history


def test_replay_uses_original_proposals_and_raw_extraction_instead_of_reconstruction(
    scripted, knowledge
):
    raw, proposal = scripted
    trace = _record(scripted, knowledge).build().model_dump(mode="json")
    assert packet_from_trace(trace) == raw.to_evidence_packet()
    assert proposals_from_trace(trace) == [
        group.model_dump(mode="json") for group in proposal.sets
    ]
    fixture = fixture_from_trace(trace)
    assert fixture["responses"]["primary:evidence_extractor"] == raw.model_dump(mode="json")
    assert fixture["responses"]["primary:candidate_generator"] == proposal.model_dump(mode="json")


def test_missing_raw_output_is_explicit_and_never_invented(scripted):
    raw, _ = scripted
    recorder = TraceRecorder("legacy-caller")
    recorder.record_evidence(raw.to_evidence_packet())
    trace = recorder.build()
    assert trace.evidence_history[0].raw is None
    assert trace.evidence_history[0].raw_sha256 is None
    gaps = replay_gaps(trace.model_dump(mode="json"))
    assert any(gap.startswith("raw_evidence:") for gap in gaps)


def test_out_of_order_revisions_are_not_mistaken_for_the_latest_packet(scripted, knowledge):
    raw, _ = scripted
    recorder = _record(scripted, knowledge)
    recorder.record_evidence(raw.to_evidence_packet(), generated=raw, retry_index=1)
    payload = recorder.build().model_dump(mode="json")
    payload["evidence_history"].reverse()
    with pytest.raises(ReplayUnavailableError, match="contiguous and ordered"):
        packet_from_trace(payload)


def test_compact_ledger_limit_does_not_truncate_complete_artifacts(scripted):
    raw, _ = scripted
    first = raw.observations[0]
    observations = tuple(
        first.model_copy(update={"observation_id": f"reading-{index}"})
        for index in range(MAX_LEDGER_OBSERVATIONS + 1)
    )
    expanded = raw.model_copy(update={"observations": observations, "inferences": ()})
    recorder = TraceRecorder("large-extraction")
    recorder.record_evidence(expanded.to_evidence_packet(), generated=expanded)
    trace = recorder.build()
    assert len(trace.evidence_observations) == MAX_LEDGER_OBSERVATIONS
    assert len(trace.evidence_history[0].effective.observations) == MAX_LEDGER_OBSERVATIONS + 1
    assert len(trace.observation_uses) == MAX_LEDGER_OBSERVATIONS + 1
    record = StoredRecord("large-extraction", "inline", trace.model_dump(mode="json"), (), False)
    assert replay_fidelity(record) == (f"reading-{MAX_LEDGER_OBSERVATIONS}",)


def test_live_runner_records_provider_packet_before_component_collapse(simple_case, run_scenario):
    result = run_scenario(simple_case, "primary-pass")
    (revision,) = result.trace.evidence_history
    assert revision.raw is not None
    assert len(revision.raw.subjects) > len(revision.effective.subjects)
    assert revision.effective == result.state.evidence
    assert revision.candidate_proposal is not None
    assert result.trace.candidate_validation_history


def test_controlled_empty_input_still_records_its_evidence_revision(run_scenario):
    result = run_scenario(CaseInput(case_id="empty-history"), "primary-pass")
    (revision,) = result.trace.evidence_history
    assert revision.raw is None
    assert revision.effective.context_limitations == ("input_unusable",)
    assert not revision.effective.observations


def test_pre_history_trace_remains_readable():
    trace = RunTrace.model_validate({"case_id": "old", "trace_schema_version": 2})
    assert trace.evidence_history == ()
    assert trace.candidate_validation_history == ()
    assert trace.observation_uses == ()
