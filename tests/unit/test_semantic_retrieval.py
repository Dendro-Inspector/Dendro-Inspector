"""Retrieval can show related cards; only exact observations can support them."""

from __future__ import annotations

import yaml

from dendro_inspector.config import GraphConfig, KnowledgeConfig, load_config
from dendro_inspector.knowledge.candidate_validation import (
    cards_in_play,
    retrieve_cards,
    validate_candidate_set,
)
from dendro_inspector.knowledge.loader import KnowledgeBase
from dendro_inspector.schemas.candidates import CandidateSet
from dendro_inspector.schemas.taxon import ValueVocabulary
from tests.unit.test_candidate_validation import _candidate, _obs, _packet


def _knowledge(tmp_path, knowledge):
    """Synthetic relations exercise the mechanism without adding botanical knowledge."""
    (tmp_path / "taxa").mkdir()
    template = knowledge.taxon("tilia").model_dump(mode="json", exclude={"value_vocabulary"})
    for taxon, value in (("exact", "broad"), ("alpha", "narrow"), ("beta", "narrow")):
        payload = {
            **template,
            "taxon_id": taxon,
            "strong_positive_features": [{"feature": "leaf.shape", "values": [value]}],
            "supporting_features": [],
            "contradictions": [],
            "confidence_exceptions": [],
            "diagnostic_combinations": [],
        }
        (tmp_path / "taxa" / f"{taxon}.yaml").write_text(yaml.safe_dump(payload), encoding="utf-8")
    (tmp_path / "vocabulary.yaml").write_text(
        yaml.safe_dump(
            {
                "provenance": template["provenance"],
                "refinements": {"leaf.shape": {"narrow": "broad"}},
            }
        ),
        encoding="utf-8",
    )
    return KnowledgeBase(KnowledgeConfig(root=tmp_path))


def test_semantic_retrieval_does_not_grant_support(tmp_path, knowledge):
    kb = _knowledge(tmp_path, knowledge)
    packet = _packet(_obs("leaf", "leaf.shape", "broad"))
    assert cards_in_play(packet, kb, ("log_1",)) == ("exact",)
    assert cards_in_play(packet, kb, ("log_1",), semantic=True) == ("alpha", "beta", "exact")
    proposed = CandidateSet(subject_id="log_1", candidates=(_candidate("alpha", 1, "leaf"),))
    assert not validate_candidate_set(proposed, packet, kb).candidates
    record = next(
        r for r in retrieve_cards(packet, kb, ("log_1",), semantic=True) if r.taxon == "alpha"
    )
    assert record.semantic_only
    assert record.reasons[0].relation == "observed_broader"


def test_semantic_limit_preserves_exact_cards(tmp_path, knowledge):
    kb = _knowledge(tmp_path, knowledge)
    packet = _packet(_obs("leaf", "leaf.shape", "broad"))
    assert cards_in_play(packet, kb, ("log_1",), semantic=True, semantic_limit=1) == (
        "alpha",
        "exact",
    )


def test_word_overlap_and_other_subjects_do_not_retrieve(tmp_path, knowledge):
    kb = _knowledge(tmp_path, knowledge)
    packet = _packet(_obs("leaf", "leaf.shape", "broad_unrelated"))
    assert not cards_in_play(packet, kb, ("log_1",), semantic=True)
    packet = _packet(_obs("leaf", "leaf.shape", "broad"))
    assert not cards_in_play(packet, kb, ("another_tree",), semantic=True)


def test_existing_relations_are_directional_and_feature_scoped(knowledge):
    vocabulary = knowledge.vocabulary()
    assert vocabulary.retrieval_relation("fruit.type", "drupe", "apricot") == "observed_broader"
    assert vocabulary.retrieval_relation("fruit.type", "apricot", "drupe") == "observed_narrower"
    assert vocabulary.retrieval_relation("fruit.type", "apricot", "small_round_drupe") is None
    assert vocabulary.retrieval_relation("leaf.shape", "apricot", "drupe") is None
    assert isinstance(vocabulary, ValueVocabulary)


def test_default_off_and_image_override_keeps_flag(monkeypatch):
    assert not GraphConfig().semantic_retrieval
    monkeypatch.setenv("DENDRO_SEMANTIC_RETRIEVAL", "true")
    monkeypatch.setenv("DENDRO_IMAGE_MAX_EDGE_PX", "1024")
    assert load_config().graph.semantic_retrieval
