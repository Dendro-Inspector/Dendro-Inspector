"""Compatible evidence versus discriminating evidence.

Properties, not answers: these tests never assert that a photograph is a pine. They assert
what a kind of evidence may and may not do — that plated bark, which pine shares with
spruce, fir and sycamore, cannot quietly carry a genus; that three readings of one trunk are
one look at one organ; and that a conifer's separating characters do separate it.

Botanical sources for the conifer rules are recorded on the cards themselves (Gymnosperm
Database pages, accessed 2026-11-11). None of this is fitted to a stored run.
"""

from __future__ import annotations

import pytest

from dendro_inspector.graph.state import GraphState
from dendro_inspector.knowledge.candidate_validation import derive_support_strength
from dendro_inspector.knowledge.coverage_audit import CoverageStatus, audit_observation
from dendro_inspector.knowledge.taxon_cards import match_card, self_contradiction_hits
from dendro_inspector.nodes.final_decision import (
    NO_DISCRIMINATING_SUPPORT_PHRASE,
    decide_subject,
    discriminating_support,
)
from dendro_inspector.schemas.candidates import Candidate, CandidateSet, SupportStrength
from dendro_inspector.schemas.decisions import DecisionStatus
from dendro_inspector.schemas.evidence import (
    AttachmentStatus,
    EvidencePacket,
    Observation,
    ObservationSource,
    Reliability,
    Subject,
    SubjectKind,
    Visibility,
)
from dendro_inspector.schemas.taxon import Resolution, resolution_rank
from tests.conftest import with_legacy_policy

SUBJECT = "tree_1"


def _obs(
    observation_id: str,
    feature: str,
    value: str,
    *,
    image: str = "img-1",
    reliability: Reliability = Reliability.HIGH,
) -> Observation:
    detachable = feature.split(".", 1)[0] in {"leaf", "needles", "cones", "samara"}
    return Observation(
        observation_id=observation_id,
        feature=feature,
        value=value,
        subject_id=SUBJECT,
        source=ObservationSource.IMAGE,
        image_id=image,
        visibility=Visibility.CLEAR,
        reliability=reliability,
        attachment=AttachmentStatus.CONFIRMED_ATTACHED if detachable else None,
    )


def _packet(*observations: Observation) -> EvidencePacket:
    return EvidencePacket(
        subjects=(Subject(subject_id=SUBJECT, kind=SubjectKind.STANDING_TREE),),
        observations=observations,
    )


def _candidate(taxon: str, *support: str, rank: int = 1) -> Candidate:
    return Candidate(
        taxon=taxon,
        resolution=Resolution.GENUS,
        supporting_evidence_ids=support,
        score=SupportStrength.MODERATE,
        rank=rank,
    )


def _state(case, packet: EvidencePacket, *candidates: Candidate) -> GraphState:
    return GraphState(
        case=case,
        evidence=packet,
        candidate_sets=(CandidateSet(subject_id=SUBJECT, candidates=candidates),),
    )


# Plated bark: the reading pine shares with spruce, fir and sycamore.
PLATES = _obs("plates", "bark.texture", "scaly_plates")
# Two-needle fascicles: what the pine card itself calls diagnostic.
FASCICLES = _obs("fascicles", "needles.fascicles", "two")


class TestDiscriminatingSupport:
    def test_a_supporting_feature_alone_discriminates_nothing(self, knowledge):
        card = knowledge.taxon("pinus")
        leader = _candidate("pinus", "plates")
        assert discriminating_support(card, _packet(PLATES), leader, SUBJECT) == ()

    def test_a_strong_positive_hit_discriminates(self, knowledge):
        card = knowledge.taxon("pinus")
        leader = _candidate("pinus", "plates", "fascicles")
        packet = _packet(PLATES, FASCICLES)
        assert discriminating_support(card, packet, leader, SUBJECT) == ("fascicles",)

    def test_an_uncited_strong_hit_does_not_count(self, knowledge):
        # The packet holds a fascicle reading, but the leader's admitted support does not
        # cite it: discrimination is about what the verdict rests on.
        card = knowledge.taxon("pinus")
        leader = _candidate("pinus", "plates")
        assert discriminating_support(card, _packet(PLATES, FASCICLES), leader, SUBJECT) == ()

    def test_a_card_declared_exception_counts_as_diagnostic(self, knowledge):
        card = knowledge.taxon("betula")
        bark = _obs("bark", "bark.pattern", "white_papery_with_black_marks")
        leader = _candidate("betula", "bark")
        assert discriminating_support(card, _packet(bark), leader, SUBJECT) == ("bark",)


