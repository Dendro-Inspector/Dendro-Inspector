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
from dendro_inspector.knowledge.taxon_cards import (
    diagnostic_combination_for,
    match_card,
    owed_combination,
    self_contradiction_hits,
)
from dendro_inspector.nodes.final_decision import (
    DIAGNOSTIC_COMBINATION_PHRASE,
    NO_DISCRIMINATING_SUPPORT_PHRASE,
    decide_subject,
    discriminating_support,
    rerank_displaces_diagnostic_leader,
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
    WoodSurface,
    requires_wood_surface,
)
from dendro_inspector.schemas.reviews import (
    AdmittedRerank,
    FindingCategory,
    FindingStatus,
    Impact,
    RequiredAction,
    Reviewer,
    ReviewFinding,
    ReviewSynthesis,
    Severity,
)
from dendro_inspector.schemas.taxon import (
    Confidence,
    DiagnosticCombination,
    Resolution,
    TaxonCard,
    resolution_rank,
)
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


def _wood(
    observation_id: str,
    feature: str,
    value: str,
    *,
    reliability: Reliability = Reliability.MEDIUM,
) -> Observation:
    """A reading off a chainsawn log end: real evidence, never prepared end grain."""
    return Observation(
        observation_id=observation_id,
        feature=feature,
        value=value,
        subject_id=SUBJECT,
        source=ObservationSource.IMAGE,
        image_id="img-1",
        visibility=Visibility.CLEAR,
        reliability=reliability,
        wood_surface=WoodSurface.ROUGH_END_GRAIN,
    )


# The three log readings section 14 of the domain prompt names together for pine.
RESIN = _wood("resin", "resin.presence", "present")
HONEY = _wood("honey", "wood.tone", "light_yellow_honey")
LOG = ("plates", "resin", "honey")


