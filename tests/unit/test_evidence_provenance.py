"""What a case record must be able to answer after the fact.

The Golden-100 Sonnet 5.5 archive could not say which observations each decision rested on,
which support was refused and why, or why three cases died: the trace held none of it, the
refusal reasons from candidate validation (PR #18) only reached a log line, and a failed case
kept a stderr tail. These tests hold the record to those questions, on the fake provider.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from dendro_inspector.graph.definition import NodeName
from dendro_inspector.graph.executor import partial_trace_of, run_graph
from dendro_inspector.knowledge.evidence_hierarchy import EvidenceTrust
from dendro_inspector.nodes import build_registry
from dendro_inspector.observability.events import NodeStatus, ProviderCallRecord
from dendro_inspector.providers.base import StructuredOutputError
from dendro_inspector.schemas.input import CaseInput, DeclaredObjectType, ImageRef

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def log_case() -> CaseInput:
    return CaseInput(
        case_id="provenance-case",
        images=(ImageRef(image_id="img-1", path=Path("examples/log.jpg")),),
        declared_object_type=DeclaredObjectType.LOG,
    )


class TestTheEvidenceLedger:
    def test_every_observation_is_recorded_with_its_subject_reading_and_trust(
        self, log_case, run_scenario
    ):
        trace = run_scenario(log_case, "primary-pass").trace

        assert trace.evidence_observations
        for record in trace.evidence_observations:
            assert record.subject_id
            assert record.trust in {trust.name.lower() for trust in EvidenceTrust}
            assert 0 < record.tier <= 7

    def test_refused_support_is_recorded_with_a_machine_readable_reason(
        self, log_case, run_scenario
    ):
        trace = run_scenario(log_case, "primary-pass").trace

        [validation] = trace.candidate_validations
        assert validation.admitted
        assert validation.dropped_support
        reasons = {record.reason for record in validation.dropped_support}
        assert reasons <= {
            "unresolved_or_foreign_reference",
            "untrusted_evidence",
            "feature_not_declared",
            "value_not_declared",
        }
        ledger = {record.observation_id for record in trace.evidence_observations}
        admitted = {i for c in validation.admitted for i in c.supporting_evidence_ids}
        assert admitted <= ledger | {"inf-1", "inf-2"}

    def test_the_ledger_carries_no_prose_or_image_payload(self, log_case, run_scenario):
        trace = run_scenario(log_case, "primary-pass").trace
        dumped = json.dumps(trace.model_dump(mode="json")["evidence_observations"])
        assert "notes" not in dumped
        assert "base64" not in dumped


class TestAFailedRunStillExplainsItself:
    def test_the_partial_trace_names_the_failed_node_and_its_validation_errors(
        self, log_case, node_context
    ):
        async def extractor_that_cannot_parse(state, ctx):
            ctx.recorder.record_provider_call(
                ProviderCallRecord(
                    role="primary",
                    adapter="scripted",
                    node=NodeName.EVIDENCE_EXTRACTOR.value,
                    response_model="GeneratedEvidencePacket",
                    attempts=2,
                    validation_failures=2,
                    validation_errors=(
                        "attempt-1:image_limitations.0.lighting:string_pattern_mismatch",
                        "attempt-2:image_limitations.0.lighting:string_pattern_mismatch",
                    ),
                )
            )
            msg = "unusable structured output for node 'evidence_extractor'"
            raise StructuredOutputError(msg)

        registry = {**build_registry(), NodeName.EVIDENCE_EXTRACTOR: extractor_that_cannot_parse}

        with pytest.raises(StructuredOutputError) as caught:
            asyncio.run(run_graph(log_case, node_context, registry))

        partial = partial_trace_of(caught.value)
        assert partial is not None
        last = partial.events[-1]
        assert last.node == NodeName.EVIDENCE_EXTRACTOR.value
        assert last.status is NodeStatus.FAILED
        [call] = last.provider_calls
        assert "attempt-2:image_limitations.0.lighting:string_pattern_mismatch" in (
            call.validation_errors
        )


def _load_batch_script(name: str):
    path = REPO / "scripts" / "batch" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_batch_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("script", ["run_dataset_case", "run_upload_case"])
def test_batch_records_keep_the_cli_failure_report(script):
    failure_record = _load_batch_script(script)._failure_record

    report = {"error": {"type": "StructuredOutputError", "message": "x"}, "trace": {"events": []}}
    assert failure_record(json.dumps(report)) == report
    assert failure_record("Traceback (most recent call last): ...") is None
    assert failure_record(json.dumps({"response": {}})) is None