class TestDefaultPolicyRequiresDiscriminatingSupport:
    """On by default since v0.10."""

    def test_the_switch_is_on_by_default(self, node_context):
        assert node_context.config.graph.require_discriminating_support is True

    def test_supporting_only_names_no_taxon_and_says_why(self, simple_case, node_context):
        state = _state(simple_case, _packet(PLATES), _candidate("pinus", "plates"))
        decision = decide_subject(state, node_context, state.candidate_sets[0])
        assert decision.selected_taxon is None
        assert decision.status is DecisionStatus.INSUFFICIENT_EVIDENCE
        assert decision.unresolved_questions[0].startswith(NO_DISCRIMINATING_SUPPORT_PHRASE)
        assert "needles.fascicles" in decision.unresolved_questions[0]
        derivation = node_context.recorder._decision_derivations[SUBJECT]
        assert derivation.resolution_binding_source == "no_discriminating_support"

    def test_abstaining_keeps_the_evidence_and_the_hypotheses(self, simple_case, node_context):
        peeling = _obs("peeling", "bark.peeling", "irregular_plates")
        state = _state(
            simple_case,
            _packet(PLATES, peeling),
            _candidate("acer_pseudoplatanus", "peeling"),
            _candidate("pinus", "plates", rank=2),
        )
        decision = decide_subject(state, node_context, state.candidate_sets[0])
        assert decision.selected_taxon is None
        assert decision.supporting_evidence  # what was observed is still reported
        consistent = next(
            q for q in decision.unresolved_questions if q.startswith("Still consistent")
        )
        assert "Acer" in consistent
        assert "Pinus" in consistent
        assert decision.best_next_photo is not None  # and what would split them
        # Abstention is a verdict, not a deletion: the candidates are untouched.
        assert [c.taxon for c in state.candidate_sets[0].ordered] == [
            "acer_pseudoplatanus",
            "pinus",
        ]

    def test_discriminating_evidence_still_names_the_genus(self, simple_case, node_context):
        state = _state(
            simple_case, _packet(PLATES, FASCICLES), _candidate("pinus", "plates", "fascicles")
        )
        decision = decide_subject(state, node_context, state.candidate_sets[0])
        assert decision.selected_taxon == "pinus"
        assert decision.resolution is Resolution.GENUS
        assert not any(
            q.startswith(NO_DISCRIMINATING_SUPPORT_PHRASE) for q in decision.unresolved_questions
        )

    def test_bark_alone_is_not_banned_when_the_card_calls_it_diagnostic(
        self, simple_case, node_context
    ):
        # The rule is about what the card calls diagnostic, not about which organ was seen.
        bark = _obs("bark", "bark.pattern", "white_papery_with_black_marks")
        state = _state(simple_case, _packet(bark), _candidate("betula", "bark"))
        decision = decide_subject(state, node_context, state.candidate_sets[0])
        assert decision.selected_taxon == "betula"


class TestLegacyPolicy:
    """Off: the pre-v0.10 behaviour, kept for comparative evaluation."""

    def test_supporting_only_is_named_at_low_confidence_and_disclosed(
        self, simple_case, node_context
    ):
        legacy = with_legacy_policy(node_context)
        state = _state(simple_case, _packet(PLATES), _candidate("pinus", "plates"))
        decision = decide_subject(state, legacy, state.candidate_sets[0])
        assert decision.selected_taxon == "pinus"
        assert decision.resolution is Resolution.GENUS
        assert decision.status is DecisionStatus.PROBABLE
        assert decision.unresolved_questions[0].startswith(NO_DISCRIMINATING_SUPPORT_PHRASE)

    def test_an_undistinguished_rival_is_named(self, simple_case, node_context):
        peeling = _obs("peeling", "bark.peeling", "irregular_plates")
        state = _state(
            simple_case,
            _packet(PLATES, peeling),
            _candidate("acer_pseudoplatanus", "peeling"),
            _candidate("pinus", "plates", rank=2),
        )
        decision = decide_subject(state, with_legacy_policy(node_context), state.candidate_sets[0])
        assert "Not ruled out on this evidence" in " ".join(decision.unresolved_questions)

    def test_the_default_is_never_more_specific_than_legacy(self, simple_case, node_context):
        state = _state(simple_case, _packet(PLATES), _candidate("pinus", "plates"))
        legacy = decide_subject(state, with_legacy_policy(node_context), state.candidate_sets[0])
        default = decide_subject(state, node_context, state.candidate_sets[0])
        assert resolution_rank(default.resolution) <= resolution_rank(legacy.resolution)