class TestDiagnosticCombination:
    """Readings that are shared one by one may name a taxon together — and only name it.

    Source: domain prompt section 14, PINE — "On a pile of logs you may go high if reddish
    bark, coniferous structure and typical log ends are all visible at once." A log has no
    needles and no cones, so without this the pine card could never name one.
    """

    def test_the_complete_combination_discriminates(self, knowledge):
        card = knowledge.taxon("pinus")
        packet = _packet(PLATES, RESIN, HONEY)
        hit = diagnostic_combination_for(card, packet, SUBJECT)
        assert hit is not None
        assert set(hit.evidence_ids) == set(LOG)
        assert set(discriminating_support(card, packet, _candidate("pinus", *LOG), SUBJECT)) == (
            set(LOG)
        )

    @pytest.mark.parametrize("missing", LOG)
    def test_any_missing_row_earns_nothing(self, knowledge, missing):
        rows = {"plates": PLATES, "resin": RESIN, "honey": HONEY}
        present = tuple(row for key, row in rows.items() if key != missing)
        assert diagnostic_combination_for(knowledge.taxon("pinus"), _packet(*present), SUBJECT) is (
            None
        )

    def test_a_row_the_leader_does_not_cite_does_not_count(self, knowledge):
        card = knowledge.taxon("pinus")
        packet = _packet(PLATES, RESIN, HONEY)
        leader = _candidate("pinus", "plates", "honey")
        assert discriminating_support(card, packet, leader, SUBJECT) == ()

    def test_a_doubtful_reading_breaks_the_conjunction(self, knowledge):
        doubtful = _wood("resin", "resin.presence", "present", reliability=Reliability.LOW)
        packet = _packet(PLATES, doubtful, HONEY)
        assert diagnostic_combination_for(knowledge.taxon("pinus"), packet, SUBJECT) is None

    def test_contradicting_evidence_earns_nothing(self, knowledge):
        # Spruce's needle attachment on the same subject: the combination cannot out-argue it.
        peg = _obs("peg", "needles.attachment", "single_on_woody_peg")
        packet = _packet(PLATES, RESIN, HONEY, peg)
        assert diagnostic_combination_for(knowledge.taxon("pinus"), packet, SUBJECT) is None

    def test_the_log_is_named_at_genus_and_no_higher_than_its_tier_allows(
        self, simple_case, node_context
    ):
        state = _state(simple_case, _packet(PLATES, RESIN, HONEY), _candidate("pinus", *LOG))
        decision = decide_subject(state, node_context, state.candidate_sets[0])
        assert decision.selected_taxon == "pinus"
        assert decision.resolution is Resolution.GENUS
        # Permission to name, not to be sure: a rough log end and bark stay at the bark-tier
        # ceiling whatever the combination says.
        assert decision.confidence is Confidence.LOW
        assert decision.status is DecisionStatus.PROBABLE
        assert any(
            q.startswith(DIAGNOSTIC_COMBINATION_PHRASE) for q in decision.unresolved_questions
        )
        derivation = node_context.recorder._decision_derivations[SUBJECT]
        assert "diagnostic_combination" in {b.source for b in derivation.resolution_bounds}
        assert "diagnostic_exception" not in {s.source for s in derivation.confidence_steps}

    def test_bark_and_resin_without_the_log_end_still_name_nothing(self, simple_case, node_context):
        state = _state(simple_case, _packet(PLATES, RESIN), _candidate("pinus", "plates", "resin"))
        decision = decide_subject(state, node_context, state.candidate_sets[0])
        assert decision.selected_taxon is None
        assert decision.status is DecisionStatus.INSUFFICIENT_EVIDENCE

    def test_spruce_is_not_named_from_the_pine_combination(self, simple_case, node_context):
        # The combination belongs to one card. Spruce bark on the same log end stays an
        # abstention: nothing on the spruce card is diagnostic without needles or cones.
        fine = _obs("fine", "bark.texture", "fine_scales")
        state = _state(simple_case, _packet(fine, RESIN), _candidate("picea", "fine"))
        decision = decide_subject(state, node_context, state.candidate_sets[0])
        assert decision.selected_taxon is None

    def test_the_weaker_combination_says_which_look_alikes_it_leaves_open(
        self, simple_case, node_context
    ):
        # Bark, resin and tone do not exclude a spruce or larch log (both carry resin). The
        # answer names pine and says so, with the sign that would settle it.
        state = _state(simple_case, _packet(PLATES, RESIN, HONEY), _candidate("pinus", *LOG))
        decision = decide_subject(state, node_context, state.candidate_sets[0])
        line = next(
            q for q in decision.unresolved_questions if q.startswith(DIAGNOSTIC_COMBINATION_PHRASE)
        )
        assert "Spruce and larch" in line
        assert "cut end" in line

    def test_bark_with_the_pine_cut_end_names_pine_without_resin_or_tone(
        self, simple_case, node_context
    ):
        # Sources on the card: pine has a reddish core in a wide pale band; spruce and fir
        # have no coloured heartwood, larch a very narrow sapwood.
        orange = _obs("orange", "bark.texture", "thin_orange_flaking")
        heart = _wood("heart", "heartwood.demarcation", "reddish_core_wide_pale_sapwood")
        state = _state(simple_case, _packet(orange, heart), _candidate("pinus", "orange", "heart"))
        decision = decide_subject(state, node_context, state.candidate_sets[0])
        assert decision.selected_taxon == "pinus"
        assert decision.resolution is Resolution.GENUS
        assert decision.confidence is Confidence.LOW

    def test_orange_flaking_bark_alone_names_nothing(self, simple_case, node_context):
        # Young spruce bark is also orange-brown and finely flaking.
        orange = _obs("orange", "bark.texture", "thin_orange_flaking")
        state = _state(simple_case, _packet(orange), _candidate("pinus", "orange"))
        decision = decide_subject(state, node_context, state.candidate_sets[0])
        assert decision.selected_taxon is None

    def test_fir_resin_blisters_block_the_log_combination(self, knowledge):
        blisters = _obs("blisters", "bark.surface_marks", "resin_blisters")
        packet = _packet(PLATES, RESIN, HONEY, blisters)
        assert diagnostic_combination_for(knowledge.taxon("pinus"), packet, SUBJECT) is None

    def test_a_larch_cut_end_contradicts_pine_and_blocks_it(self, knowledge):
        card = knowledge.taxon("pinus")
        larch_end = _wood("end", "heartwood.demarcation", "dark_core_very_narrow_sapwood")
        packet = _packet(PLATES, RESIN, HONEY, larch_end)
        assert match_card(card, packet, SUBJECT).contradiction_hits == ("end",)
        assert diagnostic_combination_for(card, packet, SUBJECT) is None

    def test_a_pine_cut_end_contradicts_larch(self, knowledge):
        pine_end = _wood("end", "heartwood.demarcation", "reddish_core_wide_pale_sapwood")
        match = match_card(knowledge.taxon("larix"), _packet(pine_end), SUBJECT)
        assert match.contradiction_hits == ("end",)


