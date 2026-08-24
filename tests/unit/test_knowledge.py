"""Knowledge is data: lazily loaded, per-taxon, and matched by pure functions."""

from __future__ import annotations

import pytest

from dendro_inspector.knowledge.comparison_cards import (
    decisive_features_between,
    drop_resolved_photos,
    follow_up_photos,
    insufficient_features,
    photo_bindings,
    recommended_photos,
    relies_only_on_insufficient_features,
)
from dendro_inspector.knowledge.regional_packs import (
    likely_in_region,
    region_assumption_risk,
    unlikely_in_region,
)
from dendro_inspector.knowledge.taxon_cards import (
    card_value_vocabulary,
    match_card,
    requirement_selectors,
    unreachable_selectors,
)
from dendro_inspector.schemas.evidence import (
    AttachmentStatus,
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


class TestRequirementGrammar:
    """`required_for_high_confidence` is a two-operator expression, not a bare string.

    `_and_` binds tighter than `_or_`, selectors are canonical feature paths or feature
    families, and nothing turns an underscore into a dot. Every case below was reachable
    only after the cards stopped inventing names like `bark_pattern`.
    """

    def test_exact_feature_path_satisfies_its_limb_of_a_disjunction(self, knowledge):
        """Betula's own strong feature must satisfy Betula's own requirement.

        The domain prompt's section 14 says white papery bark with black marks is enough to
        name the genus. For weeks the deterministic output quoted that observation as
        support and reported the same requirement as missing, in one decision.
        """
        match = match_card(
            knowledge.taxon("betula"),
            _packet(_obs("obs-1", "bark.pattern", "white_papery_with_black_marks")),
            "log_1",
        )
        assert match.missing_for_high_confidence == ()

    def test_a_family_selector_matches_any_feature_beneath_it(self, knowledge):
        match = match_card(
            knowledge.taxon("betula"),
            _packet(_obs("obs-1", "leaf.shape", "small_triangular_serrate")),
            "log_1",
        )
        assert match.missing_for_high_confidence == ()

    def test_a_sibling_feature_does_not_satisfy_a_path_selector(self, knowledge):
        """`bark.pattern` is the selector; other bark features are not it."""
        match = match_card(
            knowledge.taxon("betula"),
            _packet(_obs("obs-1", "bark.peeling", "thin_layers")),
            "log_1",
        )
        assert match.missing_for_high_confidence == ("bark.pattern_or_leaf",)

    @pytest.mark.parametrize(
        "attachment",
        (AttachmentStatus.UNKNOWN, AttachmentStatus.CONFIRMED_DETACHED),
    )
    def test_unattached_foliage_cannot_satisfy_a_requirement(self, knowledge, attachment):
        """The attachment rule outranks the grammar: unattached leaves prove nothing here."""
        observation = _obs("obs-1", "leaf.shape", "small_triangular_serrate").model_copy(
            update={"attachment": attachment}
        )
        match = match_card(
            knowledge.taxon("betula"),
            _packet(observation),
            "log_1",
        )
        assert match.missing_for_high_confidence == ("bark.pattern_or_leaf",)

    def test_a_conjunction_needs_every_selector(self, knowledge):
        card = knowledge.taxon("populus_alba")
        one_half = match_card(
            card,
            _packet(_obs("obs-1", "leaf.underside", "white_tomentose")),
            "log_1",
        )
        assert one_half.missing_for_high_confidence == ("leaf.underside_and_leaf.arrangement",)

        both = match_card(
            card,
            _packet(
                _obs("obs-1", "leaf.underside", "white_tomentose"),
                _obs("obs-2", "leaf.arrangement", "alternate"),
            ),
            "log_1",
        )
        assert both.missing_for_high_confidence == ()

    def test_the_conjunction_holds_for_every_card_that_declares_one(self, knowledge):
        card = knowledge.taxon("acer_saccharinum")
        assert match_card(
            card,
            _packet(_obs("obs-1", "leaf.arrangement", "opposite")),
            "log_1",
        ).missing_for_high_confidence == ("leaf.underside_and_leaf.arrangement",)
        assert (
            match_card(
                card,
                _packet(
                    _obs("obs-1", "leaf.underside", "pale_not_tomentose"),
                    _obs("obs-2", "leaf.arrangement", "opposite"),
                ),
                "log_1",
            ).missing_for_high_confidence
            == ()
        )

    @pytest.mark.parametrize(
        ("taxon_id", "value"),
        (("carpinus", "fluted_muscular"), ("fagus", "straight_cylindrical")),
    )
    def test_a_non_leaf_limb_satisfies_a_disjunction_on_its_own(self, knowledge, taxon_id, value):
        """Carpinus and Fagus requirements must both notice their trunk-form evidence."""
        match = match_card(
            knowledge.taxon(taxon_id),
            _packet(_obs("obs-1", "trunk.form", value)),
            "log_1",
        )
        assert match.missing_for_high_confidence == ()

    @pytest.mark.parametrize(
        ("taxon_id", "value"),
        [("prunus_armeniaca", "apricot"), ("prunus_cerasifera", "small_round_drupe")],
    )
    def test_fruit_requirements_name_the_feature_that_carries_them(
        self, knowledge, taxon_id, value
    ):
        """Both stone-fruit cards required `fruit_present`, which is not a feature.

        "Плід закриває дискусію" — and the requirement has to be able to notice that the
        fruit is in the frame.
        """
        match = match_card(
            knowledge.taxon(taxon_id),
            _packet(_obs("obs-1", "fruit.type", value)),
            "log_1",
        )
        assert match.missing_for_high_confidence == ()

    def test_the_grammar_reads_and_inside_or(self):
        assert requirement_selectors("leaf.underside_and_leaf.arrangement_or_fruit.type") == (
            ("leaf.underside", "leaf.arrangement"),
            ("fruit.type",),
        )

    def test_no_card_requirement_can_go_unsatisfiable_unnoticed(self, knowledge):
        """The repository-wide gate lives in `tests/contract/test_data_contract.py`.

        This is the unit-level half: the helper it uses must actually report a dead limb,
        including one hidden behind a limb that works.
        """
        card = knowledge.taxon("betula")
        assert unreachable_selectors(card, ("bark.pattern", "leaf.shape")) == ()
        assert unreachable_selectors(card, ("leaf.shape",)) == ("bark.pattern",)


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


class TestFollowUpPhotoSelection:
    """Which photograph to ask for is chosen from declared data, not from list order.

    Every discriminator that a photograph can resolve says so on the comparison card, so
    the planner can tell a question apart from a question already answered.
    """

    def test_nothing_resolved_yet_keeps_the_declared_order(self, knowledge):
        taxa = frozenset({"betula", "populus_alba"})
        cards = knowledge.comparisons_for(taxa)
        assert follow_up_photos(cards, taxa, ()) == recommended_photos(cards)

    def test_a_resolved_discriminator_loses_its_photograph(self, knowledge):
        """Bark peeling read off this trunk means another bark macro answers nothing."""
        taxa = frozenset({"betula", "populus_alba"})
        photos = follow_up_photos(knowledge.comparisons_for(taxa), taxa, ("bark.peeling",))
        assert photos == ("leaf_underside_macro", "leaf_attachment_photo")

    def test_a_photograph_answering_two_features_survives_one_of_them(self, knowledge):
        """A bark macro bound to texture and to lenticels still has lenticels to answer."""
        taxa = frozenset({"robinia_pseudoacacia", "morus", "prunus"})
        photos = follow_up_photos(knowledge.comparisons_for(taxa), taxa, ("bark.texture",))
        assert "bark_macro_mid_trunk" in photos

    def test_bindings_ignore_features_the_card_in_hand_cannot_use(self, knowledge):
        """Betula has no `bark.texture` rule, so a photograph of it proves nothing here.

        What survives is every bark character Betula does declare — which is why a trunk
        with all three of them read off it has nothing left to gain from a fourth bark macro.
        """
        usable = frozenset(card_value_vocabulary((knowledge.taxon("betula"),)))
        bindings = photo_bindings(knowledge.comparisons(), usable)
        assert bindings["bark_macro_mid_trunk"] == frozenset(
            {"bark.pattern", "bark.peeling", "lenticels.orientation"}
        )
        assert "bark.texture" not in {
            feature for features in bindings.values() for feature in features
        }

    def test_an_unbound_photograph_is_never_dropped(self):
        """No declared binding means unknown value, and unknown is not zero."""
        bindings = {"bark_macro_mid_trunk": frozenset({"bark.peeling"})}
        photos = ("bark_macro_mid_trunk", "twig_photo")
        assert drop_resolved_photos(photos, bindings, ("bark.peeling",)) == ("twig_photo",)
        assert drop_resolved_photos(photos, bindings, ()) == photos


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
