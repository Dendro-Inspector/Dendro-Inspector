"""Knowledge is data: lazily loaded, per-taxon, and matched by pure functions."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

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
    confidence_exception_for,
    match_card,
    requirement_selectors,
    unreachable_selectors,
)
from dendro_inspector.schemas.evidence import (
    AttachmentStatus,
    EvidencePacket,
    Observation,
    ObservationSource,
    Reliability,
    Subject,
    Visibility,
)
from dendro_inspector.schemas.taxon import (
    ConfidenceException,
    ExceptionCeiling,
    FeatureExpectation,
    Provenance,
    Resolution,
    SourceType,
    TaxonCard,
)
from tests.conftest import _attachment

DETACHABLE = ("leaf", "needles", "fruit", "cones", "branch", "bud", "seed", "nut", "acorn")


def _obs(
    observation_id: str,
    feature: str,
    value: str,
    *,
    visibility=Visibility.CLEAR,
    reliability=Reliability.MEDIUM,
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
        reliability=reliability,
        attachment=_attachment(feature, attached),
    )


def _packet(*observations: Observation) -> EvidencePacket:
    return EvidencePacket(subjects=(Subject(subject_id="log_1"),), observations=observations)


def _exception_ids(card, evidence, resolution=Resolution.GENUS) -> tuple[str, ...]:
    """Evidence ids earning a card-declared confidence exception, or `()` for none."""
    hit = confidence_exception_for(card, evidence, "log_1", resolution)
    return () if hit is None else hit.evidence_ids


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
        """A half-seen decisive feature the extractor was only moderately sure of."""
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

    def test_a_partial_but_confidently_read_strong_feature_does_unlock_it(self, knowledge):
        """The other meaning of `PARTIAL`: unambiguous, just not filling the frame.

        This is the first link in the birch chain. `bark.pattern` read at high reliability
        through a partial view used to be reported as a decisive feature "not visible",
        in the same answer that cited that observation as its evidence.
        """
        match = match_card(
            knowledge.taxon("pinus"),
            _packet(
                _obs(
                    "obs-1",
                    "needles.fascicles",
                    "two",
                    visibility=Visibility.PARTIAL,
                    reliability=Reliability.HIGH,
                )
            ),
            "log_1",
        )
        assert match.strong_hits == ("obs-1",)
        assert match.full_strong_hits == ("obs-1",)
        assert match.high_confidence_supported
        assert match.missing_for_high_confidence == ()

    def test_declared_contradiction_is_detected(self, knowledge):
        """Single needles on a woody peg disqualify Pinus, per its own card."""
        match = match_card(
            knowledge.taxon("pinus"),
            _packet(_obs("obs-1", "needles.attachment", "single_on_woody_peg")),
            "log_1",
        )
        assert match.has_contradiction
        assert match.is_disqualified
        assert match.disqualifying_hits == ("obs-1",)

    def test_unattached_contradiction_is_recorded_but_cannot_disqualify(self, knowledge):
        match = match_card(
            knowledge.taxon("picea"),
            _packet(_obs("obs-1", "needles.fascicles", "two", attached=False)),
            "log_1",
        )
        assert match.contradiction_hits == ("obs-1",)
        assert not match.is_disqualified

    def test_bark_tier_contradiction_is_recorded_but_cannot_disqualify(self):
        card = TaxonCard(
            taxon_id="test_taxon",
            display_name="Test taxon",
            native_resolution=Resolution.GENUS,
            supported_resolution=(Resolution.GENUS,),
            contradictions=(FeatureExpectation(feature="bark.texture", values=("scaly_plates",)),),
            provenance=Provenance(source="test fixture", source_type=SourceType.INFERRED),
        )

        match = match_card(
            card,
            _packet(_obs("obs-1", "bark.texture", "scaly_plates")),
            "log_1",
        )

        assert match.contradiction_hits == ("obs-1",)
        assert match.disqualifying_hits == ()

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
        taxa = frozenset({"acer_saccharinum", "populus_alba"})
        cards = knowledge.comparisons_for(taxa)
        assert recommended_photos(cards) == (
            "leaf_underside_macro",
            "leaf_attachment_photo",
            "samara_photo",
        )
        assert follow_up_photos(cards, taxa, ()) == recommended_photos(cards)

    def test_a_resolved_discriminator_loses_its_photograph(self, knowledge):
        """Bark peeling read off this trunk means another bark macro answers nothing."""
        taxa = frozenset({"betula", "populus_alba"})
        photos = follow_up_photos(knowledge.comparisons_for(taxa), taxa, ("bark.peeling",))
        assert photos == ("leaf_underside_macro",)

    def test_betula_populus_card_does_not_call_shared_arrangement_decisive(self, knowledge):
        card = knowledge.comparison("betula-populus-alba")
        assert "leaf.arrangement" not in {
            difference.feature for difference in card.decisive_differences
        }
        assert "leaf_attachment_photo" not in card.recommended_follow_up_photos

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


class TestCardDeclaredConfidenceException:
    """The declared escape from an otherwise unconditional evidence-tier ceiling.

    The ceilings and the cards were in direct conflict: Betula declares
    `bark.pattern = white_papery_with_black_marks` a strong positive and accepts
    `bark.pattern_or_leaf` for high confidence, while the ceiling said no bark observation
    could exceed `low`. A correctly-identified birch could not be reported above 50-69/100
    whatever the photograph showed, and no model change could alter that.

    The ceiling stays the default for everything that does not declare otherwise. What a
    card may declare, and what evidence earns it, is the whole of the policy.
    """

    def test_a_declared_diagnostic_bark_value_earns_the_exemption(self, knowledge):
        evidence = _packet(
            _obs(
                "obs-1",
                "bark.pattern",
                "white_papery_with_black_marks",
                reliability=Reliability.HIGH,
            )
        )

        assert _exception_ids(knowledge.taxon("betula"), evidence) == ("obs-1",)

    def test_it_survives_a_partial_view_read_confidently(self, knowledge):
        """The whole birch chain: partial framing, high reliability, requirement satisfied."""
        evidence = _packet(
            _obs(
                "obs-1",
                "bark.pattern",
                "white_papery_with_black_marks",
                visibility=Visibility.PARTIAL,
                reliability=Reliability.HIGH,
            )
        )
        card = knowledge.taxon("betula")

        assert match_card(card, evidence, "log_1").missing_for_high_confidence == ()
        assert _exception_ids(card, evidence) == ("obs-1",)

    def test_a_partial_view_read_without_confidence_earns_nothing(self, knowledge):
        """The exemption consumes the corrected trust policy instead of restating it."""
        evidence = _packet(
            _obs(
                "obs-1",
                "bark.pattern",
                "white_papery_with_black_marks",
                visibility=Visibility.PARTIAL,
                reliability=Reliability.MEDIUM,
            )
        )

        assert _exception_ids(knowledge.taxon("betula"), evidence) == ()

    def test_a_strong_positive_bark_feature_alone_earns_nothing(self, knowledge):
        """Fagus declares `bark.texture = smooth_grey` strong, and gets no exemption.

        Appearing among a card's strong positives is not the assertion. The card has to say
        this exact value is diagnostic enough to lift a confidence ceiling, and only Betula
        says that in this pack.
        """
        evidence = _packet(
            _obs("obs-1", "bark.texture", "smooth_grey", reliability=Reliability.HIGH)
        )
        card = knowledge.taxon("fagus")

        assert match_card(card, evidence, "log_1").strong_hits == ("obs-1",)
        assert _exception_ids(card, evidence) == ()

    def test_generic_rough_bark_earns_nothing(self, knowledge):
        """ "Definitely an oak, from the bark" stays capped. That is FAILURE 8."""
        evidence = _packet(
            _obs(
                "obs-1",
                "bark.texture",
                "deep_longitudinal_fissures",
                reliability=Reliability.HIGH,
            )
        )

        assert _exception_ids(knowledge.taxon("quercus"), evidence) == ()

    def test_a_species_claim_earns_nothing_from_the_same_bark(self, knowledge):
        """`max_resolution: genus` is on the card, and it is the point of the field.

        The prompt permits very high confidence for the *genus* from this bark and asks for
        leaves and thin twigs before a species. Recognising a birch and naming which birch
        are not the same assertion, and one photograph of bark cannot make them one.
        """
        evidence = _packet(
            _obs(
                "obs-1",
                "bark.pattern",
                "white_papery_with_black_marks",
                reliability=Reliability.HIGH,
            )
        )
        card = knowledge.taxon("betula")

        assert _exception_ids(card, evidence, Resolution.GENUS) == ("obs-1",)
        assert _exception_ids(card, evidence, Resolution.SPECIES) == ()

    def test_contradicted_evidence_earns_nothing(self):
        """An exception is not a way to out-argue the evidence that the card is wrong.

        Betula declares no contradictions, so this needs a card that does: the declared
        reading is present and matches, and the same packet carries a feature the card
        itself writes out as disqualifying. The reading still matches; the card is still
        wrong, and a ceiling is not lifted for a taxon the evidence has ruled out.
        """
        card = TaxonCard(
            taxon_id="test_taxon",
            display_name="Test taxon",
            native_resolution=Resolution.GENUS,
            supported_resolution=(Resolution.GENUS,),
            strong_positive_features=(
                FeatureExpectation(feature="bark.pattern", values=("white_papery",)),
            ),
            contradictions=(
                FeatureExpectation(feature="leaf.underside", values=("white_tomentose",)),
            ),
            confidence_exceptions=(
                ConfidenceException(
                    requires=(
                        FeatureExpectation(feature="bark.pattern", values=("white_papery",)),
                    ),
                    max_resolution=Resolution.GENUS,
                    ceiling=ExceptionCeiling.VERY_HIGH,
                ),
            ),
            provenance=Provenance(source="test fixture", source_type=SourceType.INFERRED),
        )
        clean = _packet(_obs("obs-1", "bark.pattern", "white_papery", reliability=Reliability.HIGH))
        contradicted = _packet(
            _obs("obs-1", "bark.pattern", "white_papery", reliability=Reliability.HIGH),
            _obs("obs-2", "leaf.underside", "white_tomentose", reliability=Reliability.HIGH),
        )

        assert _exception_ids(card, clean) == ("obs-1",)
        assert match_card(card, contradicted, "log_1").has_contradiction
        assert _exception_ids(card, contradicted) == ()

    def test_exactly_one_card_in_this_pack_declares_an_exception(self, knowledge):
        """A count, so a careless card edit shows up as a failure rather than a surprise."""
        declaring = {
            taxon_id
            for taxon_id in knowledge.available_taxon_ids()
            if (card := knowledge.try_taxon(taxon_id)) is not None and card.confidence_exceptions
        }

        assert declaring == {"betula"}


class TestConfidenceExceptionDeclarationIsValidated:
    """What a card may declare is checked at load, not trusted at decision time."""

    @staticmethod
    def _card(
        strong: tuple[FeatureExpectation, ...],
        exceptions: tuple[ConfidenceException, ...],
        *,
        supported: tuple[Resolution, ...] = (Resolution.GENUS,),
    ) -> TaxonCard:
        return TaxonCard(
            taxon_id="test_taxon",
            display_name="Test taxon",
            native_resolution=supported[0],
            supported_resolution=supported,
            strong_positive_features=strong,
            confidence_exceptions=exceptions,
            provenance=Provenance(source="test fixture", source_type=SourceType.INFERRED),
        )

    @staticmethod
    def _exception(feature: str, value: str, **kwargs) -> ConfidenceException:
        return ConfidenceException(
            requires=(FeatureExpectation(feature=feature, values=(value,)),),
            max_resolution=kwargs.pop("max_resolution", Resolution.GENUS),
            ceiling=kwargs.pop("ceiling", ExceptionCeiling.VERY_HIGH),
        )

    def test_a_value_the_card_does_not_call_strong_is_refused(self):
        """A card cannot lift a ceiling on evidence it does not otherwise call decisive."""
        with pytest.raises(ValidationError, match="must also appear in"):
            self._card(
                (FeatureExpectation(feature="bark.pattern", values=("white_papery",)),),
                (self._exception("bark.pattern", "something_else"),),
            )

    def test_a_colour_reading_is_refused(self):
        """Colour is supporting evidence however favourable the photograph looks."""
        strong = (FeatureExpectation(feature="wood.tone", values=("yellowish",)),)

        with pytest.raises(ValidationError, match="cannot rest on a colour reading"):
            self._card(strong, (self._exception("wood.tone", "yellowish"),))

    def test_an_exception_cannot_reach_past_what_the_card_supports(self):
        """A genus-only card declaring a species exception would out-claim itself."""
        strong = (FeatureExpectation(feature="bark.pattern", values=("white_papery",)),)

        with pytest.raises(ValidationError, match="may not reach"):
            self._card(
                strong,
                (
                    self._exception(
                        "bark.pattern", "white_papery", max_resolution=Resolution.SPECIES
                    ),
                ),
            )

    def test_a_non_bark_feature_is_accepted(self):
        """The primitive is not bark-specific, and the prompt's other examples are not bark.

        Section 6 lists clear palmate maple leaves alongside birch bark. Whether that becomes
        a declaration on the Acer card is a separate owner decision (F5); the mechanism must
        not be the thing that blocks it.
        """
        leaf = (FeatureExpectation(feature="leaf.shape", values=("palmate_lobed",)),)

        card = self._card(leaf, (self._exception("leaf.shape", "palmate_lobed"),))

        assert card.confidence_exceptions[0].ceiling is ExceptionCeiling.VERY_HIGH

    def test_a_value_the_card_calls_strong_is_accepted(self):
        strong = (FeatureExpectation(feature="bark.pattern", values=("white_papery",)),)

        card = self._card(strong, (self._exception("bark.pattern", "white_papery"),))

        assert card.confidence_exceptions[0].requires == strong


class TestTheTwoBirches:
    """The same bark pattern, two reliability readings, two outcomes.

    This contrast is the whole point of items 3 and 4 together, and it is why neither is a
    blanket loosening. `evals/public/light-trunk-birch-001` is a distant, backlit trunk
    whose `bark.pattern` the extractor recorded as `partial` + `low`: it must stay capped,
    and the suite requires it to. The live case that prompted this work recorded the same
    feature and the same value as `partial` + `high`, and was pinned at the same band
    anyway.

    One number separates them, and it is the one number that should.
    """

    FEATURE = "bark.pattern"
    VALUE = "white_papery_with_black_marks"

    def _card_and_evidence(self, knowledge, reliability):
        return knowledge.taxon("betula"), _packet(
            _obs(
                "obs-1",
                self.FEATURE,
                self.VALUE,
                visibility=Visibility.PARTIAL,
                reliability=reliability,
            )
        )

    def test_the_confidently_read_birch_is_freed(self, knowledge):
        card, evidence = self._card_and_evidence(knowledge, Reliability.HIGH)

        assert match_card(card, evidence, "log_1").missing_for_high_confidence == ()
        assert _exception_ids(card, evidence) == ("obs-1",)

    def test_the_uncertainly_read_birch_stays_capped(self, knowledge):
        card, evidence = self._card_and_evidence(knowledge, Reliability.LOW)

        assert match_card(card, evidence, "log_1").missing_for_high_confidence != ()
        assert _exception_ids(card, evidence) == ()