class TestOldBirchBase:
    def test_a_dark_base_beside_white_papery_bark_agrees_with_birch(self, knowledge):
        # Woodland Trust: the white bark "becomes black and rugged at the base". Read as one
        # two-zone pattern, it supports birch and is not disagreement on `bark.pattern`.
        white = _obs("white", "bark.pattern", "white_papery_with_black_marks")
        base = _obs("base", "bark.pattern", "pale_upper_dark_rough_base")
        match = match_card(knowledge.taxon("betula"), _packet(white, base), SUBJECT)
        assert not match.contradicts_own_card
        assert "base" in match.supporting_hits
        assert "white" in match.strong_hits

    def test_the_dark_base_alone_still_names_nothing(self, simple_case, node_context):
        # Old white poplar goes dark at the base too; the card says so.
        base = _obs("base", "bark.pattern", "pale_upper_dark_rough_base")
        state = _state(simple_case, _packet(base), _candidate("betula", "base"))
        decision = decide_subject(state, node_context, state.candidate_sets[0])
        assert decision.selected_taxon is None


class TestDiagnosticCombinationDeclarationIsValidated:
    @staticmethod
    def _combination(**requires: str) -> DiagnosticCombination:
        return DiagnosticCombination.model_validate(
            {
                "requires": {key.replace("__", "."): value for key, value in requires.items()},
                "max_resolution": "genus",
            }
        )

    def test_one_organ_described_twice_is_refused(self):
        with pytest.raises(ValueError, match="at least two organs"):
            self._combination(bark__texture="scaly_plates", bark__peeling="irregular_plates")

    def test_colour_does_not_count_as_a_second_organ(self):
        with pytest.raises(ValueError, match="at least two organs"):
            self._combination(bark__texture="scaly_plates", wood__tone="light_yellow_honey")

    def test_a_single_reading_is_not_a_combination(self):
        with pytest.raises(ValueError):
            self._combination(bark__texture="scaly_plates")

    def test_a_reading_the_card_does_not_declare_is_refused(self, knowledge):
        card = knowledge.taxon("pinus")
        foreign = self._combination(bark__texture="fine_scales", resin__presence="present")
        with pytest.raises(ValueError, match="must also appear"):
            TaxonCard.model_validate(
                {**card.model_dump(mode="json"), "diagnostic_combinations": [foreign.model_dump()]}
            )

    def test_only_cards_with_a_recorded_source_declare_one(self, knowledge):
        """New combinations are a naming policy; each one is a reviewed, sourced decision."""
        declaring = sorted(
            taxon_id
            for taxon_id in knowledge.available_taxon_ids()
            if knowledge.taxon(taxon_id).diagnostic_combinations
        )
        assert declaring == DECLARING
        for taxon_id in DECLARING:
            for combination in knowledge.taxon(taxon_id).diagnostic_combinations:
                assert combination.provenance is not None
                assert combination.note


