"""Knowledge provenance.

Provenance does not make a rule true. It makes a rule attributable — which is the only
property that survives growth from 25 taxa to 100, because at that size nobody can hold in
their head which features somebody actually checked.
"""

from __future__ import annotations

from datetime import date

import pytest
import yaml
from pydantic import ValidationError

from evil_duck_dendro.schemas.taxon import (
    Applicability,
    ComparisonCard,
    DecisiveDifference,
    LifeStage,
    Provenance,
    ReviewState,
    SourceType,
    TaxonCard,
)


class TestProvenanceContract:
    def test_a_review_claim_requires_a_reviewer_and_a_date(self):
        """A card cannot claim review without saying who and when."""
        with pytest.raises(ValidationError, match="requires both reviewed_by and last_reviewed"):
            Provenance(
                source="a field guide",
                source_type=SourceType.EXPERT_REVIEW,
                review_state=ReviewState.REVIEWED,
            )

    def test_a_complete_review_claim_validates(self):
        provenance = Provenance(
            source="a field guide",
            source_type=SourceType.EXPERT_REVIEW,
            review_state=ReviewState.REVIEWED,
            reviewed_by="a dendrologist",
            last_reviewed=date(2026, 7, 25),
        )
        assert provenance.review_state is ReviewState.REVIEWED

    def test_unreviewed_is_the_default(self):
        provenance = Provenance(source="somewhere", source_type=SourceType.INFERRED)
        assert provenance.review_state is ReviewState.UNREVIEWED
        assert provenance.life_stage is LifeStage.ANY
        assert provenance.season is Applicability.ANY

    def test_seasonality_can_be_stated(self):
        """A deciduous character does not survive January, and the card should say so."""
        provenance = Provenance(
            source="domain prompt",
            source_type=SourceType.DOMAIN_PROMPT,
            season=Applicability.GROWING_SEASON,
            life_stage=LifeStage.MATURE,
        )
        assert provenance.season is Applicability.GROWING_SEASON


class TestShippedCards:
    def test_every_taxon_card_declares_provenance(self, repo_root):
        paths = sorted((repo_root / "knowledge" / "taxa").glob("*.yaml"))
        assert paths
        for path in paths:
            card = TaxonCard.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
            assert card.provenance.source
            assert card.provenance.source_type is SourceType.DOMAIN_PROMPT

    def test_every_comparison_card_declares_provenance(self, repo_root):
        paths = sorted((repo_root / "knowledge" / "comparisons").glob("*.yaml"))
        assert paths
        for path in paths:
            card = ComparisonCard.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
            assert card.provenance.source

    def test_nothing_shipped_claims_to_have_been_reviewed(self, repo_root):
        """No dendrologist has seen any of this, and the cards must not imply otherwise."""
        for path in sorted((repo_root / "knowledge" / "taxa").glob("*.yaml")):
            card = TaxonCard.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
            assert card.provenance.review_state is ReviewState.UNREVIEWED
            assert card.provenance.reviewed_by is None


class TestDirectionalDiscriminators:
    def test_a_favoured_taxon_must_be_one_the_feature_separates(self):
        with pytest.raises(ValidationError, match="not among the taxa"):
            DecisiveDifference(
                feature="needles.fascicles",
                separates=("pinus", "picea"),
                favours="quercus",
            )

    def test_discriminators_can_point_one_way(self, knowledge):
        """The confusion edge is symmetric; the discriminator usually is not.

        A counted fascicle of two rules Picea out. It does not rule Pinus out for anybody.
        """
        card = knowledge.comparison("pinus-picea-larix")
        fascicles = next(
            difference
            for difference in card.decisive_differences
            if difference.feature == "needles.fascicles"
        )
        assert fascicles.favours == "pinus"
        assert set(fascicles.separates) == {"pinus", "picea"}

    def test_a_group_discriminator_may_favour_nobody(self, knowledge):
        card = knowledge.comparison("pinus-picea-larix")
        attachment = next(
            difference
            for difference in card.decisive_differences
            if difference.feature == "needles.attachment"
        )
        assert attachment.favours is None
        assert len(attachment.separates) == 3
