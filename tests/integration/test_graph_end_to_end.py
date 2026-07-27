"""End-to-end runs over the recorded scenarios. No network, no credentials, no cost."""

from __future__ import annotations

import pytest

from dendro_inspector.schemas.decisions import DecisionStatus
from dendro_inspector.schemas.input import DeclaredObjectType
from dendro_inspector.schemas.taxon import Confidence, Resolution


class TestHappyPath:
    def test_clean_log_yields_a_hedged_genus_answer(self, simple_case, run_scenario):
        result = run_scenario(simple_case, "primary-pass")
        decision = result.state.decisions[0]
        assert decision.selected_taxon == "pinus"
        assert decision.resolution is Resolution.GENUS
        assert decision.confidence is Confidence.MEDIUM
        assert decision.status is DecisionStatus.PROBABLE

    def test_the_whole_declared_path_is_walked(self, simple_case, run_scenario):
        result = run_scenario(simple_case, "primary-pass")
        assert result.trace.executed_nodes == (
            "input_guard",
            "planner",
            "evidence_extractor",
            "evidence_quality",
            "candidate_generator",
            "botanical_reviewer",
            "confusion_reviewer",
            "confidence_reviewer",
            "review_synthesizer",
            "escalation_gate",
            "final_decision",
            "response_composer",
            "tone_layer",
        )

    def test_a_clean_cheap_result_does_not_pay_for_an_arbiter(self, simple_case, run_scenario):
        result = run_scenario(simple_case, "primary-pass")
        assert not result.trace.arbiter_used
        assert "arbiter" not in result.trace.executed_nodes

    def test_response_is_toned_and_structured(self, simple_case, run_scenario):
        result = run_scenario(simple_case, "primary-pass")
        response = result.response
        assert response is not None
        assert response.tone_applied
        assert "Оцінка" in response.human_readable
        assert response.results[0].taxonomic_resolution is Resolution.GENUS


class TestAbstention:
    def test_insufficient_evidence_abstains_with_a_targeted_request(
        self, simple_case, run_scenario
    ):
        result = run_scenario(simple_case, "primary-insufficient")
        decision = result.state.decisions[0]
        assert decision.status is DecisionStatus.INSUFFICIENT_EVIDENCE
        assert decision.resolution is Resolution.UNKNOWN
        assert decision.selected_taxon is None
        assert decision.best_next_photo is not None

    def test_abstention_skips_candidate_generation_entirely(self, simple_case, run_scenario):
        result = run_scenario(simple_case, "primary-insufficient")
        nodes = result.trace.executed_nodes
        assert "photo_planner" in nodes
        assert "candidate_generator" not in nodes
        assert "arbiter" not in nodes

    def test_abstention_does_not_burn_retries(self, simple_case, run_scenario):
        assert run_scenario(simple_case, "primary-insufficient").state.retries == 0


class TestSplitFirewood:
    def test_declared_split_firewood_reconciles_scope_and_photo_target(
        self, simple_case, run_scenario
    ):
        case = simple_case.model_copy(
            update={"declared_object_type": DeclaredObjectType.SPLIT_FIREWOOD}
        )

        result = run_scenario(case, "split-face-colour-only")

        assert result.state.plan is not None
        assert result.state.plan.split_firewood_input
        assert result.state.plan.expect_multiple_subjects
        assert result.state.evidence is not None
        assert result.state.evidence.possible_multiple_taxa
        decision = result.state.decisions[0]
        assert decision.best_next_photo is not None
        assert decision.best_next_photo.target == "prepared_end_grain_and_bark_circumference"

    def test_unknown_declared_type_uses_the_extracted_split_subject(
        self, simple_case, run_scenario
    ):
        case = simple_case.model_copy(update={"declared_object_type": DeclaredObjectType.UNKNOWN})

        result = run_scenario(case, "split-face-colour-only")

        decision = result.state.decisions[0]
        assert decision.best_next_photo is not None
        assert decision.best_next_photo.target == "prepared_end_grain_and_bark_circumference"