#: Every card that names its taxon from a log or trunk without foliage or fruit. Adding one
#: is a reviewed, sourced decision, so the list is pinned rather than discovered.
DECLARING = [
    "abies",
    "larix",
    "picea",
    "pinus",
    "populus",
    "prunus_avium",
    "quercus",
    "robinia_pseudoacacia",
]


def _clean_reading(observation_id: str, feature: str, value: str) -> Observation:
    """One row read as well as a photograph can: clearly, on a prepared end grain if wood."""
    return Observation(
        observation_id=observation_id,
        feature=feature,
        value=value,
        subject_id=SUBJECT,
        source=ObservationSource.IMAGE,
        image_id="img-1",
        visibility=Visibility.CLEAR,
        reliability=Reliability.HIGH,
        wood_surface=WoodSurface.PREPARED_END_GRAIN if requires_wood_surface(feature) else None,
    )


def _declared_combinations(knowledge) -> list[tuple[str, int]]:
    return [
        (taxon_id, index)
        for taxon_id in DECLARING
        for index, _ in enumerate(knowledge.taxon(taxon_id).diagnostic_combinations)
    ]


class TestEveryDeclaredCombination:
    """Properties every card's combination must hold, whatever the taxon."""

    def test_complete_it_names_the_taxon_and_never_at_high_confidence(
        self, knowledge, simple_case, node_context
    ):
        for taxon_id, index in _declared_combinations(knowledge):
            card = knowledge.taxon(taxon_id)
            combination = card.diagnostic_combinations[index]
            rows = tuple(
                _clean_reading(f"row-{number}", expectation.feature, expectation.values[0])
                for number, expectation in enumerate(combination.requires)
            )
            ids = tuple(row.observation_id for row in rows)
            state = _state(simple_case, _packet(*rows), _candidate(taxon_id, *ids))
            decision = decide_subject(state, node_context, state.candidate_sets[0])

            expected = card.identity_at_or_broader(combination.max_resolution)
            assert expected is not None
            assert decision.selected_taxon == expected.taxon_id, taxon_id
            assert decision.confidence is not Confidence.HIGH, taxon_id
            assert any(
                q.startswith(DIAGNOSTIC_COMBINATION_PHRASE) for q in decision.unresolved_questions
            ), taxon_id

    def test_with_any_one_row_missing_it_earns_nothing(self, knowledge):
        for taxon_id, index in _declared_combinations(knowledge):
            card = knowledge.taxon(taxon_id)
            combination = card.diagnostic_combinations[index]
            rows = tuple(
                _clean_reading(f"row-{number}", expectation.feature, expectation.values[0])
                for number, expectation in enumerate(combination.requires)
            )
            for dropped in range(len(rows)):
                partial = tuple(row for number, row in enumerate(rows) if number != dropped)
                assert diagnostic_combination_for(card, _packet(*partial), SUBJECT) is None, (
                    taxon_id,
                    dropped,
                )

    def test_no_complete_combination_also_completes_another_cards(self, knowledge):
        """One log must not be nameable as two different trees."""
        for taxon_id, index in _declared_combinations(knowledge):
            combination = knowledge.taxon(taxon_id).diagnostic_combinations[index]
            rows = tuple(
                _clean_reading(f"row-{number}", expectation.feature, value)
                for number, expectation in enumerate(combination.requires)
                for value in expectation.values[:1]
            )
            packet = _packet(*rows)
            for other in DECLARING:
                if other != taxon_id:
                    assert diagnostic_combination_for(knowledge.taxon(other), packet, SUBJECT) is (
                        None
                    ), (taxon_id, other)


