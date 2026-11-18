"""Every submitted photograph is accounted for, or reported as unaccounted for.

An inspection of eleven photographs completed with evidence that named three of them. The
image-reference validator only checks the other direction (every cited photograph was
supplied), so the eight unexamined views left no trace. These tests pin the reverse
account: which photographs were submitted, which reached extraction, which the returned
evidence names, and which an explicit reason accounts for without adding evidence.
"""

from __future__ import annotations

import asyncio
import csv
import importlib.util
import json
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from pydantic import ValidationError

from dendro_inspector.config import Role
from dendro_inspector.evaluation.runner import load_cases
from dendro_inspector.evaluation.trace_replay import StoredRecord, _case_input, replay
from dendro_inspector.graph.state import GraphState
from dendro_inspector.nodes import evidence_extractor
from dendro_inspector.observability.events import RunTrace
from dendro_inspector.observability.evidence_history import EvidenceRevision, packet_digest
from dendro_inspector.providers.base import StructuredOutputError
from dendro_inspector.providers.fake import ScriptedProvider
from dendro_inspector.providers.registry import ProviderRegistry
from dendro_inspector.runner import CaseRunResult, run_case
from dendro_inspector.schemas.evidence import (
    GeneratedEvidencePacket,
    ImageOutcomeReason,
    PhotoStatus,
    Subject,
    photo_coverage,
)
from dendro_inspector.schemas.input import ImageRef
from tests.conftest import make_config

SCENARIO = "primary-pass"
CASE_ID = "conifer-log-001"
ELEVEN = tuple(f"img-{index}" for index in range(1, 12))
NAMED = ("img-2", "img-9", "img-11")
UNNAMED = ("img-1", "img-3", "img-4", "img-5", "img-6", "img-7", "img-8", "img-10")

#: Digests of the primary-pass extraction computed before `image_outcomes` existed. A trace
#: stored then carries these values; changing them makes every older trace fail to load.
RAW_DIGEST_BEFORE_OUTCOMES = "abfa9f829f2571d96e674f115df959870b1a47bd49fe6a574af62486d143972e"
EFFECTIVE_DIGEST_BEFORE_OUTCOMES = (
    "7b2ecc94a2f9f2c0b393fa54367ae026424c15b0a5bad0a1da76fea19ba7720a"
)


def _extraction(repo_root: Path) -> dict[str, Any]:
    fixture = json.loads((repo_root / f"evals/fixtures/{SCENARIO}.json").read_text("utf-8"))
    extraction: dict[str, Any] = fixture["responses"]["primary:evidence_extractor"]
    return extraction


def _naming_three_of_eleven(repo_root: Path) -> dict[str, Any]:
    """The scripted extraction moved onto the reported layout: three photographs named."""
    moved = json.dumps(_extraction(repo_root)).replace('"img-1"', '"img-2"')
    extraction: dict[str, Any] = json.loads(moved)
    extraction["subjects"][0]["image_ids"] = list(NAMED)
    return extraction


def _run(
    repo_root: Path, fixtures: Path, extraction: dict[str, Any], image_ids: tuple[str, ...]
) -> CaseRunResult:
    """Run the public conifer case on ``image_ids`` with ``extraction`` scripted."""
    fixture = json.loads((repo_root / f"evals/fixtures/{SCENARIO}.json").read_text("utf-8"))
    fixture["responses"]["primary:evidence_extractor"] = extraction
    fixtures.mkdir(exist_ok=True)
    (fixtures / f"{SCENARIO}.json").write_text(json.dumps(fixture), encoding="utf-8")
    case = next(
        case for case in load_cases(repo_root / "evals" / "public") if case.case_id == CASE_ID
    ).input
    case = case.model_copy(
        update={
            "images": tuple(
                ImageRef(image_id=image_id, path=Path(f"{image_id}.jpg")) for image_id in image_ids
            )
        }
    )
    config = make_config(SCENARIO).model_copy(update={"fixtures_root": fixtures})
    return asyncio.run(run_case(case, config=config, root=repo_root))


def _verdicts(result: CaseRunResult | GraphState) -> list[tuple[Any, ...]]:
    state = result.state if isinstance(result, CaseRunResult) else result
    return [
        (d.subject_id, d.selected_taxon, d.status, d.resolution, d.confidence)
        for d in state.decisions
    ]


