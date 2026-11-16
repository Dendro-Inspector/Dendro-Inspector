"""Which result is the answer: the target subject, context subjects, and the versioned contract.

A photograph of an oak with a beech behind it produces two verdicts. Presenting the first
as "the answer" reported background trees as the tree that was asked about. These tests hold
the composed answer to saying whose verdict each result is, without changing any verdict.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from dendro_inspector.config import GraphConfig, TargetSelection, load_config
from dendro_inspector.evaluation.runner import load_cases
from dendro_inspector.nodes.consistency import TARGET_MISMATCH, explanation_conflicts
from dendro_inspector.nodes.response_composer import designate_targets
from dendro_inspector.providers.base import StructuredOutputError
from dendro_inspector.runner import run_case
from dendro_inspector.schemas.decisions import (
    LEGACY_RESULT_SCHEMA_VERSION,
    RESULT_SCHEMA_VERSION,
    CaseResponse,
    SubjectRole,
    TargetSource,
    assert_tone_preserved_decision,
)
from dendro_inspector.schemas.evidence import GeneratedEvidencePacket, TargetedEvidencePacket
from tests.conftest import make_config

MIXED_SCENARIO = "primary-mixed-taxa"
FOREGROUND, BACKGROUND = "foreground_log_1", "background_log_1"


class TestDesignation:
    def test_a_lone_subject_is_the_target(self):
        roles, source = designate_targets(("tree_1",))
        assert roles == {"tree_1": SubjectRole.TARGET}
        assert source is TargetSource.SINGLE_SUBJECT

    def test_among_several_none_is_designated(self):
        roles, source = designate_targets(("oak_1", "beech_1"))
        assert set(roles.values()) == {SubjectRole.UNSPECIFIED}
        assert source is TargetSource.NONE

    def test_a_proposal_is_ignored_unless_the_experiment_is_on(self):
        roles, source = designate_targets(("oak_1", "beech_1"), "oak_1")
        assert set(roles.values()) == {SubjectRole.UNSPECIFIED}
        assert source is TargetSource.NONE

    def test_the_extractor_proposal_designates_one_and_the_rest_are_context(self):
        roles, source = designate_targets(("oak_1", "beech_1"), "oak_1", TargetSelection.EXTRACTOR)
        assert roles == {"oak_1": SubjectRole.TARGET, "beech_1": SubjectRole.CONTEXT}
        assert source is TargetSource.EXTRACTOR_PROPOSAL

    @pytest.mark.parametrize("proposed", [None, "elm_9"])
    def test_a_missing_or_unknown_proposal_designates_nothing(self, proposed):
        roles, source = designate_targets(("oak_1", "beech_1"), proposed, TargetSelection.EXTRACTOR)
        assert set(roles.values()) == {SubjectRole.UNSPECIFIED}
        assert source is TargetSource.NONE

    def test_a_lone_subject_needs_no_proposal_under_the_experiment(self):
        roles, source = designate_targets(("tree_1",), None, TargetSelection.EXTRACTOR)
        assert roles == {"tree_1": SubjectRole.TARGET}
        assert source is TargetSource.SINGLE_SUBJECT


class TestResponseSchemaIsUnchangedByDefault:
    def test_the_released_extractor_contract_has_no_proposal_field(self):
        assert "main_subject_id" not in GeneratedEvidencePacket.model_json_schema()["properties"]
        assert "main_subject_id" in TargetedEvidencePacket.model_json_schema()["properties"]

    def test_the_setting_defaults_to_the_code_rule(self, monkeypatch):
        assert GraphConfig().target_selection is TargetSelection.SINGLE_SUBJECT
        monkeypatch.delenv("DENDRO_TARGET_SELECTION", raising=False)
        assert load_config().graph.target_selection is TargetSelection.SINGLE_SUBJECT

    def test_the_setting_survives_the_image_override(self, monkeypatch):
        monkeypatch.setenv("DENDRO_TARGET_SELECTION", "extractor")
        monkeypatch.setenv("DENDRO_IMAGE_MAX_EDGE_PX", "1024")
        graph = load_config().graph
        assert graph.target_selection is TargetSelection.EXTRACTOR
        assert graph.image_max_edge_px == 1024

    def test_an_unknown_setting_fails_at_load(self, monkeypatch):
        monkeypatch.setenv("DENDRO_TARGET_SELECTION", "guess")
        with pytest.raises(ValueError, match="guess"):
            load_config()


def _case(repo_root, case_id: str):
    return next(
        case for case in load_cases(repo_root / "evals" / "public") if case.case_id == case_id
    )


def _run(
    repo_root,
    case_id: str,
    *,
    selection: TargetSelection = TargetSelection.SINGLE_SUBJECT,
    fixtures=None,
):
    case = _case(repo_root, case_id)
    base = make_config(case.scenario)
    update: dict[str, object] = {
        "graph": base.graph.model_copy(update={"target_selection": selection})
    }
    if fixtures is not None:
        update["fixtures_root"] = fixtures
    return asyncio.run(run_case(case.input, config=base.model_copy(update=update), root=repo_root))


def _fixtures_with_proposal(repo_root, tmp_path, main_subject_id: str):
    """The mixed-taxa fixture, with the extractor also naming a main subject."""
    source = repo_root / "evals" / "fixtures" / f"{MIXED_SCENARIO}.json"
    fixture = json.loads(source.read_text(encoding="utf-8"))
    fixture["responses"]["primary:evidence_extractor"]["main_subject_id"] = main_subject_id
    (tmp_path / f"{MIXED_SCENARIO}.json").write_text(json.dumps(fixture), encoding="utf-8")
    return tmp_path


def _verdicts(result) -> list[dict[str, object]]:
    return [decision.model_dump(mode="json") for decision in result.state.decisions]


class TestComposedAnswer:
    def test_a_single_subject_answer_names_its_target(self, repo_root):
        response = _run(repo_root, "conifer-log-001").response
        assert response.schema_version == RESULT_SCHEMA_VERSION
        assert response.target_subject_ids == (FOREGROUND,)
        assert response.target_source is TargetSource.SINGLE_SUBJECT
        assert [result.subject_role for result in response.results] == [SubjectRole.TARGET]
        # One subject reads as it did before roles existed.
        assert "request is about" not in response.human_readable
        assert response.consistency_issues == ()

    def test_two_subjects_have_no_target_and_the_text_says_so(self, repo_root):
        response = _run(repo_root, "mixed-taxa-001").response
        assert response.target_subject_ids == ()
        assert response.target_source is TargetSource.NONE
        assert {result.subject_role for result in response.results} == {SubjectRole.UNSPECIFIED}
        assert "show 2 subjects, and none is designated" in response.human_readable
        assert response.consistency_issues == ()

    def test_the_extractor_proposal_marks_the_target_and_changes_no_verdict(
        self, repo_root, tmp_path
    ):
        """The proposal names a shoot; its tree is the target and is presented first."""
        fixtures = _fixtures_with_proposal(repo_root, tmp_path, "background_shoot_1")
        proposed = _run(
            repo_root, "mixed-taxa-001", selection=TargetSelection.EXTRACTOR, fixtures=fixtures
        )
        response = proposed.response
        assert proposed.state.proposed_target_subject_id == BACKGROUND
        assert response.target_subject_ids == (BACKGROUND,)
        assert response.target_source is TargetSource.EXTRACTOR_PROPOSAL
        assert {result.subject: result.subject_role for result in response.results} == {
            FOREGROUND: SubjectRole.CONTEXT,
            BACKGROUND: SubjectRole.TARGET,
        }
        text = response.human_readable
        assert text.index(f"Subject: {BACKGROUND}") < text.index(f"Subject: {FOREGROUND}")
        assert "This is the subject the request is about." in text
        assert "Also in the frame; the request is not about this subject." in text
        assert response.consistency_issues == ()

        default = _run(repo_root, "mixed-taxa-001")
        assert _verdicts(proposed) == _verdicts(default)
        # Results and decisions keep the extractor's order; only the text leads with the target.
        assert [r.subject for r in response.results] == [
            r.subject for r in default.response.results
        ]

    def test_a_proposal_naming_no_subject_is_dropped(self, repo_root, tmp_path):
        fixtures = _fixtures_with_proposal(repo_root, tmp_path, "nobody")
        result = _run(
            repo_root, "mixed-taxa-001", selection=TargetSelection.EXTRACTOR, fixtures=fixtures
        )
        assert result.state.proposed_target_subject_id is None
        assert result.response.target_subject_ids == ()

    def test_without_the_experiment_the_extractor_may_not_send_the_field(self, repo_root, tmp_path):
        """Off, the response contract is the released one: an extra field is a contract error."""
        fixtures = _fixtures_with_proposal(repo_root, tmp_path, "background_shoot_1")
        with pytest.raises(StructuredOutputError, match="main_subject_id"):
            _run(repo_root, "mixed-taxa-001", fixtures=fixtures)


class TestContract:
    def test_an_answer_stored_before_the_version_existed_reads_as_version_one(self):
        legacy = CaseResponse.model_validate({"case_id": "old"})
        assert legacy.schema_version == LEGACY_RESULT_SCHEMA_VERSION
        assert legacy.target_subject_ids == ()
        assert legacy.target_source is TargetSource.NONE

    def test_targets_and_marked_results_must_agree(self, repo_root):
        response = _run(repo_root, "conifer-log-001").response
        assert TARGET_MISMATCH not in explanation_conflicts(response)
        moved = response.model_copy(update={"target_subject_ids": ()})
        assert TARGET_MISMATCH in explanation_conflicts(moved)

    def test_the_tone_layer_may_not_change_the_target(self, repo_root):
        response = _run(repo_root, "conifer-log-001").response
        assert_tone_preserved_decision(response, response.model_copy(update={"tone_applied": True}))
        with pytest.raises(ValueError, match="which subject the answer is about"):
            assert_tone_preserved_decision(
                response, response.model_copy(update={"target_subject_ids": ()})
            )