class TestTheNextLookAtALog:
    """A log is asked for its cut end, not for needles it does not have."""

    def test_bark_and_resin_ask_for_the_cut_end(self, simple_case, node_context):
        state = _state(simple_case, _packet(PLATES, RESIN), _candidate("pinus", "plates", "resin"))
        decision = decide_subject(state, node_context, state.candidate_sets[0])
        assert decision.selected_taxon is None
        request = decision.best_next_photo
        assert request is not None
        assert request.target == "prepared_end_grain_macro"
        assert "heartwood.demarcation" in request.reason

    def test_a_pine_named_on_the_weaker_signs_is_still_asked_for_the_cut_end(
        self, simple_case, node_context
    ):
        state = _state(simple_case, _packet(PLATES, RESIN, HONEY), _candidate("pinus", *LOG))
        decision = decide_subject(state, node_context, state.candidate_sets[0])
        assert decision.selected_taxon == "pinus"
        assert decision.best_next_photo is not None
        assert decision.best_next_photo.target == "prepared_end_grain_macro"

    def test_the_strongest_combination_complete_owes_nothing(self, knowledge):
        heart = _wood("heart", "heartwood.demarcation", "reddish_core_wide_pale_sapwood")
        assert owed_combination(knowledge.taxon("pinus"), _packet(PLATES, heart), SUBJECT) is None

    def test_a_contradicted_card_owes_nothing(self, knowledge):
        blisters = _obs("blisters", "bark.surface_marks", "resin_blisters")
        assert owed_combination(knowledge.taxon("pinus"), _packet(PLATES, blisters), SUBJECT) is (
            None
        )

    def test_a_standing_tree_is_not_asked_for_a_cross_section(
        self, standing_tree_case, node_context
    ):
        state = _state(standing_tree_case, _packet(PLATES), _candidate("pinus", "plates"))
        decision = decide_subject(state, node_context, state.candidate_sets[0])
        assert decision.best_next_photo is not None
        assert decision.best_next_photo.target != "prepared_end_grain_macro"


class TestLogLookAlikeChecks:
    def test_oak_needs_a_prepared_end_grain(self, knowledge):
        # Pores and rays claim anatomy a chainsawn end cannot show; read there they are
        # context and complete nothing.
        pores = _wood("pores", "pores.arrangement", "ring_porous")
        rays = _wood("rays", "rays.visibility", "prominent")
        assert diagnostic_combination_for(
            knowledge.taxon("quercus"), _packet(pores, rays), SUBJECT
        ) is (None)

    def test_rays_visible_on_the_cut_end_count_against_ash(self, knowledge):
        rays = _clean_reading("rays", "rays.visibility", "prominent")
        match = match_card(knowledge.taxon("fraxinus"), _packet(rays), SUBJECT)
        assert match.contradiction_hits == ("rays",)

    def test_an_end_grain_without_resin_canals_counts_against_spruce(self, knowledge):
        none = _clean_reading("canals", "wood.resin_canals", "none_in_end_grain")
        match = match_card(knowledge.taxon("picea"), _packet(none), SUBJECT)
        assert match.contradiction_hits == ("canals",)

    def test_tissue_thin_peeling_bark_blocks_the_cherry_bark_answer(self, knowledge):
        # Young birch is the look-alike of cherry bark; papery peeling is the birch sign.
        card = knowledge.taxon("prunus_avium")
        banded = _obs("banded", "bark.pattern", "grey_reddish_banded")
        lenticels = _obs("lenticels", "lenticels.orientation", "prominent_horizontal")
        peeling = _obs("peeling", "bark.peeling", "thin_layers")
        assert diagnostic_combination_for(card, _packet(banded, lenticels), SUBJECT) is not None
        assert diagnostic_combination_for(card, _packet(banded, lenticels, peeling), SUBJECT) is (
            None
        )

    def test_diamond_lenticels_alone_do_not_name_poplar(self, simple_case, node_context):
        diamonds = _obs("diamonds", "lenticels.shape", "diamond")
        state = _state(simple_case, _packet(diamonds), _candidate("populus", "diamonds"))
        decision = decide_subject(state, node_context, state.candidate_sets[0])
        assert decision.selected_taxon is None

    def test_resin_canals_do_not_count_against_fir(self, knowledge):
        # Wounded fir forms traumatic canals, so their presence is silence about fir.
        present = _clean_reading("canals", "wood.resin_canals", "present")
        match = match_card(knowledge.taxon("abies"), _packet(present), SUBJECT)
        assert match.contradiction_hits == ()
        assert not match.contradicts_own_card


