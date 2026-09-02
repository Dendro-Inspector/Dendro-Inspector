"""Resolution and identity are one monotonic, deterministic final claim."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from dendro_inspector.graph.state import GraphState
from dendro_inspector.nodes import abstain
from dendro_inspector.nodes.final_decision import (
    apply_reranking,
    cap_resolution,
    decide_subject,
    resolve_identity,
)
from dendro_inspector.nodes.response_composer import build_result
from dendro_inspector.schemas.candidates import Candidate, CandidateSet, SupportStrength
from dendro_inspector.schemas.decisions import DecisionStatus, FinalDecision
from dendro_inspector.schemas.evidence import (
    AttachmentStatus,
    EvidencePacket,
    Observation,
    ObservationSource,
    Subject,
    SubjectKind,
    WoodSurface,
)
from dendro_inspector.schemas.input import DeclaredObjectType
from dendro_inspector.schemas.reviews import (
    AdmittedRerank,
    FindingCategory,
    FindingOrigin,
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
from dendro_inspector.schemas.taxon import (
    Confidence,
    Provenance,
    Resolution,
    SourceType,
    TaxonCard,
    TaxonIdentity,
    resolution_rank,
)
from tests.conftest import _wood_surface

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
        wood_surface=_wood_surface(feature),
    )


def _state(
    simple_case,
    observations: tuple[Observation, ...],
    candidates: tuple[Candidate, ...],
    *,
    subject_kind: SubjectKind = SubjectKind.UNKNOWN,
    possible_multiple_taxa: bool = False,
):
    return GraphState(
        case=simple_case,
        evidence=EvidencePacket(
            subjects=(Subject(subject_id="tree_1", kind=subject_kind),),
            observations=observations,
            possible_multiple_taxa=possible_multiple_taxa,
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

    def test_empty_split_subject_uses_its_own_photo_request(self, simple_case, node_context):
        case = simple_case.model_copy(update={"declared_object_type": DeclaredObjectType.UNKNOWN})
        empty = CandidateSet(subject_id="firewood")
        state = GraphState(
            case=case,
            evidence=EvidencePacket(
                subjects=(
                    Subject(subject_id="firewood", kind=SubjectKind.SPLIT_WOOD),
                    Subject(subject_id="tree", kind=SubjectKind.STANDING_TREE),
                )
            ),
            candidate_sets=(empty,),
        )

        decision = decide_subject(state, node_context, empty)

        assert decision.best_next_photo is not None
        assert decision.best_next_photo.target == "prepared_end_grain_and_bark_circumference"
        assert decision.best_next_photo.subject_id == "firewood"

    def test_split_firewood_result_is_scoped_and_requests_matching_piece_views(
        self, simple_case, node_context
    ):
        case = simple_case.model_copy(
            update={"declared_object_type": DeclaredObjectType.SPLIT_FIREWOOD}
        )
        bark = _observation("bark", "bark.texture", "scaly_plates")
        resin = _observation("resin", "resin.presence", "present").model_copy(
            update={"wood_surface": WoodSurface.SPLIT_FACE}
        )
        candidate = Candidate(
            taxon="pinus",
            resolution=Resolution.GENUS,
            supporting_evidence_ids=("bark", "resin"),
            score=SupportStrength.MODERATE,
            rank=1,
        )
        candidates = CandidateSet(subject_id="tree_1", candidates=(candidate,))

        decision = decide_subject(
            _state(
                case,
                (bark, resin),
                (candidate,),
                subject_kind=SubjectKind.SPLIT_WOOD,
                possible_multiple_taxa=True,
            ),
            node_context,
            candidates,
        )

        assert decision.selected_taxon == "pinus"
        assert decision.best_next_photo is not None
        assert decision.best_next_photo.target == "prepared_end_grain_and_bark_circumference"
        assert decision.unresolved_questions
        assert "scoped to this subject" in decision.unresolved_questions[0]

    def test_split_subject_does_not_change_another_subjects_follow_up(
        self, simple_case, node_context
    ):
        case = simple_case.model_copy(update={"declared_object_type": DeclaredObjectType.UNKNOWN})
        bark = _observation("bark", "bark.texture", "scaly_plates")
        candidate = Candidate(
            taxon="pinus",
            resolution=Resolution.GENUS,
            supporting_evidence_ids=("bark",),
            score=SupportStrength.MODERATE,
            rank=1,
        )
        candidates = CandidateSet(subject_id="tree_1", candidates=(candidate,))
        state = GraphState(
            case=case,
            evidence=EvidencePacket(
                subjects=(
                    Subject(subject_id="firewood", kind=SubjectKind.SPLIT_WOOD),
                    Subject(subject_id="tree_1", kind=SubjectKind.STANDING_TREE),
                ),
                observations=(bark,),
            ),
            candidate_sets=(candidates,),
        )

        decision = decide_subject(state, node_context, candidates)

        assert decision.best_next_photo is not None
        assert decision.best_next_photo.target != "prepared_end_grain_and_bark_circumference"

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


class TestRecommendationIsAFloor:
    """A reviewer that names a level has said where its own findings stop.

    Every reviewer writing up one overclaim used to charge for it separately: three
    `lower_confidence` findings cost three steps, and a `lower_resolution` finding filed
    alongside `recommended_resolution: genus` landed on family — one step below the answer
    every reviewer asked for.
    """

    def _finding(
        self,
        finding_id: str,
        action: RequiredAction,
        *,
        origin: FindingOrigin = FindingOrigin.MODEL,
    ) -> ReviewFinding:
        return ReviewFinding(
            finding_id=finding_id,
            category=FindingCategory.RESOLUTION_TOO_SPECIFIC,
            severity=Severity.MAJOR,
            origin=origin,
            summary="The species candidate outruns the evidence; genus is defensible.",
            evidence_ids=("support",),
            subject_id="tree_1",
            required_action=action,
            impact=Impact.RESOLUTION_CHANGE,
        )

    def _decide(self, simple_case, node_context, synthesis: ReviewSynthesis):
        support = _observation("support", "leaf.shape", "palmate_lobed")
        leader = Candidate(
            taxon="pinus",
            resolution=Resolution.GENUS,
            supporting_evidence_ids=("support",),
            score=SupportStrength.STRONG,
            rank=1,
        )
        candidates = CandidateSet(subject_id="tree_1", candidates=(leader,))
        state = _state(simple_case, (support,), candidates.candidates).model_copy(
            update={"synthesis": synthesis}
        )
        return decide_subject(state, node_context, candidates)

    def test_model_lower_resolution_stops_at_the_recommended_level(self, simple_case, node_context):
        decision = self._decide(
            simple_case,
            node_context,
            ReviewSynthesis(
                accepted_findings=(self._finding("model-one", RequiredAction.LOWER_RESOLUTION),),
                resolution_delta=Resolution.GENUS,
            ),
        )

        assert decision.resolution is Resolution.GENUS
        assert decision.selected_taxon == "pinus"

    def test_deterministic_lower_resolution_bites_past_the_recommendation(
        self, simple_case, node_context
    ):
        decision = self._decide(
            simple_case,
            node_context,
            ReviewSynthesis(
                accepted_findings=(
                    self._finding(
                        "auto-one",
                        RequiredAction.LOWER_RESOLUTION,
                        origin=FindingOrigin.DETERMINISTIC,
                    ),
                ),
                resolution_delta=Resolution.GENUS,
            ),
        )

        assert decision.resolution is Resolution.FAMILY

    def test_lower_resolution_without_a_recommendation_still_broadens(
        self, simple_case, node_context
    ):
        decision = self._decide(
            simple_case,
            node_context,
            ReviewSynthesis(
                accepted_findings=(self._finding("model-one", RequiredAction.LOWER_RESOLUTION),),
            ),
        )

        assert decision.resolution is Resolution.FAMILY

    def test_repeated_model_downgrades_do_not_sink_below_the_recommendation(
        self, simple_case, node_context
    ):
        decision = self._decide(
            simple_case,
            node_context,
            ReviewSynthesis(
                accepted_findings=tuple(
                    self._finding(f"model-{index}", RequiredAction.LOWER_CONFIDENCE)
                    for index in range(3)
                ),
                confidence_delta=Confidence.MEDIUM,
            ),
        )

        assert decision.confidence is Confidence.MEDIUM

    def test_deterministic_downgrade_bites_past_the_recommendation(self, simple_case, node_context):
        decision = self._decide(
            simple_case,
            node_context,
            ReviewSynthesis(
                accepted_findings=(
                    self._finding("model-one", RequiredAction.LOWER_CONFIDENCE),
                    self._finding(
                        "auto-one",
                        RequiredAction.LOWER_CONFIDENCE,
                        origin=FindingOrigin.DETERMINISTIC,
                    ),
                ),
                confidence_delta=Confidence.MEDIUM,
            ),
        )

        assert decision.confidence is Confidence.LOW

    def test_downgrades_without_a_recommendation_still_compose(self, simple_case, node_context):
        decision = self._decide(
            simple_case,
            node_context,
            ReviewSynthesis(
                accepted_findings=tuple(
                    self._finding(f"model-{index}", RequiredAction.LOWER_CONFIDENCE)
                    for index in range(2)
                ),
            ),
        )

        assert decision.confidence is Confidence.LOW


class TestNearestAlternativeSearch:
    def test_alternative_is_found_past_a_candidate_that_collapsed_into_the_verdict(
        self, simple_case, node_context
    ):
        """Two species of one genus must not hide the alternative behind them."""
        observation = _observation("support", "trunk.form", "straight_long")
        candidates = CandidateSet(
            subject_id="tree_1",
            candidates=(
                Candidate(
                    taxon="pinus",
                    resolution=Resolution.GENUS,
                    supporting_evidence_ids=("support",),
                    score=SupportStrength.MODERATE,
                    rank=1,
                ),
                Candidate(
                    taxon="picea",
                    resolution=Resolution.GENUS,
                    score=SupportStrength.WEAK,
                    rank=2,
                ),
                Candidate(
                    taxon="quercus",
                    resolution=Resolution.GENUS,
                    score=SupportStrength.WEAK,
                    rank=3,
                ),
            ),
        )

        decision = decide_subject(
            _state(simple_case, (observation,), candidates.candidates),
            node_context,
            candidates,
        )

        assert decision.selected_taxon == "pinaceae"
        assert decision.nearest_alternative == "fagaceae"


class TestSupportingEvidenceIsReportedInFull:
    """A verdict must not display less evidence than an abstention.

    `_supporting_evidence` fell back to every visible observation when no candidate supplied
    a summary, and to a single line when one did — so the identified subject printed one
    bullet and the insufficient-evidence subject beside it printed five.
    """

    def _decide(self, simple_case, node_context):
        shape = _observation("shape", "leaf.shape", "palmate_lobed")
        arrangement = _observation("arrangement", "leaf.arrangement", "opposite")
        leader = Candidate(
            taxon="acer",
            resolution=Resolution.GENUS,
            supporting_evidence_ids=("shape", "arrangement"),
            score=SupportStrength.STRONG,
            rank=1,
        )
        candidates = CandidateSet(subject_id="tree_1", candidates=(leader,))
        state = _state(simple_case, (shape, arrangement), candidates.candidates)
        return decide_subject(state, node_context, candidates), state

    def test_every_validated_support_is_carried(self, simple_case, node_context):
        decision, _ = self._decide(simple_case, node_context)

        assert decision.selected_taxon == "acer"
        assert len(decision.supporting_evidence) == 2
        assert any("leaf.shape = palmate_lobed" in item for item in decision.supporting_evidence)
        assert any("leaf.arrangement = opposite" in item for item in decision.supporting_evidence)

    def test_rendered_result_lists_them_all(self, simple_case, node_context):
        decision, state = self._decide(simple_case, node_context)

        result = build_result(decision, "en", state)

        assert len(result.supporting_evidence) == 2

    def test_support_order_follows_the_candidate_citation_order(self, simple_case, node_context):
        decision, _ = self._decide(simple_case, node_context)

        assert decision.supporting_evidence[0].startswith("leaf.shape")


class TestPhaseZeroHardeningGates:
    """Failing gates for the findings in `docs/specs/core-logic-hardening.md`.

    Each is strict, so the marker has to be removed in the same commit that fixes the
    finding. Until then these record, executably, what the evidence says is wrong.
    """

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "F1: `_SCORE_TO_CONFIDENCE[leader.score]` seeds confidence from the primary "
            "model's own label, so the same evidence returns low, medium or high."
        ),
    )
    def test_the_same_evidence_yields_the_same_confidence_whatever_the_model_said(
        self, simple_case, node_context
    ):
        support = _observation("support", "needles.fascicles", "two")
        confidences = set()
        for score in SupportStrength:
            candidates = CandidateSet(
                subject_id="tree_1",
                candidates=(
                    Candidate(
                        taxon="pinus",
                        resolution=Resolution.GENUS,
                        supporting_evidence_ids=("support",),
                        score=score,
                        rank=1,
                    ),
                ),
            )
            state = _state(simple_case, (support,), candidates.candidates)
            confidences.add(decide_subject(state, node_context, candidates).confidence)

        assert len(confidences) == 1

    def test_conflicting_evidence_status_needs_a_disqualifying_hit(self, simple_case, node_context):
        """Foliage that could not be traced to this trunk cannot convict the answer.

        The Picea card declares `needles.fascicles` disqualifying. Here that observation
        carries `attachment: unknown`, so the evidence hierarchy projects it to context and
        it could not have supported any candidate.
        """
        support = _observation("support", "needles.attachment", "single_on_woody_peg")
        loose = Observation(
            observation_id="loose",
            feature="needles.fascicles",
            value="two",
            subject_id="tree_1",
            source=ObservationSource.IMAGE,
            image_id="img-1",
            attachment=AttachmentStatus.UNKNOWN,
        )
        candidates = CandidateSet(
            subject_id="tree_1",
            candidates=(
                Candidate(
                    taxon="picea",
                    resolution=Resolution.GENUS,
                    supporting_evidence_ids=("support",),
                    score=SupportStrength.MODERATE,
                    rank=1,
                ),
            ),
        )
        state = _state(simple_case, (support, loose), candidates.candidates)

        decision = decide_subject(state, node_context, candidates)

        assert decision.status is not DecisionStatus.CONFLICTING_EVIDENCE

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "F6: `FinalDecision` has no `abstained` field, and the abstention step is "
            "computed from the proposed resolution the card cap has already broadened."
        ),
    )
    def test_an_abstained_verdict_says_so_and_is_broader(self, simple_case, node_context):
        """A species proposal capped to genus must not abstain to the same genus.

        The card supports genus only, so the composed bound is already genus before
        abstention. Lowering one step from the *proposed* species lands back on genus, and
        the returned verdict is then indistinguishable from the confident one.
        """
        support = _observation("support", "needles.fascicles", "two")
        candidates = CandidateSet(
            subject_id="tree_1",
            candidates=(
                Candidate(
                    taxon="pinus",
                    resolution=Resolution.SPECIES,
                    supporting_evidence_ids=("support",),
                    score=SupportStrength.MODERATE,
                    rank=1,
                ),
            ),
        )
        state = _state(simple_case, (support,), candidates.candidates)
        abstained = asyncio.run(abstain.run(state, node_context))

        decision = decide_subject(abstained, node_context, candidates)

        # Reached through `getattr` so the gate type-checks before the field exists.
        assert getattr(decision, "abstained", False)
        assert decision.resolution is Resolution.FAMILY