class TestFailedReadingsAreSilence:
    """A failed reading stays visible to diagnostics and never counts as disagreement."""

    def test_an_unresolved_fascicle_count_does_not_veto_pine(self, knowledge):
        unresolved = _obs("count", "needles.fascicles", "unresolved")
        assert self_contradiction_hits(knowledge.taxon("pinus"), (unresolved,)) == ()

    def test_it_is_still_reported_by_the_audit(self, knowledge):
        unresolved = _obs("count", "needles.fascicles", "unresolved")
        assert audit_observation(unresolved, knowledge).status is CoverageStatus.NOT_A_READING


class TestCorrelatedReadingsCountOnce:
    def test_three_readings_of_one_bark_are_one_organ(self, knowledge):
        card = knowledge.taxon("acer_pseudoplatanus")
        peeling = _obs("peeling", "bark.peeling", "irregular_plates")
        mosaic = _obs("mosaic", "bark.pattern", "mosaic_patchwork")
        strength = derive_support_strength(
            card, _packet(peeling, mosaic), SUBJECT, ("peeling", "mosaic")
        )
        assert strength is SupportStrength.WEAK

    def test_two_organs_agreeing_are_corroboration(self, knowledge):
        card = knowledge.taxon("acer_pseudoplatanus")
        peeling = _obs("peeling", "bark.peeling", "irregular_plates")
        opposite = _obs("opposite", "leaf.arrangement", "opposite")
        strength = derive_support_strength(
            card, _packet(peeling, opposite), SUBJECT, ("peeling", "opposite")
        )
        assert strength is SupportStrength.MODERATE


class TestConiferSeparatingCharacters:
    @pytest.mark.parametrize(
        ("feature", "value"),
        [
            ("needles.arrangement", "clustered_on_short_shoots"),  # larch
            ("needles.attachment", "single_on_disc_base"),  # fir
            ("cones.type", "upright_disintegrating"),  # fir
            ("needles.attachment", "single_on_woody_peg"),  # spruce (pre-existing)
        ],
    )
    def test_pine_is_contradicted_by_another_conifers_diagnostic(self, knowledge, feature, value):
        observation = _obs("seen", feature, value)
        match = match_card(knowledge.taxon("pinus"), _packet(observation), SUBJECT)
        assert match.contradiction_hits == ("seen",)
        assert match.is_disqualified  # needle and cone tiers outrank bark

    @pytest.mark.parametrize(
        ("taxon", "feature", "value"),
        [
            ("picea", "needles.attachment", "single_on_disc_base"),
            ("picea", "cones.type", "upright_disintegrating"),
            ("picea", "needles.arrangement", "clustered_on_short_shoots"),
            ("abies", "needles.arrangement", "clustered_on_short_shoots"),
        ],
    )
    def test_spruce_and_fir_are_contradicted_symmetrically(self, knowledge, taxon, feature, value):
        match = match_card(knowledge.taxon(taxon), _packet(_obs("seen", feature, value)), SUBJECT)
        assert match.contradiction_hits == ("seen",)

    def test_two_zone_scots_pine_bark_supports_pine_without_deciding_it(self, knowledge):
        card = knowledge.taxon("pinus")
        bark = _obs("bark", "bark.pattern", "orange_flaking_upper_plated_base")
        match = match_card(card, _packet(bark), SUBJECT)
        assert match.supporting_hits == ("bark",)
        assert match.strong_hits == ()

    def test_lower_trunk_read_differently_does_not_veto_pine(self, knowledge):
        # The two-zone pattern is a supporting rule precisely so that a lower-trunk view,
        # read as some other pattern, is silence about pine rather than a contradiction.
        other = _obs("lower", "bark.pattern", "deep_fissures_grey")
        assert self_contradiction_hits(knowledge.taxon("pinus"), (other,)) == ()