class TestRerankCannotDisplaceADiagnosedLeader:
    """Rank order is not evidence.

    A reviewer may reorder candidates, and validation only checks that each one has some
    card support. That let poplar — coarse bark and a massive trunk, both supporting — be
    moved above a birch whose white papery bark its card declares diagnostic.
    """

    BIRCH_BARK = _obs("white", "bark.pattern", "white_papery_with_black_marks")
    COARSE = _obs("coarse", "bark.texture", "coarse_furrowed")

    @staticmethod
    def _reranked_state(case, packet, original: CandidateSet, reranked: CandidateSet) -> GraphState:
        finding = ReviewFinding(
            finding_id="swap",
            category=FindingCategory.OVERLOOKED_ALTERNATIVE,
            severity=Severity.MAJOR,
            status=FindingStatus.ACCEPTED,
            summary="Poplar explains the coarse base.",
            subject_id=SUBJECT,
            required_action=RequiredAction.RERANK_CANDIDATES,
            impact=Impact.CANDIDATE_CHANGE,
        )
        return GraphState(
            case=case,
            evidence=packet,
            candidate_sets=(original,),
            synthesis=ReviewSynthesis(
                accepted_findings=(finding,),
                admitted_reranks=(
                    AdmittedRerank(
                        finding=finding, reviewer=Reviewer.CONFUSION, candidate_set=reranked
                    ),
                ),
                recommendations=(),
            ),
        )

    def _sets(self) -> tuple[CandidateSet, CandidateSet]:
        birch = _candidate("betula", "white")
        poplar = _candidate("populus", "coarse", rank=2)
        original = CandidateSet(subject_id=SUBJECT, candidates=(birch, poplar))
        reranked = CandidateSet(
            subject_id=SUBJECT,
            candidates=(
                poplar.model_copy(update={"rank": 1}),
                birch.model_copy(update={"rank": 2}),
            ),
        )
        return original, reranked

    def test_a_supporting_only_candidate_does_not_displace_diagnostic_bark(
        self, simple_case, node_context
    ):
        original, reranked = self._sets()
        packet = _packet(self.BIRCH_BARK, self.COARSE)
        assert rerank_displaces_diagnostic_leader(node_context, packet, original, reranked)

        state = self._reranked_state(simple_case, packet, original, reranked)
        decision = decide_subject(state, node_context, original)
        assert decision.selected_taxon == "betula"
        derivation = node_context.recorder._decision_derivations[SUBJECT]
        assert derivation.rerank_source == "withheld"
        assert derivation.rerank_finding_id == "swap"

    def test_a_rerank_toward_diagnostic_evidence_is_applied(self, node_context):
        # The mirror image: the reviewer moves the diagnosed candidate up. Untouched.
        original, reranked = self._sets()
        packet = _packet(self.BIRCH_BARK, self.COARSE)
        assert not rerank_displaces_diagnostic_leader(node_context, packet, reranked, original)

    def test_a_contradicted_leader_may_still_be_displaced(self, node_context):
        # White-felted leaf undersides contradict the birch card; its bark no longer
        # protects its rank.
        original, reranked = self._sets()
        felted = _obs("felt", "leaf.underside", "white_tomentose")
        packet = _packet(self.BIRCH_BARK, self.COARSE, felted)
        assert not rerank_displaces_diagnostic_leader(node_context, packet, original, reranked)

    def test_legacy_policy_applies_the_rerank_as_before(self, simple_case, node_context):
        original, reranked = self._sets()
        packet = _packet(self.BIRCH_BARK, self.COARSE)
        state = self._reranked_state(simple_case, packet, original, reranked)
        decision = decide_subject(state, with_legacy_policy(node_context), original)
        assert decision.selected_taxon == "populus"
