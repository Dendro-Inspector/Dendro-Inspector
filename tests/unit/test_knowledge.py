"""Knowledge is data: lazily loaded, per-taxon, and matched by pure functions."""

from __future__ import annotations

from evil_duck_dendro.knowledge.comparison_cards import (
    decisive_features_between,
    insufficient_features,
    relies_only_on_insufficient_features,
)
from evil_duck_dendro.knowledge.regional_packs import (
    likely_in_region,
    region_assumption_risk,
    unlikely_in_region,
)
from evil_duck_dendro.knowledge.taxon_cards import match_card
from evil_duck_dendro.schemas.evidence import (
    EvidencePacket,
    Observation,
    ObservationSource,
    Subject,
    Visibility,
)
from tests.conftest import _attachment

DETACHABLE = ("leaf", "needles", "fruit", "cones", "branch", "bud", "seed", "nut", "acorn")


def _obs(
    observation_id: str,
    feature: str,
    value: str,
    *,
    visibility=Visibility.CLEAR,
    attached: bool = True,
) -> Observation:
    return Observation(
        observation_id=observation_id,
        feature=feature,
        value=value,
        subject_id="log_1",
        source=ObservationSource.IMAGE,
        image_id="img-1",
        visibility=visibility,
        attachment=_attachment(feature, attached),
    )


def _packet(*observations: Observation) -> EvidencePacket:
    return EvidencePacket(subjects=(Subject(subject_id="log_1"),), observations=observations)


class TestLoading:
    def test_cards_load_and_validate(self, knowledge):
        card = knowledge.taxon("pinus")
        assert card.display_name.startswith("Pinus")
        assert "picea" in card.common_confusions

    def test_cards_carry_aliases_a_user_might_actually_type(self, knowledge):
        """A user's own version arrives in their language, not in Latin."""
        assert "сосна" in knowledge.taxon("pinus").aliases
        assert "дуб" in knowledge.taxon("quercus").aliases
        assert "акація" in knowledge.taxon("robinia_pseudoacacia").aliases

    def test_loading_is_lazy_and_per_taxon(self, knowledge):
        """The whole catalogue must not be pulled in for every request.

        Reaching into the private cache is the point of the test: laziness is not
        observable from the public surface, and it is the property that keeps prompt size
        from growing with the size of the knowledge base.
        """
        assert knowledge._taxa == {}
        knowledge.taxon("pinus")
        assert set(knowledge._taxa) == {"pinus"}

    def test_unknown_taxon_returns_none_rather_than_raising(self, knowledge):
        assert knowledge.try_taxon("eucalyptus") is None

    def test_available_ids_are_discovered_from_disk(self, knowledge):
        available = set(knowledge.available_taxon_ids())
        assert {"pinus", "picea", "larix", "quercus", "betula", "acer", "prunus"} <= available
        assert len(available) >= 20

    def test_region_pack_loads(self, knowledge):
        region = knowledge.region()
        assert region is not None
        assert "pinus" in region.likely_taxa

    def test_comparison_cards_are_selected_by_taxon_overlap(self, knowledge):
        cards = knowledge.comparisons_for(frozenset({"pinus", "picea"}))
        assert len(cards) == 1
        assert cards[0].comparison_id == "pinus-picea-larix"

    def test_a_single_taxon_matches_no_comparison(self, knowledge):
        assert knowledge.comparisons_for(frozenset({"pinus"})) == ()


class TestCardMatching:
    def test_strong_feature_matches(self, knowledge):
        match = match_card(
            knowledge.taxon("pinus"), _packet(_obs("obs-1", "needles.fascicles", "two")), "log_1"
        )
        assert match.strong_hits == ("obs-1",)
        assert not match.has_contradiction

    def test_partial_strong_feature_cannot_unlock_high_confidence(self, knowledge):
        match = match_card(
            knowledge.taxon("pinus"),
            _packet(
                _obs(
                    "obs-1",
                    "needles.fascicles",
                    "two",
                    visibility=Visibility.PARTIAL,
                )
            ),
            "log_1",
        )
        assert match.strong_hits == ("obs-1",)
        assert match.full_strong_hits == ()
        assert not match.high_confidence_supported

    def test_declared_contradiction_is_detected(self, knowledge):
        """Single needles on a woody peg disqualify Pinus, per its own card."""
        match = match_card(
            knowledge.taxon("pinus"),
            _packet(_obs("obs-1", "needles.attachment", "single_on_woody_peg")),
            "log_1",
        )
        assert match.has_contradiction

    def test_unresolvable_features_neither_support_nor_contradict(self, knowledge):
        match = match_card(
            knowledge.taxon("pinus"),
            _packet(_obs("obs-1", "needles.fascicles", "two", visibility=Visibility.NOT_VISIBLE)),
            "log_1",
        )
        assert match.strong_hits == ()
        assert not match.has_contradiction

    def test_missing_requirement_blocks_high_confidence(self, knowledge):
        match = match_card(
            knowledge.taxon("pinus"),
            _packet(_obs("obs-1", "bark.flake_geometry", "thin_irregular_edge_lifting")),
            "log_1",
        )
        assert "needles_or_cones" in match.missing_for_high_confidence
        assert not match.high_confidence_supported

    def test_either_alternative_satisfies_an_or_requirement(self, knowledge):
        match = match_card(
            knowledge.taxon("pinus"),
            _packet(_obs("obs-1", "cones.scale_shape", "woody_umbo")),
            "log_1",
        )
        assert match.missing_for_high_confidence == ()


class TestComparisonHelpers:
    def test_colour_is_always_insufficient_even_without_a_card(self):
        assert "bark.colour" in insufficient_features(())

    def test_colour_only_evidence_is_detected(self):
        assert relies_only_on_insufficient_features(
            _packet(_obs("obs-1", "bark.colour", "red")), "log_1"
        )

    def test_one_colour_observation_alongside_structure_is_fine(self):
        packet = _packet(
            _obs("obs-1", "bark.colour", "red"),
            _obs("obs-2", "needles.fascicles", "two"),
        )
        assert not relies_only_on_insufficient_features(packet, "log_1")

    def test_decisive_features_come_from_the_card(self, knowledge):
        features = decisive_features_between(
            knowledge.comparisons_for(frozenset({"pinus", "picea"})),
            frozenset({"pinus", "picea"}),
        )
        assert "needles.attachment" in features


class TestRegionalPriors:
    def test_priors_do_not_apply_without_a_location(self, knowledge):
        region = knowledge.region()
        assert not likely_in_region(region, "pinus", None)
        assert not unlikely_in_region(region, "pinus", None)

    def test_missing_location_with_a_loaded_pack_is_flagged_as_a_risk(self, knowledge):
        assert region_assumption_risk(knowledge.region(), None)
        assert not region_assumption_risk(knowledge.region(), "Kyiv Oblast, Ukraine")

    def test_priors_apply_when_a_location_is_supplied(self, knowledge):
        assert likely_in_region(knowledge.region(), "pinus", "Kyiv Oblast, Ukraine")