class TestMultipleSubjects:
    def test_each_subject_gets_its_own_decision(self, simple_case, run_scenario):
        result = run_scenario(simple_case, "primary-mixed-taxa")
        subjects = {decision.subject_id for decision in result.state.decisions}
        assert subjects == {"foreground_log_1", "background_log_1"}

    def test_subjects_can_reach_different_taxa(self, simple_case, run_scenario):
        result = run_scenario(simple_case, "primary-mixed-taxa")
        taxa = {decision.selected_taxon for decision in result.state.decisions}
        assert taxa == {"pinus", "picea"}

    def test_evidence_does_not_leak_between_subjects(self, simple_case, run_scenario):
        """The fixture deliberately cites a foreign id; the generator must strip it."""
        result = run_scenario(simple_case, "primary-mixed-taxa")
        evidence = result.state.evidence
        assert evidence is not None
        for candidate_set in result.state.candidate_sets:
            own = {o.observation_id for o in evidence.observations_for(candidate_set.subject_id)}
            own |= {
                inference.inference_id
                for inference in evidence.inferences
                if set(inference.derived_from) <= own
            }
            for candidate in candidate_set.ordered:
                cited = set(
                    candidate.supporting_evidence_ids + candidate.contradicting_evidence_ids
                )
                assert cited <= own

    def test_possible_mixed_taxa_forces_escalation(self, simple_case, run_scenario):
        result = run_scenario(simple_case, "primary-mixed-taxa")
        assert result.trace.arbiter_used
        assert "possible_multiple_taxa" in result.trace.escalation_reasons


class TestColourRegression:
    def test_colour_dependence_is_caught_without_model_help(self, simple_case, run_scenario):
        """Every model reviewer in this fixture returns pass. The code check must fire."""
        result = run_scenario(simple_case, "primary-conflict")
        synthesis = result.state.synthesis
        assert synthesis is not None
        categories = {finding.category.value for finding in synthesis.accepted_findings}
        assert "colour_overweighting" in categories

    def test_colour_dependence_lowers_confidence(self, simple_case, run_scenario):
        result = run_scenario(simple_case, "primary-conflict")
        assert result.state.decisions[0].confidence is Confidence.LOW


class TestArbitration:
    def test_species_overclaim_is_capped_and_escalated(self, simple_case, run_scenario):
        result = run_scenario(simple_case, "arbiter-review")
        assert result.trace.arbiter_used
        assert result.state.decisions[0].resolution is not Resolution.SPECIES

    def test_arbiter_rerank_changes_the_answer(self, simple_case, run_scenario):
        """The evidence shows Picea; the primary said Pinus. The arbiter must win on merit."""
        result = run_scenario(simple_case, "arbiter-review")
        assert result.state.decisions[0].selected_taxon == "picea"

    def test_candidate_delta_is_recorded(self, simple_case, run_scenario):
        result = run_scenario(simple_case, "arbiter-review")
        deltas = [
            delta
            for synthesis in (result.state.synthesis, result.state.arbiter_synthesis)
            if synthesis is not None
            for delta in synthesis.candidate_delta
        ]
        assert deltas

    def test_arbiter_findings_face_the_same_admissibility_bar(self, simple_case, run_scenario):
        result = run_scenario(simple_case, "arbiter-review")
        synthesis = result.state.arbiter_synthesis
        assert synthesis is not None
        assert all(finding.reason_code is not None for finding in synthesis.accepted_findings)
        assert all(finding.reason_code is not None for finding in synthesis.rejected_findings)

    def test_decision_records_that_an_arbiter_was_used(self, simple_case, run_scenario):
        result = run_scenario(simple_case, "arbiter-review")
        assert result.state.decisions[0].arbiter_used


class TestTermination:
    @pytest.mark.parametrize(
        "scenario",
        [
            "primary-pass",
            "primary-insufficient",
            "primary-mixed-taxa",
            "primary-conflict",
            "arbiter-review",
        ],
    )
    def test_every_scenario_terminates_within_budget(self, simple_case, run_scenario, scenario):
        result = run_scenario(simple_case, scenario)
        assert result.state.retries <= 1
        assert len(result.trace.events) < 32
        assert result.state.final_response is not None