class TestTheAccount:
    def test_the_reported_packet_accounts_for_three_of_eleven(self):
        packet = GeneratedEvidencePacket(subjects=(Subject(subject_id="tree", image_ids=NAMED),))
        packet.validate_image_references(frozenset(ELEVEN))  # still accepted, as before

        coverage = photo_coverage(ELEVEN, ELEVEN, packet)

        assert coverage.accounted_image_ids == NAMED
        assert coverage.missing_image_ids == UNNAMED
        assert coverage.unavailable_image_ids == ()
        assert len(coverage.photos) == 11
        assert [photo.image_id for photo in coverage.photos] == list(ELEVEN)

    def test_a_reason_accounts_for_a_photograph_the_evidence_does_not_name(self):
        packet = GeneratedEvidencePacket.model_validate(
            {
                "subjects": [{"subject_id": "tree", "image_ids": ["img-1"]}],
                "image_outcomes": [{"image_id": "img-2", "reason": "redundant_view"}],
            }
        )

        coverage = photo_coverage(("img-1", "img-2"), ("img-1", "img-2"), packet)

        assert coverage.missing_image_ids == ()
        assert coverage.photos[1].status is PhotoStatus.EXPLAINED
        assert coverage.photos[1].reason is ImageOutcomeReason.REDUNDANT_VIEW

    def test_a_photograph_never_sent_is_unavailable_not_missing(self):
        packet = GeneratedEvidencePacket(subjects=(Subject(subject_id="tree", image_ids=("a",)),))

        coverage = photo_coverage(("a", "b", "c"), ("a", "c"), packet)

        assert coverage.unavailable_image_ids == ("b",)
        assert coverage.missing_image_ids == ("c",)

    def test_a_component_names_its_photograph_before_it_is_folded_away(self, repo_root):
        """Why coverage reads the raw response: collapse drops a component's photographs."""
        extraction = _extraction(repo_root)
        extraction["subjects"][1]["image_ids"] = ["img-3"]
        raw = GeneratedEvidencePacket.model_validate(extraction)
        submitted = ("img-1", "img-3")

        assert photo_coverage(submitted, submitted, raw).missing_image_ids == ()
        effective = raw.to_evidence_packet()
        assert photo_coverage(submitted, submitted, effective).missing_image_ids == ("img-3",)

    def test_an_extraction_that_returned_nothing_accounts_for_nothing(self):
        assert photo_coverage(("a", "b"), ("a", "b")).missing_image_ids == ("a", "b")

    def test_one_outcome_per_photograph(self):
        outcome = {"image_id": "img-1", "reason": "unreadable"}
        with pytest.raises(ValidationError, match="duplicate image outcome"):
            GeneratedEvidencePacket.model_validate({"image_outcomes": [outcome, outcome]})

    def test_outcomes_never_reach_the_packet_downstream_nodes_receive(self):
        packet = GeneratedEvidencePacket.model_validate(
            {"image_outcomes": [{"image_id": "img-1", "reason": "unreadable"}]}
        )
        assert "image_outcomes" not in packet.to_evidence_packet().model_dump()


class TestTheExtractor:
    """The node, with a provider that is not fixture replay: only readable files are sent."""

    @pytest.fixture
    def case(self, simple_case, tmp_path):
        readable = tmp_path / "readable.jpg"
        readable.write_bytes(b"not decoded by the scripted provider")
        return simple_case.model_copy(
            update={
                "images": (
                    ImageRef(image_id="img-1", path=readable),
                    ImageRef(image_id="img-2", path=tmp_path / "never-uploaded.jpg"),
                    ImageRef(image_id="img-3", path=readable),
                )
            }
        )

    def _context(self, node_context, response: GeneratedEvidencePacket):
        provider = ScriptedProvider({"primary:evidence_extractor": response})
        return replace(node_context, providers=ProviderRegistry({Role.PRIMARY: provider}))

    def test_a_reason_for_a_photograph_never_sent_is_refused(self, case, node_context, repo_root):
        payload = _extraction(repo_root)
        payload["image_outcomes"] = [{"image_id": "img-2", "reason": "unreadable"}]
        ctx = self._context(node_context, GeneratedEvidencePacket.model_validate(payload))

        with pytest.raises(StructuredOutputError, match=r"unavailable image ids: \['img-2'\]"):
            asyncio.run(evidence_extractor.run(GraphState(case=case), ctx))

    def test_unavailable_and_missing_are_recorded_apart(self, case, node_context, repo_root):
        ctx = self._context(
            node_context, GeneratedEvidencePacket.model_validate(_extraction(repo_root))
        )

        asyncio.run(evidence_extractor.run(GraphState(case=case), ctx))

        coverage = ctx.recorder.build().evidence_history[-1].photo_coverage
        assert coverage is not None
        assert [(photo.image_id, photo.status) for photo in coverage.photos] == [
            ("img-1", PhotoStatus.REFERENCED),
            ("img-2", PhotoStatus.UNAVAILABLE),
            ("img-3", PhotoStatus.MISSING),
        ]


