"""Open-world candidates: retained beside admission, bounded by evidence, never selected.

Issue #10. A proposed taxon with no local card used to vanish at admission, so a frame
showing real diagnostic features of an uncovered taxon read as "the photograph showed
nothing". These tests pin what replaced that, and the lines it must not cross.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from dendro_inspector.config import GraphConfig
from dendro_inspector.graph.definition import NodeName
from dendro_inspector.graph.routing import route_candidate_generator
from dendro_inspector.graph.state import GraphState
from dendro_inspector.knowledge.candidate_validation import validate_candidate_set_with_report
from dendro_inspector.knowledge.loader import KnowledgeBase
from dendro_inspector.knowledge.taxon_cards import card_value_vocabulary
from dendro_inspector.nodes.candidate_generator import OPEN_WORLD_SECTION, open_world_context
from dendro_inspector.nodes.evidence_quality import assess
from dendro_inspector.nodes.final_decision import decide_subject_base
from dendro_inspector.schemas.candidates import (
    Candidate,
    CandidateSet,
    OpenWorldCandidate,
    SupportStrength,
)
from dendro_inspector.schemas.decisions import DecisionStatus
from dendro_inspector.schemas.evidence import (
    EvidencePacket,
    Observation,
    ObservationSource,
    Reliability,
    Subject,
    SubjectKind,
    Visibility,
)
from dendro_inspector.schemas.input import CaseInput, DeclaredObjectType, ImageRef
from dendro_inspector.schemas.reference import ReferenceCitation, ReferenceStatus, SourceTrust
from dendro_inspector.schemas.taxon import Confidence, Resolution
from tests.conftest import _attachment

SUBJECT = "tree_1"


def _obs(
    observation_id: str,
    feature: str,
    value: str,
    *,
    subject_id: str = SUBJECT,
    attached: bool = True,
    reliability: Reliability = Reliability.HIGH,
) -> Observation:
    return Observation(
        observation_id=observation_id,
        feature=feature,
        value=value,
        subject_id=subject_id,
        source=ObservationSource.IMAGE,
        image_id="img-1",
        visibility=Visibility.CLEAR,
        reliability=reliability,
        attachment=_attachment(feature, attached),
    )


def _packet(*observations: Observation, subjects: tuple[str, ...] = (SUBJECT,)) -> EvidencePacket:
    return EvidencePacket(
        subjects=tuple(
            Subject(subject_id=subject_id, kind=SubjectKind.STANDING_TREE)
            for subject_id in subjects
        ),
        observations=observations,
    )


def _candidate(
    taxon: str,
    rank: int,
    *support: str,
    resolution: Resolution = Resolution.SPECIES,
    score: SupportStrength = SupportStrength.MODERATE,
) -> Candidate:
    return Candidate(
        taxon=taxon,
        resolution=resolution,
        supporting_evidence_ids=support,
        score=score,
        rank=rank,
    )


def _open_world(
    proposal: CandidateSet, evidence: EvidencePacket, knowledge: KnowledgeBase
) -> tuple[OpenWorldCandidate, ...]:
    return validate_candidate_set_with_report(proposal, evidence, knowledge).open_world


FOLIAGE = _obs("obs-leaf", "leaf.type", "compound_palmate")
FRUIT = _obs("obs-fruit", "fruit.husk", "spiny_green")


class TestRetention:
    def test_a_no_card_taxon_with_trusted_support_is_retained_not_admitted(self, knowledge):
        evidence = _packet(FOLIAGE, FRUIT)
        proposal = CandidateSet(
            subject_id=SUBJECT,
            candidates=(_candidate("aesculus_hippocastanum", 1, "obs-leaf", "obs-fruit"),),
        )

        result = validate_candidate_set_with_report(proposal, evidence, knowledge)

        assert result.candidate_set.candidates == ()
        assert result.rejected_taxa == ("aesculus_hippocastanum",)
        (retained,) = result.open_world
        assert retained.taxon == "aesculus_hippocastanum"
        assert retained.supporting_evidence_ids == ("obs-leaf", "obs-fruit")
        assert retained.proposed_rank == 1

    def test_admission_itself_is_unchanged_by_retention(self, knowledge):
        """The admitted world is computed exactly as before; retention only reads it."""
        evidence = _packet(_obs("obs-fruit", "fruit.type", "small_round_drupe"))
        proposal = CandidateSet(
            subject_id=SUBJECT,
            candidates=(
                _candidate("prunus_domestica", 1, "obs-fruit"),
                _candidate("prunus_cerasifera", 2, "obs-fruit"),
            ),
        )

        result = validate_candidate_set_with_report(proposal, evidence, knowledge)

        assert [c.taxon for c in result.candidate_set.candidates] == ["prunus_cerasifera"]
        assert result.candidate_set.candidates[0].rank == 1
        assert [c.taxon for c in result.open_world] == ["prunus_domestica"]

    def test_colour_alone_retains_nothing(self, knowledge):
        evidence = _packet(_obs("obs-colour", "bark.colour", "dark_grey"))
        proposal = CandidateSet(
            subject_id=SUBJECT,
            candidates=(_candidate("aesculus_hippocastanum", 1, "obs-colour"),),
        )
        assert _open_world(proposal, evidence, knowledge) == ()

    def test_another_subjects_evidence_retains_nothing(self, knowledge):
        evidence = _packet(
            _obs("obs-leaf", "leaf.type", "compound_palmate", subject_id="tree_2"),
            subjects=(SUBJECT, "tree_2"),
        )
        proposal = CandidateSet(
            subject_id=SUBJECT,
            candidates=(_candidate("aesculus_hippocastanum", 1, "obs-leaf"),),
        )
        assert _open_world(proposal, evidence, knowledge) == ()

    def test_an_unattached_fallen_fruit_retains_nothing(self, knowledge):
        """No card is not a reason to trust detached evidence the cards would not trust."""
        evidence = _packet(_obs("obs-fruit", "fruit.husk", "spiny_green", attached=False))
        proposal = CandidateSet(
            subject_id=SUBJECT,
            candidates=(_candidate("aesculus_hippocastanum", 1, "obs-fruit"),),
        )
        assert _open_world(proposal, evidence, knowledge) == ()

    def test_an_unreadable_value_retains_nothing(self, knowledge):
        evidence = _packet(_obs("obs-leaf", "leaf.type", "not_resolvable"))
        proposal = CandidateSet(
            subject_id=SUBJECT,
            candidates=(_candidate("aesculus_hippocastanum", 1, "obs-leaf"),),
        )
        assert _open_world(proposal, evidence, knowledge) == ()

    @pytest.mark.parametrize(
        "taxon",
        [
            "juglans",  # a broader identity the walnut card declares, with no card of its own
            "walnut",  # an alias the walnut card declares
        ],
    )
    def test_a_name_a_card_declares_is_retained_as_covered_not_dropped(self, knowledge, taxon):
        """No black hole between admission (card ids only) and missing taxonomy."""
        evidence = _packet(FOLIAGE)
        proposal = CandidateSet(subject_id=SUBJECT, candidates=(_candidate(taxon, 1, "obs-leaf"),))

        result = validate_candidate_set_with_report(proposal, evidence, knowledge)

        assert result.candidate_set.candidates == ()  # admission is not widened
        (retained,) = result.open_world
        assert retained.taxon == taxon
        assert retained.covered_by == ("juglans_regia",)
        assert not retained.uncovered
        assert retained.reference_status is ReferenceStatus.LOCAL_UNREVIEWED

    @pytest.mark.parametrize("taxon", ["tree_1", "oak-like", "x"])
    def test_ids_not_shaped_like_a_scientific_name_are_discarded(self, knowledge, taxon):
        evidence = _packet(FOLIAGE)
        proposal = CandidateSet(subject_id=SUBJECT, candidates=(_candidate(taxon, 1, "obs-leaf"),))
        assert _open_world(proposal, evidence, knowledge) == ()

    def test_a_proposal_at_unknown_resolution_is_not_a_hypothesis(self, knowledge):
        evidence = _packet(FOLIAGE)
        proposal = CandidateSet(
            subject_id=SUBJECT,
            candidates=(
                _candidate("aesculus_hippocastanum", 1, "obs-leaf", resolution=Resolution.UNKNOWN),
            ),
        )
        assert _open_world(proposal, evidence, knowledge) == ()


class TestBoundedByEvidence:
    def test_bark_only_support_caps_resolution_and_confidence(self, knowledge):
        evidence = _packet(_obs("obs-bark", "bark.texture", "deep_diamond_ridges"))
        proposal = CandidateSet(
            subject_id=SUBJECT,
            candidates=(
                _candidate(
                    "juglans_nigra",
                    1,
                    "obs-bark",
                    resolution=Resolution.SPECIES,
                    score=SupportStrength.STRONG,
                ),
            ),
        )

        (retained,) = _open_world(proposal, evidence, knowledge)

        assert retained.proposed_resolution is Resolution.SPECIES
        assert retained.resolution is Resolution.GENUS
        assert retained.image_confidence_ceiling is Confidence.LOW
        assert retained.score is SupportStrength.MODERATE

    def test_no_card_means_never_high_confidence_even_on_fruit(self, knowledge):
        """Fruit can carry high confidence for a card whose requirement is met. Here there is
        no card, so no requirement can be met."""
        evidence = _packet(FOLIAGE, FRUIT)
        proposal = CandidateSet(
            subject_id=SUBJECT,
            candidates=(
                _candidate(
                    "aesculus_hippocastanum",
                    1,
                    "obs-leaf",
                    "obs-fruit",
                    score=SupportStrength.STRONG,
                ),
            ),
        )

        (retained,) = _open_world(proposal, evidence, knowledge)

        assert retained.image_confidence_ceiling is Confidence.MEDIUM
        assert retained.score is SupportStrength.MODERATE

    def test_a_weak_proposal_stays_low(self, knowledge):
        evidence = _packet(FOLIAGE, FRUIT)
        proposal = CandidateSet(
            subject_id=SUBJECT,
            candidates=(
                _candidate("aesculus_hippocastanum", 1, "obs-fruit", score=SupportStrength.WEAK),
            ),
        )
        (retained,) = _open_world(proposal, evidence, knowledge)
        assert retained.image_confidence_ceiling is Confidence.LOW

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("image_confidence_ceiling", Confidence.HIGH),
            ("score", SupportStrength.STRONG),
            ("reference_status", ReferenceStatus.LOCAL_REVIEWED),
            ("reference_status", ReferenceStatus.LOCAL_UNREVIEWED),
            ("reference_status", ReferenceStatus.EXTERNALLY_VERIFIED),
            ("resolution", Resolution.UNKNOWN),
        ],
    )
    def test_the_contract_refuses_claims_only_a_card_or_a_citation_can_earn(self, field, value):
        base = {
            "subject_id": SUBJECT,
            "taxon": "aesculus_hippocastanum",
            "proposed_rank": 1,
            "proposed_resolution": Resolution.SPECIES,
            "resolution": Resolution.SPECIES,
            "proposed_score": SupportStrength.MODERATE,
            "score": SupportStrength.MODERATE,
            "supporting_evidence_ids": ("obs-leaf",),
            "evidence_tier": 6,
            "image_confidence_ceiling": Confidence.MEDIUM,
        }
        OpenWorldCandidate.model_validate(base)
        with pytest.raises(ValidationError):
            OpenWorldCandidate.model_validate({**base, field: value})


def _verified(candidate: OpenWorldCandidate) -> OpenWorldCandidate:
    """As an authoritative source this system queried itself would leave it. Synthetic."""
    citation = ReferenceCitation(
        source_id="example_flora",
        source_trust=SourceTrust.AUTHORITATIVE_RETRIEVAL,
        outcome=ReferenceStatus.EXTERNALLY_VERIFIED,
        reason="documented_feature_match",
        source_url=f"https://references.example.org/taxon/{candidate.taxon}",
        retrieved_at=datetime(2026, 1, 15, 10, tzinfo=UTC),
        matched_features=("fruit.type",),
    )
    return OpenWorldCandidate.model_validate(
        {
            **candidate.model_dump(),
            "reference_status": ReferenceStatus.EXTERNALLY_VERIFIED,
            "references": (citation,),
        }
    )


def _state(
    evidence: EvidencePacket,
    proposal: CandidateSet,
    knowledge: KnowledgeBase,
    *,
    verified: tuple[str, ...] = (),
) -> tuple[GraphState, CandidateSet]:
    result = validate_candidate_set_with_report(proposal, evidence, knowledge)
    vocabulary = card_value_vocabulary(knowledge.taxa(knowledge.available_taxon_ids()))
    quality = assess(
        evidence,
        min_observations=1,
        require_non_colour=True,
        vocabulary=vocabulary,
    )
    state = GraphState(
        case=CaseInput(
            case_id="open-world",
            images=(ImageRef(image_id="img-1", path=Path("examples/log.jpg")),),
            declared_object_type=DeclaredObjectType.STANDING_TREE,
        ),
        evidence=evidence,
        quality=quality,
        proposed_candidate_sets=(proposal,),
        candidate_sets=(result.candidate_set,),
        open_world_candidates=tuple(
            _verified(candidate) if candidate.taxon in verified else candidate
            for candidate in result.open_world
        ),
    )
    return state, result.candidate_set


@pytest.fixture
def open_world_context_(node_context):
    """The decision rules below are open-world rules; closed-world, none of them runs."""
    graph = node_context.config.graph.model_copy(update={"open_world_discovery": True})
    return dataclasses.replace(
        node_context, config=node_context.config.model_copy(update={"graph": graph})
    )


class TestDecisionRules:
    def test_only_open_world_on_uncovered_evidence_is_a_coverage_gap(self, open_world_context_):
        evidence = _packet(FOLIAGE, FRUIT)
        proposal = CandidateSet(
            subject_id=SUBJECT,
            candidates=(_candidate("aesculus_hippocastanum", 1, "obs-leaf", "obs-fruit"),),
        )
        state, admitted = _state(evidence, proposal, open_world_context_.knowledge)

        decision = decide_subject_base(state, open_world_context_, admitted, record=False)

        assert decision.status is DecisionStatus.KNOWLEDGE_COVERAGE_GAP
        assert decision.selected_taxon is None
        assert decision.resolution is Resolution.UNKNOWN
        assert [c.taxon for c in decision.open_world_candidates] == ["aesculus_hippocastanum"]
        text = " | ".join(decision.unresolved_questions)
        assert "Aesculus hippocastanum" in text
        assert "fruit.husk" in text  # the reader learns what the cards lacked

    def test_an_off_card_name_on_representable_evidence_does_not_move_the_blame(
        self, open_world_context_
    ):
        """Naming an off-card taxon is not evidence the knowledge base is the limit.

        Pinus lists both readings, so nothing here is outside the cards; the frame simply
        did not support the card it could open. The hypothesis is still reported.
        """
        evidence = _packet(
            _obs("obs-bark", "bark.texture", "scaly_plates", reliability=Reliability.MEDIUM),
        )
        proposal = CandidateSet(
            subject_id=SUBJECT,
            candidates=(_candidate("eucalyptus", 1, "obs-bark", resolution=Resolution.GENUS),),
        )
        state, admitted = _state(evidence, proposal, open_world_context_.knowledge)

        decision = decide_subject_base(state, open_world_context_, admitted, record=False)

        assert decision.status is DecisionStatus.INSUFFICIENT_EVIDENCE
        assert [c.taxon for c in decision.open_world_candidates] == ["eucalyptus"]

    def test_a_verified_congener_on_the_same_evidence_caps_the_look_alike_at_genus(
        self, open_world_context_
    ):
        """No silent substitution: the model led with a real species this pack lacks."""
        fruit = _obs("obs-fruit", "fruit.type", "small_round_drupe")
        evidence = _packet(fruit)
        baseline_proposal = CandidateSet(
            subject_id=SUBJECT, candidates=(_candidate("prunus_cerasifera", 1, "obs-fruit"),)
        )
        outranked_proposal = CandidateSet(
            subject_id=SUBJECT,
            candidates=(
                _candidate("prunus_domestica", 1, "obs-fruit"),
                _candidate("prunus_cerasifera", 2, "obs-fruit"),
            ),
        )

        baseline_state, baseline_set = _state(
            evidence, baseline_proposal, open_world_context_.knowledge
        )
        state, admitted = _state(
            evidence,
            outranked_proposal,
            open_world_context_.knowledge,
            verified=("prunus_domestica",),
        )
        baseline = decide_subject_base(
            baseline_state, open_world_context_, baseline_set, record=False
        )
        decision = decide_subject_base(state, open_world_context_, admitted, record=False)

        assert baseline.selected_taxon == "prunus_cerasifera"
        assert baseline.resolution is Resolution.SPECIES
        assert decision.selected_taxon == "prunus"
        assert decision.resolution is Resolution.GENUS
        assert decision.open_world_conflict is False
        assert [c.taxon for c in decision.open_world_candidates] == ["prunus_domestica"]
        assert "Prunus domestica" in " | ".join(decision.unresolved_questions)

    def test_an_unverified_congener_flags_a_conflict_but_never_moves_the_verdict(
        self, open_world_context_
    ):
        """A shared observation proves both cited it, not that both fit it.

        A model can invent ``prunus_fictitia``, rank it first and cite the leader's own fruit.
        Nothing has checked that the name exists or that its morphology fits, so it may
        raise the conflict flag but not take the species away.
        """
        fruit = _obs("obs-fruit", "fruit.type", "small_round_drupe")
        proposal = CandidateSet(
            subject_id=SUBJECT,
            candidates=(
                _candidate("prunus_fictitia", 1, "obs-fruit"),
                _candidate("prunus_cerasifera", 2, "obs-fruit"),
            ),
        )
        state, admitted = _state(_packet(fruit), proposal, open_world_context_.knowledge)

        decision = decide_subject_base(state, open_world_context_, admitted, record=False)

        assert decision.selected_taxon == "prunus_cerasifera"
        assert decision.resolution is Resolution.SPECIES
        assert decision.open_world_conflict is True

    def test_a_lower_ranked_uncovered_alternative_does_not_cap(self, open_world_context_):
        fruit = _obs("obs-fruit", "fruit.type", "small_round_drupe")
        proposal = CandidateSet(
            subject_id=SUBJECT,
            candidates=(
                _candidate("prunus_cerasifera", 1, "obs-fruit"),
                _candidate("prunus_domestica", 2, "obs-fruit"),
            ),
        )
        state, admitted = _state(_packet(fruit), proposal, open_world_context_.knowledge)

        decision = decide_subject_base(state, open_world_context_, admitted, record=False)

        assert decision.selected_taxon == "prunus_cerasifera"
        assert decision.resolution is Resolution.SPECIES
        assert [c.taxon for c in decision.open_world_candidates] == ["prunus_domestica"]

    def test_a_selected_taxon_reports_its_cards_reference_status(self, open_world_context_):
        fruit = _obs("obs-fruit", "fruit.type", "small_round_drupe")
        proposal = CandidateSet(
            subject_id=SUBJECT, candidates=(_candidate("prunus_cerasifera", 1, "obs-fruit"),)
        )
        state, admitted = _state(_packet(fruit), proposal, open_world_context_.knowledge)

        decision = decide_subject_base(state, open_world_context_, admitted, record=False)

        # Every shipped card is unreviewed demonstration content, and the verdict says so
        # without that changing its confidence.
        assert decision.reference_status is ReferenceStatus.LOCAL_UNREVIEWED

    def test_a_covered_name_is_reported_but_is_not_missing_taxonomy(self, open_world_context_):
        evidence = _packet(_obs("obs-nut", "nut.presence", "present"))
        proposal = CandidateSet(
            subject_id=SUBJECT,
            candidates=(_candidate("juglans", 1, "obs-nut", resolution=Resolution.GENUS),),
        )
        state, admitted = _state(evidence, proposal, open_world_context_.knowledge)

        decision = decide_subject_base(state, open_world_context_, admitted, record=False)

        assert decision.status is DecisionStatus.INSUFFICIENT_EVIDENCE
        (candidate,) = decision.open_world_candidates
        assert candidate.covered_by == ("juglans_regia",)
        assert "declares only within juglans_regia" in " | ".join(decision.unresolved_questions)

    def test_a_congener_that_shares_no_decisive_evidence_does_not_cap(self, open_world_context_):
        """Rank alone, or rank on weaker evidence, never moves a validated verdict."""
        evidence = _packet(
            _obs("obs-fruit", "fruit.type", "small_round_drupe"),
            _obs("obs-bark", "bark.texture", "grey_brown_cracked"),
        )
        proposal = CandidateSet(
            subject_id=SUBJECT,
            candidates=(
                _candidate("prunus_domestica", 1, "obs-bark"),
                _candidate("prunus_cerasifera", 2, "obs-fruit", "obs-bark"),
            ),
        )
        state, admitted = _state(
            evidence, proposal, open_world_context_.knowledge, verified=("prunus_domestica",)
        )

        decision = decide_subject_base(state, open_world_context_, admitted, record=False)

        assert decision.selected_taxon == "prunus_cerasifera"
        assert decision.resolution is Resolution.SPECIES
        assert decision.open_world_conflict is True
        assert "Competing hypothesis" in " | ".join(decision.unresolved_questions)

    def test_another_genus_ranked_first_competes_but_never_moves_the_verdict(
        self, open_world_context_
    ):
        """Abstaining here would give the model's ranking the authority it is denied."""
        fruit = _obs("obs-fruit", "fruit.type", "small_round_drupe")
        proposal = CandidateSet(
            subject_id=SUBJECT,
            candidates=(
                _candidate("zelkova_carpinifolia", 1, "obs-fruit"),
                _candidate("prunus_cerasifera", 2, "obs-fruit"),
            ),
        )
        state, admitted = _state(
            _packet(fruit),
            proposal,
            open_world_context_.knowledge,
            verified=("zelkova_carpinifolia",),
        )

        decision = decide_subject_base(state, open_world_context_, admitted, record=False)

        assert decision.selected_taxon == "prunus_cerasifera"
        assert decision.resolution is Resolution.SPECIES
        assert decision.open_world_conflict is True
        text = " | ".join(decision.unresolved_questions)
        assert "Competing hypothesis: the model ranked Zelkova carpinifolia" in text

    def test_closed_world_keeps_the_old_status_for_a_coverage_gap_subject(self, node_context):
        """The status rule is open-world only: the flag is the single switch."""
        evidence = _packet(FOLIAGE, FRUIT)
        proposal = CandidateSet(
            subject_id=SUBJECT,
            candidates=(_candidate("aesculus_hippocastanum", 1, "obs-leaf", "obs-fruit"),),
        )
        state, admitted = _state(evidence, proposal, node_context.knowledge)

        decision = decide_subject_base(state, node_context, admitted, record=False)

        assert decision.status is DecisionStatus.INSUFFICIENT_EVIDENCE


