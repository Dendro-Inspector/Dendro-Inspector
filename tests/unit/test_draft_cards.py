"""Draft cards enter a review queue and never become reviewed knowledge on their own."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from dendro_inspector.cli import app
from dendro_inspector.config import KnowledgeConfig
from dendro_inspector.knowledge.drafts import (
    REVIEW_QUEUE_DIR,
    DraftCardError,
    draft_card,
    write_draft,
)
from dendro_inspector.knowledge.loader import KnowledgeBase
from dendro_inspector.schemas.reference import ReferenceRecord
from dendro_inspector.schemas.taxon import (
    Resolution,
    ReviewState,
    SourceType,
    TaxonCard,
)

URL = "https://references.example.org/taxon/aesculus_hippocastanum"


def _record(**overrides: object) -> ReferenceRecord:
    data: dict[str, object] = {
        "taxon": "aesculus_hippocastanum",
        "source_id": "example_flora",
        "source_url": URL,
        "retrieved_at": "2026-01-15T10:00:00+00:00",
        "name_status": "accepted",
        "accepted_taxon": "aesculus_hippocastanum",
        "documented_features": [
            {"feature": "leaf.type", "values": ["compound_palmate"]},
            {"feature": "fruit.husk", "values": ["spiny_green"]},
        ],
        "lookalikes": ["juglans_regia", "castanea_sativa"],
    }
    data.update(overrides)
    return ReferenceRecord.model_validate(data)


KNOWN = frozenset({"juglans_regia", "pinus"})


class TestDraft:
    def test_a_draft_is_unreviewed_placeholder_content_whatever_its_source(self):
        card = draft_card(_record(), known_taxon_ids=KNOWN)

        assert card.provenance.review_state is ReviewState.UNREVIEWED
        assert card.provenance.reviewed_by is None
        assert card.placeholder_content is True

    def test_every_rule_cites_its_source_and_none_is_decisive(self):
        """Which features are decisive is a reviewer's call, not a record's."""
        card = draft_card(_record(), known_taxon_ids=KNOWN)

        assert card.strong_positive_features == ()
        assert {rule.feature for rule in card.supporting_features} == {"leaf.type", "fruit.husk"}
        for rule in card.supporting_features:
            assert rule.provenance is not None
            assert rule.provenance.source == URL
            assert rule.provenance.source_type is SourceType.LITERATURE
            assert rule.provenance.review_state is ReviewState.UNREVIEWED

    def test_identity_and_confusions(self):
        card = draft_card(_record(), known_taxon_ids=KNOWN)

        assert card.native_resolution is Resolution.SPECIES
        assert card.display_name == "Aesculus hippocastanum"
        assert [identity.taxon_id for identity in card.broader_identities] == ["aesculus"]
        # Only look-alikes that have a card can be compared against.
        assert card.common_confusions == ("juglans_regia",)

    def test_a_feature_documented_twice_becomes_one_rule(self):
        card = draft_card(
            _record(
                documented_features=[
                    {"feature": "leaf.type", "values": ["compound_palmate"]},
                    {"feature": "leaf.type", "values": ["palmately_lobed", "compound_palmate"]},
                ]
            ),
            known_taxon_ids=KNOWN,
        )
        (rule,) = card.supporting_features
        assert rule.values == ("compound_palmate", "palmately_lobed")

    @pytest.mark.parametrize(
        "record",
        [
            _record(name_status="synonym", accepted_taxon="aesculus_turbinata"),
            _record(documented_features=[]),
            _record(taxon="juglans_regia", accepted_taxon="juglans_regia"),
        ],
        ids=["synonym", "nothing-documented", "card-exists"],
    )
    def test_records_that_cannot_honestly_seed_a_card_are_refused(self, record):
        with pytest.raises(DraftCardError):
            draft_card(record, known_taxon_ids=KNOWN)


class TestReviewQueue:
    def _root(self, tmp_path: Path) -> Path:
        (tmp_path / "taxa").mkdir()
        return tmp_path

    def test_the_loader_never_reads_the_queue(self, tmp_path):
        root = self._root(tmp_path)
        target = write_draft(draft_card(_record(), known_taxon_ids=KNOWN), root)

        knowledge = KnowledgeBase(KnowledgeConfig(root=root))

        assert target.parent == root / REVIEW_QUEUE_DIR
        assert knowledge.available_taxon_ids() == ()
        assert knowledge.try_taxon("aesculus_hippocastanum") is None

    def test_the_written_draft_round_trips_as_a_card_without_composed_fields(self, tmp_path):
        target = write_draft(draft_card(_record(), known_taxon_ids=KNOWN), self._root(tmp_path))

        text = target.read_text(encoding="utf-8")
        data = yaml.safe_load(text)

        assert text.startswith("# DRAFT")
        assert "value_vocabulary" not in data
        assert data["placeholder_content"] is True
        assert TaxonCard.model_validate(data).taxon_id == "aesculus_hippocastanum"

    def test_an_existing_draft_is_never_overwritten(self, tmp_path):
        root = self._root(tmp_path)
        card = draft_card(_record(), known_taxon_ids=KNOWN)
        write_draft(card, root)
        with pytest.raises(DraftCardError):
            write_draft(card, root)

    def test_a_card_claiming_review_cannot_enter_the_queue(self, tmp_path):
        card = draft_card(_record(), known_taxon_ids=KNOWN)
        promoted = card.model_copy(update={"placeholder_content": False})
        with pytest.raises(DraftCardError):
            write_draft(promoted, self._root(tmp_path))


class TestCommand:
    def test_draft_card_writes_into_the_queue(self, tmp_path, monkeypatch):
        root = tmp_path / "knowledge"
        (root / "taxa").mkdir(parents=True)
        records = tmp_path / "references.yaml"
        records.write_text(
            yaml.safe_dump({"records": [_record().model_dump(mode="json")]}), encoding="utf-8"
        )
        monkeypatch.setenv("DENDRO_KNOWLEDGE_ROOT", str(root))

        result = CliRunner().invoke(
            app, ["draft-card", "aesculus_hippocastanum", "--references", str(records)]
        )

        assert result.exit_code == 0, result.output
        assert (root / REVIEW_QUEUE_DIR / "aesculus_hippocastanum.yaml").is_file()
        assert not (root / "taxa" / "aesculus_hippocastanum.yaml").exists()

    def test_an_unknown_taxon_fails_without_writing(self, tmp_path, monkeypatch):
        root = tmp_path / "knowledge"
        (root / "taxa").mkdir(parents=True)
        records = tmp_path / "references.yaml"
        records.write_text(yaml.safe_dump({"records": []}), encoding="utf-8")
        monkeypatch.setenv("DENDRO_KNOWLEDGE_ROOT", str(root))

        result = CliRunner().invoke(
            app, ["draft-card", "aesculus_hippocastanum", "--references", str(records)]
        )

        assert result.exit_code == 1
        assert not (root / REVIEW_QUEUE_DIR).exists()