def _summary_module(repo_root: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "batch_summary_coverage", repo_root / "scripts/batch/summarize_run.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestEndToEnd:
    def test_partial_coverage_is_in_the_trace_and_changes_no_verdict(self, repo_root, tmp_path):
        reference = _run(repo_root, tmp_path / "one", _extraction(repo_root), ("img-1",))
        partial = _run(repo_root, tmp_path / "eleven", _naming_three_of_eleven(repo_root), ELEVEN)

        coverage = partial.trace.evidence_history[-1].photo_coverage
        assert coverage is not None
        assert coverage.accounted_image_ids == NAMED
        assert coverage.missing_image_ids == UNNAMED
        assert _verdicts(partial) == _verdicts(reference)

    def test_an_uninformative_view_accounts_for_a_photograph_and_raises_nothing(
        self, repo_root, tmp_path
    ):
        silent = _run(repo_root, tmp_path / "silent", _extraction(repo_root), ("img-1", "img-2"))
        explained_payload = _extraction(repo_root)
        explained_payload["image_outcomes"] = [
            {"image_id": "img-2", "reason": "no_diagnostic_feature_visible"}
        ]
        explained = _run(repo_root, tmp_path / "explained", explained_payload, ("img-1", "img-2"))

        before = silent.trace.evidence_history[-1].photo_coverage
        after = explained.trace.evidence_history[-1].photo_coverage
        assert before is not None and after is not None
        assert before.missing_image_ids == ("img-2",)
        assert after.missing_image_ids == ()
        assert after.photos[1].reason is ImageOutcomeReason.NO_DIAGNOSTIC_FEATURE_VISIBLE
        # Accounted for, and nothing downstream saw it: same evidence, same verdicts.
        assert explained.state.evidence == silent.state.evidence
        assert _verdicts(explained) == _verdicts(silent)
        assert [d.confidence_band for d in explained.state.decisions] == [
            d.confidence_band for d in silent.state.decisions
        ]

    def test_a_replay_holds_every_photograph_the_run_was_given(self, repo_root, tmp_path, config):
        extraction = _naming_three_of_eleven(repo_root)
        extraction["image_outcomes"] = [{"image_id": "img-1", "reason": "redundant_view"}]
        live = _run(repo_root, tmp_path / "live", extraction, ELEVEN)
        record = StoredRecord(CASE_ID, "inline", live.trace.model_dump(mode="json"), (), False)

        assert sorted(_case_input(record).image_ids) == sorted(ELEVEN)
        # The recorded outcome names a photograph the evidence does not: the replay must
        # still hold it, or its own extraction would be refused. The case's location is not
        # recorded, so only the verdict fields are compared.
        state = asyncio.run(replay(record, config=config, root=repo_root, reviews="recorded"))
        assert _verdicts(state) == _verdicts(live)


class TestOlderTraces:
    def test_a_packet_recorded_before_outcomes_keeps_its_digest(self, repo_root):
        raw = GeneratedEvidencePacket.model_validate(_extraction(repo_root))

        assert packet_digest(raw) == RAW_DIGEST_BEFORE_OUTCOMES
        assert packet_digest(raw.to_evidence_packet()) == EFFECTIVE_DIGEST_BEFORE_OUTCOMES

    def test_an_outcome_is_part_of_the_raw_digest(self, repo_root):
        payload = _extraction(repo_root)
        payload["image_outcomes"] = [{"image_id": "img-1", "reason": "redundant_view"}]

        assert packet_digest(GeneratedEvidencePacket.model_validate(payload)) != (
            RAW_DIGEST_BEFORE_OUTCOMES
        )

    def test_a_trace_without_coverage_still_loads_and_reads_as_unknown(self, repo_root, tmp_path):
        live = _run(repo_root, tmp_path / "live", _extraction(repo_root), ("img-1",))
        stored = live.trace.model_dump(mode="json")
        stored["trace_schema_version"] = 5
        for revision in stored["evidence_history"]:
            del revision["photo_coverage"]

        trace = RunTrace.model_validate(stored)
        assert all(revision.photo_coverage is None for revision in trace.evidence_history)
        assert EvidenceRevision.model_validate(stored["evidence_history"][0]).photo_coverage is None
        row = _summary_module(repo_root).case_row({"exit_code": 0, "result": {"trace": stored}})
        assert row["photos_submitted"] is None
        assert row["missing_photo_ids"] is None


def _coverage(*photos: tuple[str, str]) -> dict[str, Any]:
    return {"photo_coverage": {"photos": [{"image_id": i, "status": s} for i, s in photos]}}


def _record(case_id: str, exit_code: int, **trace_at: Any) -> dict[str, Any]:
    return {"case_id": case_id, "exit_code": exit_code, "wall_seconds": 1, **trace_at}


class TestBatchSummary:
    @pytest.fixture
    def summary(self, repo_root):
        return _summary_module(repo_root)

    @pytest.fixture
    def records(self):
        partial = {
            "evidence_history": [
                _coverage(("img-1", "missing")),  # superseded by the retry's extraction
                _coverage(("img-1", "referenced"), ("img-2", "missing"), ("img-3", "missing")),
            ]
        }
        failed = {"evidence_history": [_coverage(("img-1", "explained"), ("img-2", "unavailable"))]}
        older = {"evidence_history": [{"revision": 1}]}
        return [
            _record("succeeded-partial", 0, result={"response": {}, "trace": partial}),
            _record("failed-complete", 1, failure={"trace": failed}),
            _record("older-trace", 0, result={"response": {}, "trace": older}),
            _record("no-trace", 1),
        ]

    def test_coverage_is_read_whatever_the_exit_code(self, summary, records):
        rows = [summary.case_row(record) for record in records]

        assert [row["photos_accounted"] for row in rows] == [1, 1, None, None]
        assert [row["photos_submitted"] for row in rows] == [3, 2, None, None]
        assert [row["photos_unavailable"] for row in rows] == [0, 1, None, None]
        assert [row["missing_photo_ids"] for row in rows] == [["img-2", "img-3"], [], None, None]

    def test_unknown_coverage_is_counted_apart_and_never_as_complete(self, summary, records):
        result = summary.summarize([summary.case_row(record) for record in records])

        assert result["photo_coverage"] == {
            "cases_measured": 2,
            "cases_unknown": 2,
            "cases_incomplete": 1,
            "photos_submitted": 5,
            "photos_accounted": 2,
            "photos_unavailable": 1,
            "photos_missing": 2,
            "incomplete": [
                {"case_id": "succeeded-partial", "missing_photo_ids": ["img-2", "img-3"]}
            ],
        }

    def test_the_csv_lists_missing_ids_and_leaves_unknown_empty(self, summary, records, tmp_path):
        (tmp_path / "cases").mkdir()
        for record in records:
            (tmp_path / "cases" / f"{record['case_id']}.json").write_text(
                json.dumps(record), encoding="utf-8"
            )

        summary.main(tmp_path)

        with (tmp_path / "cases.csv").open(encoding="utf-8", newline="") as handle:
            rows = {row["case_id"]: row for row in csv.DictReader(handle)}
        assert json.loads(rows["succeeded-partial"]["missing_photo_ids"]) == ["img-2", "img-3"]
        assert rows["failed-complete"]["missing_photo_ids"] == "[]"
        assert rows["older-trace"]["missing_photo_ids"] == ""
        assert rows["older-trace"]["photos_submitted"] == ""
