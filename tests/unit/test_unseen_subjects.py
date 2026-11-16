"""A subject the extractor named and never saw must not become a verdict.

Live extractions sometimes list a subject that has no observation and appears on no
photograph: a name taken from the prompt's examples, beside the one tree the photographs
show. Each such subject used to get its own "insufficient evidence" section, and its presence
turned a single-subject case into one with several subjects and no designated target.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from dendro_inspector.evaluation.runner import load_cases
from dendro_inspector.nodes.evidence_extractor import drop_unseen_subjects
from dendro_inspector.runner import run_case
from dendro_inspector.schemas.decisions import SubjectRole, TargetSource
from dendro_inspector.schemas.evidence import EvidencePacket, GeneratedEvidencePacket
from tests.conftest import make_config

CASE_ID = "conifer-log-001"
SCENARIO = "primary-pass"
REAL = "foreground_log_1"
NAMED_ONLY: dict[str, Any] = {"subject_id": "detached_leaf", "kind": "detached_part"}
SEEN_UNREAD: dict[str, Any] = {
    "subject_id": "background_tree_1",
    "kind": "standing_tree",
    "image_ids": ["img-1"],
}


def _extraction(repo_root, *extra_subjects: dict[str, Any]) -> dict[str, Any]:
    """The scripted single-subject extraction, with more subjects listed beside it."""
    source = repo_root / "evals" / "fixtures" / f"{SCENARIO}.json"
    fixture: dict[str, Any] = json.loads(source.read_text(encoding="utf-8"))
    extraction = fixture["responses"]["primary:evidence_extractor"]
    extraction["subjects"] = [*extraction["subjects"], *extra_subjects]
    return fixture


def _packet(repo_root, *extra_subjects: dict[str, Any]) -> EvidencePacket:
    extraction = _extraction(repo_root, *extra_subjects)["responses"]["primary:evidence_extractor"]
    return GeneratedEvidencePacket.model_validate(extraction).to_evidence_packet()


def _run(repo_root, fixtures, *extra_subjects: dict[str, Any]):
    """Run the public single-subject case on an extraction that lists more subjects."""
    fixtures.mkdir(exist_ok=True)
    fixture = _extraction(repo_root, *extra_subjects)
    (fixtures / f"{SCENARIO}.json").write_text(json.dumps(fixture), encoding="utf-8")
    case = next(
        case for case in load_cases(repo_root / "evals" / "public") if case.case_id == CASE_ID
    )
    config = make_config(SCENARIO).model_copy(update={"fixtures_root": fixtures})
    return asyncio.run(run_case(case.input, config=config, root=repo_root))


class TestTheRule:
    def test_a_subject_named_and_never_seen_is_left_out(self, repo_root):
        packet, unseen = drop_unseen_subjects(_packet(repo_root, NAMED_ONLY))
        assert unseen == ("detached_leaf",)
        assert [subject.subject_id for subject in packet.subjects] == [REAL]

    def test_every_reading_stays_where_it_was(self, repo_root):
        before = _packet(repo_root, NAMED_ONLY)
        after, _ = drop_unseen_subjects(before)
        assert after.observations == before.observations
        assert after.inferences == before.inferences

    def test_a_subject_seen_on_a_photograph_is_kept_with_nothing_read_on_it(self, repo_root):
        """It was seen. That it could not be read is an answer, not an absence."""
        before = _packet(repo_root, SEEN_UNREAD)
        packet, unseen = drop_unseen_subjects(before)
        assert unseen == ()
        assert packet is before

    def test_a_tree_whose_readings_sit_on_its_component_counts_as_observed(self, repo_root):
        """The shoot's readings are on the log once components are collapsed."""
        before = _packet(repo_root)
        packet, unseen = drop_unseen_subjects(before)
        assert unseen == ()
        assert packet is before

    def test_when_every_subject_is_unseen_the_packet_is_left_as_it_is(self):
        before = EvidencePacket.model_validate(
            {"subjects": [NAMED_ONLY, {"subject_id": "standing_tree"}]}
        )
        packet, unseen = drop_unseen_subjects(before)
        assert unseen == ()
        assert packet is before


class TestTheAnswer:
    def test_the_one_real_subject_is_the_target_again(self, repo_root, tmp_path):
        result = _run(repo_root, tmp_path, NAMED_ONLY, {"subject_id": "standing_tree"})
        response = result.response
        assert result.state.subject_ids == (REAL,)
        assert [item.subject for item in response.results] == [REAL]
        assert response.target_subject_ids == (REAL,)
        assert response.target_source is TargetSource.SINGLE_SUBJECT
        assert [item.subject_role for item in response.results] == [SubjectRole.TARGET]
        assert "none is designated" not in response.human_readable

    def test_the_verdict_is_the_one_the_case_reaches_without_the_extra_names(
        self, repo_root, tmp_path
    ):
        plain = _run(repo_root, tmp_path / "plain")
        with_names = _run(repo_root, tmp_path / "named", NAMED_ONLY)
        assert with_names.state.decisions == plain.state.decisions

    def test_the_trace_still_shows_what_the_extraction_listed(self, repo_root, tmp_path):
        revision = _run(repo_root, tmp_path, NAMED_ONLY).trace.evidence_history[-1]
        assert revision.raw is not None
        assert "detached_leaf" in {subject.subject_id for subject in revision.raw.subjects}
        assert [subject.subject_id for subject in revision.effective.subjects] == [REAL]

    def test_a_subject_seen_and_unread_still_gets_its_own_answer(self, repo_root, tmp_path):
        result = _run(repo_root, tmp_path, SEEN_UNREAD)
        assert set(result.state.subject_ids) == {REAL, "background_tree_1"}