class TestOpenWorldRouting:
    def _state(self, *, admitted: bool) -> GraphState:
        candidates = (
            (Candidate(taxon="pinus", resolution=Resolution.GENUS, rank=1),) if admitted else ()
        )
        return GraphState(
            case=CaseInput(case_id="routing", user_text="?"),
            candidate_sets=(CandidateSet(subject_id=SUBJECT, candidates=candidates),),
        )

    def test_closed_world_topology_is_untouched(self):
        for admitted in (True, False):
            assert (
                route_candidate_generator(self._state(admitted=admitted), GraphConfig())
                is NodeName.ATTACHMENT_AUTHORITY_GATE
            )

    def test_open_world_with_nothing_admitted_skips_review(self):
        config = GraphConfig(open_world_discovery=True)
        assert (
            route_candidate_generator(self._state(admitted=False), config)
            is NodeName.FINAL_DECISION
        )

    def test_open_world_with_something_admitted_is_reviewed_as_usual(self):
        config = GraphConfig(open_world_discovery=True)
        assert (
            route_candidate_generator(self._state(admitted=True), config)
            is NodeName.ATTACHMENT_AUTHORITY_GATE
        )


class TestCoverageGateInOpenWorld:
    def _assess(self, evidence: EvidencePacket, knowledge: KnowledgeBase, *, open_world: bool):
        vocabulary = card_value_vocabulary(knowledge.taxa(knowledge.available_taxon_ids()))
        return assess(
            evidence,
            min_observations=2,
            require_non_colour=True,
            vocabulary=vocabulary,
            cards_available=lambda _subject: False,
            open_world=open_world,
        )

    def test_the_gap_is_still_measured_but_no_longer_ends_the_run(self, knowledge):
        evidence = _packet(FOLIAGE, FRUIT)

        closed = self._assess(evidence, knowledge, open_world=False)
        opened = self._assess(evidence, knowledge, open_world=True)

        assert not closed.sufficient
        assert opened.sufficient
        assert opened.usable_subject_ids == (SUBJECT,)
        for report in (closed, opened):
            assert report.coverage_gap_subject_ids == (SUBJECT,)
            assert "knowledge_coverage_gap" in report.insufficient_reasons

    def test_missing_knowledge_does_not_rescue_a_weak_frame(self, knowledge):
        """Every other reason still holds a subject back."""
        evidence = _packet(FOLIAGE)  # one observation, below the minimum of two

        report = self._assess(evidence, knowledge, open_world=True)

        assert not report.sufficient
        assert "too_few_resolvable_observations" in report.insufficient_reasons


