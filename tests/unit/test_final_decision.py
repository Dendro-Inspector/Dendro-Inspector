"""The decision engine's three absolutes: capped by the card, downgrades only, never forced."""

from __future__ import annotations

import pytest

from evil_duck_dendro.nodes.final_decision import cap_resolution
from evil_duck_dendro.schemas.taxon import Provenance, Resolution, SourceType, TaxonCard


def _card(*supported: Resolution) -> TaxonCard:
    return TaxonCard(
        taxon_id="pinus",
        display_name="Pinus",
        supported_resolution=supported,
        provenance=Provenance(source="test fixture", source_type=SourceType.INFERRED),
    )


class TestResolutionCap:
    def test_species_claim_is_capped_to_genus_when_the_card_supports_only_genus(self):
        """The rule that stops a confident model from manufacturing a species."""
        assert cap_resolution(Resolution.SPECIES, _card(Resolution.GENUS)) is Resolution.GENUS

    def test_supported_claim_passes_through(self):
        card = _card(Resolution.GENUS, Resolution.SPECIES)
        assert cap_resolution(Resolution.SPECIES, card) is Resolution.SPECIES

    def test_unknown_taxon_can_never_reach_species(self):
        """No card means nothing in this project can justify a species-level claim."""
        assert cap_resolution(Resolution.SPECIES, None) is Resolution.GENUS

    def test_unknown_taxon_keeps_a_modest_claim(self):
        assert cap_resolution(Resolution.GENUS, None) is Resolution.GENUS

    def test_cap_picks_the_narrowest_supported_level_at_or_below_the_claim(self):
        card = _card(Resolution.FAMILY, Resolution.GENUS)
        assert cap_resolution(Resolution.SPECIES, card) is Resolution.GENUS

    def test_cap_falls_back_to_the_broadest_supported_level(self):
        card = _card(Resolution.SPECIES)
        assert cap_resolution(Resolution.FAMILY, card) is Resolution.SPECIES

    @pytest.mark.parametrize(
        "claimed",
        [Resolution.FAMILY, Resolution.GENUS, Resolution.SPECIES_GROUP, Resolution.SPECIES],
    )
    def test_cap_never_widens_beyond_what_was_claimed(self, claimed):
        card = _card(
            Resolution.FAMILY, Resolution.GENUS, Resolution.SPECIES_GROUP, Resolution.SPECIES
        )
        assert cap_resolution(claimed, card) is claimed
