"""Open-world discovery through the whole graph, against a scripted fake provider.

Fixture ``open-world-uncovered-taxon`` scripts the planner, the extractor and the candidate
generator only. Any later model call raises ``UnscriptedCallError``, so "a run that admits
nothing skips review" is enforced by construction rather than asserted.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import yaml

from dendro_inspector.config import AppConfig, GraphConfig, KnowledgeConfig
from dendro_inspector.runner import CaseRunResult, run_case
from dendro_inspector.schemas.decisions import DecisionStatus
from dendro_inspector.schemas.reference import ReferenceStatus, SourceTrust
from dendro_inspector.schemas.taxon import Confidence, Resolution
from tests.conftest import make_config

SCENARIO = "open-world-uncovered-taxon"
TAXON = "aesculus_hippocastanum"


def _run(
    case,
    repo_root: Path,
    *,
    open_world: bool,
    references: Path | None = None,
) -> CaseRunResult:
    base = make_config(SCENARIO)
    config: AppConfig = base.model_copy(
        update={
            "graph": GraphConfig(open_world_discovery=open_world),
            "knowledge": KnowledgeConfig(reference_records=references),
        }
    )
    return asyncio.run(run_case(case, config=config, root=repo_root))


def _records(tmp_path: Path, **overrides: object) -> Path:
    record: dict[str, object] = {
        "taxon": TAXON,
        "source_id": "example_flora",
        "source_url": f"https://references.example.org/taxon/{TAXON}",
        "retrieved_at": "2026-01-15T10:00:00+00:00",
        "name_status": "accepted",
        "accepted_taxon": TAXON,
        "documented_features": [{"feature": "fruit.husk", "values": ["spiny_green"]}],
    }
    record.update(overrides)
    path = tmp_path / "references.yaml"
    path.write_text(yaml.safe_dump({"records": [record]}), encoding="utf-8")
    return path


class TestClosedWorldIsUnchanged:
    def test_the_coverage_gate_still_exits_before_any_further_model_call(
        self, standing_tree_case, repo_root
    ):
        result = _run(standing_tree_case, repo_root, open_world=False)

        assert result.trace.executed_nodes == (
            "input_guard",
            "planner",
            "evidence_extractor",
            "evidence_quality",
            "photo_planner",
            "response_composer",
            "tone_layer",
        )
        decision = result.state.decisions[0]
        assert decision.status is DecisionStatus.KNOWLEDGE_COVERAGE_GAP
        assert decision.open_world_candidates == ()


class TestOpenWorldDiscovery:
    def test_nothing_admitted_skips_review_and_arbitration(self, standing_tree_case, repo_root):
        result = _run(standing_tree_case, repo_root, open_world=True)

        assert result.trace.executed_nodes == (
            "input_guard",
            "planner",
            "evidence_extractor",
            "evidence_quality",
            "candidate_generator",
            "final_decision",
            "response_composer",
            "tone_layer",
        )
        called = {event.node for event in result.trace.events if event.provider_calls}
        assert called == {"planner", "evidence_extractor", "candidate_generator"}
        assert not result.trace.arbiter_used

    def test_the_uncovered_taxon_is_retained_and_never_selected(
        self, standing_tree_case, repo_root
    ):
        result = _run(standing_tree_case, repo_root, open_world=True)

        decision = result.state.decisions[0]
        assert decision.status is DecisionStatus.KNOWLEDGE_COVERAGE_GAP
        assert decision.selected_taxon is None
        assert decision.resolution is Resolution.UNKNOWN
        (candidate,) = decision.open_world_candidates
        assert candidate.taxon == TAXON
        assert candidate.supporting_evidence_ids == ("obs-leaf", "obs-fruit")

    def test_no_silent_substitution_of_a_covered_taxon(self, standing_tree_case, repo_root):
        """The fixture also proposes juglans_regia on a leaf its card does not list."""
        result = _run(standing_tree_case, repo_root, open_world=True)

        decision = result.state.decisions[0]
        assert decision.selected_taxon is None
        assert all(not cs.candidates for cs in result.state.candidate_sets)
        assert "juglans_regia" not in {c.taxon for c in decision.open_world_candidates}

    def test_confidence_stays_bounded_by_the_photograph(self, standing_tree_case, repo_root):
        result = _run(standing_tree_case, repo_root, open_world=True)

        (candidate,) = result.state.decisions[0].open_world_candidates
        assert candidate.proposed_score.value == "strong"
        assert candidate.score.value == "moderate"
        assert candidate.image_confidence_ceiling is Confidence.MEDIUM
        assert result.state.decisions[0].confidence is Confidence.LOW

    def test_the_reader_is_told_what_was_seen_and_what_the_cards_lacked(
        self, standing_tree_case, repo_root
    ):
        result = _run(standing_tree_case, repo_root, open_world=True)

        assert result.response is not None
        text = result.response.human_readable
        assert "Aesculus hippocastanum" in text
        assert "not identified" in text
        assert "fruit.husk" in text

    def test_without_a_source_nobody_checked(self, standing_tree_case, repo_root):
        result = _run(standing_tree_case, repo_root, open_world=True)

        (candidate,) = result.state.decisions[0].open_world_candidates
        assert candidate.reference_status is ReferenceStatus.UNAVAILABLE


class TestReferenceVerificationInTheGraph:
    def test_a_saved_matching_record_is_cited_but_cannot_verify(
        self, standing_tree_case, repo_root, tmp_path
    ):
        """A file asserts its provenance; nothing checked it. Matches stay provisional."""
        plain = _run(standing_tree_case, repo_root, open_world=True)
        cited = _run(standing_tree_case, repo_root, open_world=True, references=_records(tmp_path))

        (before,) = plain.state.decisions[0].open_world_candidates
        (after,) = cited.state.decisions[0].open_world_candidates
        assert after.reference_status is ReferenceStatus.PROVISIONAL
        (citation,) = after.references
        assert citation.source_trust is SourceTrust.OPERATOR_SUPPLIED
        assert citation.matched_features == ("fruit.husk",)
        assert citation.source_url == f"https://references.example.org/taxon/{TAXON}"
        # Nothing evidence-bounded moved, and neither did the verdict.
        assert (
            after.model_copy(update={"reference_status": before.reference_status, "references": ()})
            == before
        )
        assert cited.state.decisions[0].status is plain.state.decisions[0].status
        assert cited.state.decisions[0].confidence is plain.state.decisions[0].confidence

    @pytest.mark.parametrize(
        ("overrides", "expected"),
        [
            (
                {"name_status": "synonym", "accepted_taxon": "aesculus_turbinata"},
                ReferenceStatus.DISPUTED,
            ),
            (
                {"taxon": "castanea_sativa", "accepted_taxon": "castanea_sativa"},
                ReferenceStatus.PROVISIONAL,
            ),
        ],
        ids=["synonym", "not-found"],
    )
    def test_other_source_answers(
        self, standing_tree_case, repo_root, tmp_path, overrides, expected
    ):
        result = _run(
            standing_tree_case,
            repo_root,
            open_world=True,
            references=_records(tmp_path, **overrides),
        )
        (candidate,) = result.state.decisions[0].open_world_candidates
        assert candidate.reference_status is expected

    def test_a_broken_records_file_does_not_break_the_run(
        self, standing_tree_case, repo_root, tmp_path
    ):
        broken = tmp_path / "references.yaml"
        broken.write_text("records: [unterminated", encoding="utf-8")

        result = _run(standing_tree_case, repo_root, open_world=True, references=broken)

        (candidate,) = result.state.decisions[0].open_world_candidates
        assert candidate.reference_status is ReferenceStatus.UNAVAILABLE