class TestGeneratorContext:
    """The model is told what to do when no card is in play, and only in open-world mode."""

    def test_closed_world_carries_no_open_world_section(self, node_context):
        evidence = _packet(FOLIAGE, FRUIT)
        assert open_world_context(evidence, node_context, (SUBJECT,)) == ""

    def test_open_world_names_the_subjects_with_nothing_to_rank(self, open_world_context_):
        evidence = _packet(
            FOLIAGE,
            FRUIT,
            _obs("obs-cone", "cones.shape", "not_resolvable", subject_id="tree_2"),
            _obs("obs-fruit-2", "fruit.type", "small_round_drupe", subject_id="tree_2"),
            subjects=(SUBJECT, "tree_2"),
        )

        section = open_world_context(evidence, open_world_context_, (SUBJECT, "tree_2"))

        assert section.startswith(f"## {OPEN_WORLD_SECTION}")
        payload = json.loads(section.split("```json\n")[1].split("\n```")[0])
        assert payload == {"enabled": True, "subjects_without_cards": [SUBJECT]}

    def test_the_prompt_rule_is_keyed_on_the_section_the_code_emits(self, repo_root):
        prompt = (repo_root / "prompts" / "nodes" / "candidate-generator.md").read_text(
            encoding="utf-8"
        )
        assert f"`{OPEN_WORLD_SECTION}` section" in prompt
        assert "`subjects_without_cards`" in prompt
