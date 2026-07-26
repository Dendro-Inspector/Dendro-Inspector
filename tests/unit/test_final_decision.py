"""Resolution and identity are one monotonic, deterministic final claim."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from evil_duck_dendro.graph.state import GraphState
from evil_duck_dendro.nodes.final_decision import (
    apply_reranking,
    cap_resolution,
    decide_subject,
    resolve_identity,
)
from evil_duck_dendro.nodes.response_composer import build_result
from evil_duck_dendro.schemas.candidates import Candidate, CandidateSet, SupportStrength
from evil_duck_dendro.schemas.decisions import DecisionStatus, FinalDecision
from evil_duck_dendro.schemas.evidence import (
    AttachmentStatus,
    EvidencePacket,
    Observation,
    ObservationSource,
    Subject,
)
from evil_duck_dendro.schemas.reviews import (
    AdmittedRerank,
    FindingCategory,
    FindingStatus,
    Impact,
    RequiredAction,
    Reviewer,
    ReviewFinding,
    ReviewResult,
    ReviewStatus,
    ReviewSynthesis,
    Severity,
)
from evil_duck_dendro.schemas.taxon import (
    Provenance,
    Resolution,
    SourceType,
    TaxonCard,
    TaxonIdentity,
    resolution_rank,
)

NON_UNKNOWN_RESOLUTIONS = (
    Resolution.FAMILY,
    Resolution.GENUS,
    Resolution.SPECIES_GROUP,
    Resolution.SPECIES,
)


def _card(*supported: Resolution) -> TaxonCard:
    return TaxonCard(
        taxon_id="pinus_sylvestris",
        display_name="Pinus sylvestris",
        native_resolution=Resolution.SPECIES,
        broader_identities=(
            TaxonIdentity(
                resolution=Resolution.GENUS,
                taxon_id="pinus",
                display_name="Pinus",
            ),
            TaxonIdentity(
                resolution=Resolution.FAMILY,
                taxon_id="pinaceae",
                display_name="Pinaceae",
            ),
        ),
        supported_resolution=supported,
        provenance=Provenance(source="test fixture", source_type=SourceType.INFERRED),
    )


def _observation(observation_id: str, feature: str, value: str) -> Observation:
    detachable = feature.split(".", 1)[0] in {
        "leaf",
        "needles",
        "fruit",
        "cones",
        "branch",
        "bud",
        "seed",
        "nut",
        "acorn",
        "samara",
    }
    return Observation(
        observation_id=observation_id,
        feature=feature,
        value=value,
        subject_id="tree_1",
        source=ObservationSource.IMAGE,
        image_id="img-1",
        attachment=AttachmentStatus.CONFIRMED_ATTACHED if detachable else None,
    )


def _state(simple_case, observations: tuple[Observation, ...], candidates: tuple[Candidate, ...]):
    return GraphState(
        case=simple_case,
        evidence=EvidencePacket(
            subjects=(Subject(subject_id="tree_1"),),
            observations=observations,
        ),
        candidate_sets=(CandidateSet(subject_id="tree_1", candidates=candidates),),
    )


class TestResolutionCap:
    @pytest.mark.parametrize("claimed", tuple(Resolution))
    @pytest.mark.parametrize("card_ceiling", NON_UNKNOWN_RESOLUTIONS)
    def test_exhaustive_matrix_never_returns_a_narrower_claim(self, claimed, card_ceiling):
        actual = cap_resolution(claimed, _card(card_ceiling))
        assert resolution_rank(actual) <= resolution_rank(claimed)

        expected = (
            Resolution.UNKNOWN
            if claimed is Resolution.UNKNOWN
            else min((claimed, card_ceiling), key=resolution_rank)
        )
        assert actual is expected

    @pytest.mark.parametrize("claimed", tuple(Resolution))
    def test_missing_card_fails_closed_without_upgrading(self, claimed):
        actual = cap_resolution(claimed, None)
        assert actual is Resolution.UNKNOWN
        assert resolution_rank(actual) <= resolution_rank(claimed)

    def test_broad_claim_stays_broad_when_card_lists_only_species(self):
        assert cap_resolution(Resolution.FAMILY, _card(Resolution.SPECIES)) is Resolution.FAMILY


class TestIdentitySelection:
    def test_missing_species_group_identity_broadens_to_genus(self):
        identity = resolve_identity(_card(Resolution.SPECIES), Resolution.SPECIES_GROUP)
        assert identity is not None
        assert identity.resolution is Resolution.GENUS
        assert identity.taxon_id == "pinus"

    def test_unknown_resolution_has_no_taxon_identity(self):
        assert resolve_identity(_card(Resolution.SPECIES), Resolution.UNKNOWN) is None

    def test_missing_card_has_no_taxon_identity(self):
        assert resolve_identity(None, Resolution.GENUS) is None

    def test_missing_broader_identity_fails_closed(self):
        card = TaxonCard(
            taxon_id="pinus",
            display_name="Pinus",
            native_resolution=Resolution.GENUS,
            supported_resolution=(Resolution.GENUS,),
            provenance=Provenance(source="test fixture", source_type=SourceType.INFERRED),
        )
        assert resolve_identity(card, Resolution.FAMILY) is None


class TestFinalDecisionIdentityContract:
    def test_unknown_resolution_cannot_keep_a_taxon_identity(self):
        with pytest.raises(ValidationError, match="resolution=unknown"):
            FinalDecision(
                subject_id="tree_1",
                selected_taxon="acer_saccharinum",
                selected_taxon_display_name="Acer saccharinum",
            )

    @pytest.mark.parametrize(
        "fields",
        [
            {"selected_taxon": "acer"},
            {"selected_taxon_display_name": "Acer"},
            {},
        ],
    )
    def test_non_unknown_resolution_requires_complete_identity(self, fields):
        with pytest.raises(ValidationError):
            FinalDecision(subject_id="tree_1", resolution=Resolution.GENUS, **fields)


class TestStructuredAndRenderedIdentity:
    def test_all_candidates_removed_abstains_with_targeted_photo(self, simple_case, node_context):
        observation = _observation("support", "bark.texture", "scaly_plates")
        empty = CandidateSet(subject_id="tree_1")
        decision = decide_subject(
            _state(simple_case, (observation,), ()),
            node_context,
            empty,
        )
        assert decision.status is DecisionStatus.INSUFFICIENT_EVIDENCE
        assert decision.selected_taxon is None
        assert decision.best_next_photo is not None

    def test_species_hypothesis_broadened_by_foliage_renders_only_genus(
        self, simple_case, node_context
    ):
        observation = _observation("support", "leaf.dissection", "deeply_dissected_narrow_lobes")
        candidate = Candidate(
            taxon="acer_saccharinum",
            resolution=Resolution.SPECIES,
            supporting_evidence_ids=("support",),
            score=SupportStrength.STRONG,
            rank=1,
        )
        decision = decide_subject(
            _state(simple_case, (observation,), (candidate,)),
            node_context,
            CandidateSet(subject_id="tree_1", candidates=(candidate,)),
        )

        assert decision.resolution is Resolution.GENUS
        assert decision.selected_taxon == "acer"
        assert decision.selected_taxon_display_name == "Acer (клен)"
        assert "Acer saccharinum" not in build_result(decision, "en").verdict
        assert "Acer (клен) (genus)" in build_result(decision, "en").verdict

    def test_genus_hypothesis_broadened_by_silhouette_renders_only_family(
        self, simple_case, node_context
    ):
        observation = _observation("support", "trunk.form", "straight_long")
        candidate = Candidate(
            taxon="pinus",
            resolution=Resolution.GENUS,
            supporting_evidence_ids=("support",),
            score=SupportStrength.MODERATE,
            rank=1,
        )
        decision = decide_subject(
            _state(simple_case, (observation,), (candidate,)),
            node_context,
            CandidateSet(subject_id="tree_1", candidates=(candidate,)),
        )

        result = build_result(decision, "en")
        assert decision.resolution is Resolution.FAMILY
        assert decision.selected_taxon == "pinaceae"
        assert decision.selected_taxon_display_name == "Pinaceae (соснові)"
        assert "Pinus (сосна)" not in result.verdict
        assert "Pinaceae (соснові) (family)" in result.verdict

    def test_alternatives_collapsing_to_same_family_are_omitted(self, simple_case, node_context):
        observation = _observation("support", "trunk.form", "straight_long")
        leader = Candidate(
            taxon="pinus",
            resolution=Resolution.GENUS,
            supporting_evidence_ids=("support",),
            score=SupportStrength.MODERATE,
            rank=1,
        )
        runner_up = Candidate(
            taxon="picea",
            resolution=Resolution.GENUS,
            score=SupportStrength.WEAK,
            rank=2,
        )
        candidates = CandidateSet(subject_id="tree_1", candidates=(leader, runner_up))

        decision = decide_subject(
            _state(simple_case, (observation,), candidates.candidates),
            node_context,
            candidates,
        )

        assert decision.selected_taxon == "pinaceae"
        assert decision.nearest_alternative is None

    def test_alternative_is_broadened_to_the_same_family_resolution(
        self, simple_case, node_context
    ):
        observation = _observation("support", "trunk.form", "straight_long")
        leader = Candidate(
            taxon="pinus",
            resolution=Resolution.GENUS,
            supporting_evidence_ids=("support",),
            score=SupportStrength.MODERATE,
            rank=1,
        )
        runner_up = Candidate(
            taxon="quercus",
            resolution=Resolution.GENUS,
            score=SupportStrength.WEAK,
            rank=2,
        )
        candidates = CandidateSet(subject_id="tree_1", candidates=(leader, runner_up))

        decision = decide_subject(
            _state(simple_case, (observation,), candidates.candidates),
            node_context,
            candidates,
        )

        assert decision.resolution is Resolution.FAMILY
        assert decision.nearest_alternative == "fagaceae"

    def test_species_contradiction_does_not_survive_a_genus_identity(
        self, simple_case, node_context
    ):
        support = _observation("support", "leaf.dissection", "deeply_dissected_narrow_lobes")
        species_only_contradiction = _observation(
            "contradiction", "leaf.underside", "white_tomentose"
        )
        candidate = Candidate(
            taxon="acer_saccharinum",
            resolution=Resolution.SPECIES,
            supporting_evidence_ids=("support",),
            contradicting_evidence_ids=("contradiction",),
            score=SupportStrength.MODERATE,
            rank=1,
        )
        candidates = CandidateSet(subject_id="tree_1", candidates=(candidate,))

        decision = decide_subject(
            _state(simple_case, (support, species_only_contradiction), (candidate,)),
            node_context,
            candidates,
        )

        assert decision.selected_taxon == "acer"
        assert decision.status is DecisionStatus.PROBABLE
        assert decision.strongest_contradiction is None


class TestFindingBoundReranking:
    @staticmethod
    def _ranking(*taxa: str) -> CandidateSet:
        return CandidateSet(
            subject_id="tree_1",
            candidates=tuple(
                Candidate(taxon=taxon, resolution=Resolution.GENUS, rank=index)
                for index, taxon in enumerate(taxa, start=1)
            ),
        )

    @staticmethod
    def _admitted(
        finding_id: str,
        reviewer: Reviewer,
        candidate_set: CandidateSet,
    ) -> AdmittedRerank:
        return AdmittedRerank(
            finding=ReviewFinding(
                finding_id=finding_id,
                category=FindingCategory.OVERLOOKED_ALTERNATIVE,
                severity=Severity.MAJOR,
                status=FindingStatus.ACCEPTED,
                summary="Validated rerank.",
                subject_id=candidate_set.subject_id,
                required_action=RequiredAction.RERANK_CANDIDATES,
                impact=Impact.CANDIDATE_CHANGE,
            ),
            reviewer=reviewer,
            candidate_set=candidate_set,
        )

    def test_raw_recommendation_without_admitted_artifact_is_inert(self, simple_case):
        original = self._ranking("pinus", "picea")
        finding = ReviewFinding(
            finding_id="raw-rerank",
            category=FindingCategory.OVERLOOKED_ALTERNATIVE,
            severity=Severity.MAJOR,
            summary="Swap the candidates.",
            subject_id="tree_1",
            proposed_taxon="picea",
            required_action=RequiredAction.RERANK_CANDIDATES,
            impact=Impact.CANDIDATE_CHANGE,
        )
        raw_review = ReviewResult(
            reviewer=Reviewer.CONFUSION,
            status=ReviewStatus.PASS_WITH_FINDINGS,
            findings=(finding,),
            recommended_candidates=self._ranking("picea", "pinus").candidates,
            subject_id="tree_1",
        )
        state = GraphState(
            case=simple_case,
            reviews=(raw_review,),
            synthesis=ReviewSynthesis(accepted_findings=(finding,)),
        )

        assert apply_reranking(state, original) == original

    def test_one_admitted_internal_rerank_is_applied(self, simple_case):
        original = self._ranking("pinus", "picea")
        reranked = self._ranking("picea", "pinus")
        state = GraphState(
            case=simple_case,
            synthesis=ReviewSynthesis(
                admitted_reranks=(self._admitted("admitted", Reviewer.CONFUSION, reranked),)
            ),
        )

        assert apply_reranking(state, original) == reranked

    def test_one_arbiter_rerank_is_preferred_over_internal(self, simple_case):
        original = self._ranking("pinus", "picea", "larix")
        internal = self._ranking("larix", "pinus", "picea")
        arbiter = self._ranking("picea", "pinus", "larix")
        state = GraphState(
            case=simple_case,
            synthesis=ReviewSynthesis(
                admitted_reranks=(self._admitted("internal", Reviewer.CONFUSION, internal),)
            ),
            arbiter_synthesis=ReviewSynthesis(
                admitted_reranks=(self._admitted("arbiter", Reviewer.ARBITER, arbiter),)
            ),
        )

        assert apply_reranking(state, original) == arbiter

    def test_conflicting_reranks_at_same_level_preserve_current_ranking(self, simple_case):
        original = self._ranking("pinus", "picea")
        state = GraphState(
            case=simple_case,
            synthesis=ReviewSynthesis(
                admitted_reranks=(
                    self._admitted(
                        "one",
                        Reviewer.CONFUSION,
                        self._ranking("picea", "pinus"),
                    ),
                    self._admitted(
                        "two",
                        Reviewer.CONFIDENCE,
                        self._ranking("pinus", "picea"),
                    ),
                ),
                escalation_recommended=True,
            ),
        )

        assert apply_reranking(state, original) == original
