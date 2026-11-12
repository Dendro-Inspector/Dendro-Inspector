"""Closed bark/trunk vocabulary, age-specific rules and non-diagnostic context."""

from __future__ import annotations

import json

from dendro_inspector.knowledge.taxon_cards import (
    LIFE_STAGE_FEATURE,
    card_value_vocabulary,
    match_card,
    self_contradiction_hits,
)
from dendro_inspector.nodes import _support as support
from dendro_inspector.nodes.evidence_quality import classify_vocabulary_diagnostics
from dendro_inspector.schemas.evidence import (
    EvidencePacket,
    Observation,
    ObservationSource,
    Reliability,
    Subject,
    SubjectKind,
)
from dendro_inspector.schemas.taxon import FeatureExpectation, LifeStage

SUBJECT = "tree"


def _obs(observation_id: str, feature: str, value: str) -> Observation:
    return Observation(
        observation_id=observation_id,
        subject_id=SUBJECT,
        feature=feature,
        value=value,
        source=ObservationSource.IMAGE,
        image_id="img-1",
        reliability=Reliability.HIGH,
    )


def _packet(*observations: Observation) -> EvidencePacket:
    return EvidencePacket(
        subjects=(Subject(subject_id=SUBJECT, kind=SubjectKind.STANDING_TREE),),
        observations=observations,
    )


def test_every_card_value_on_a_closed_path_is_in_its_list(knowledge):
    """A card token outside the closed list is one the extractor is told never to use."""
    sets = knowledge.value_sets()
    assert sets is not None
    vocabulary = card_value_vocabulary(knowledge.taxa(knowledge.available_taxon_ids()))
    outside = {
        (feature, value)
        for feature, values in vocabulary.items()
        if (allowed := sets.allowed(feature)) is not None
        for value in values
        if value not in allowed
    }
    assert not outside, sorted(outside)


def test_the_extractor_gets_closed_lists_with_descriptions(node_context):
    context = support.evidence_value_vocabulary_context(node_context)
    closed = json.loads(context.split("```json\n")[2].split("\n```", maxsplit=1)[0])

    assert closed["trunk.form"]["fluted_muscular"]
    assert set(closed[LIFE_STAGE_FEATURE]) == {"young", "mature", "old"}
    # Descriptions say what is visible and never which tree it is.
    text = json.dumps(closed).lower()
    for taxon in ("betula", "birch", "fagus", "beech", "quercus", "oak"):
        assert taxon not in text


def test_the_extractor_is_told_bark_paths_are_read_together_and_structure_first(node_context):
    """One trunk has a texture and a plate build; reading one must not stand in for the other.

    A structural value withheld because shade muted the colour is evidence lost: the
    extractor prompt already says colour is never decisive, and this says the converse.
    """
    context = support.evidence_value_vocabulary_context(node_context)
    instruction = context.split("## Closed bark and trunk vocabulary")[1].split("```json")[0]
    assert "not alternatives" in instruction
    assert "each `bark.*` path" in instruction
    assert "structure first" in instruction
    closed = json.loads(context.split("```json\n")[2].split("\n```", maxsplit=1)[0])
    # A path with a single value is the one most easily skipped; it must still be offered.
    assert closed["bark.plate_build"]
    assert "pine" not in json.dumps(closed["bark.plate_build"]).lower()


def _card_with_young_only_bark(knowledge):
    card = knowledge.taxon("fagus")
    young_bark = FeatureExpectation(
        feature="bark.texture", values=("smooth_grey",), life_stage=LifeStage.YOUNG
    )
    strong = tuple(rule for rule in card.strong_positive_features if rule.feature != "bark.texture")
    return card.model_copy(update={"strong_positive_features": (*strong, young_bark)})


def test_an_age_specific_strong_rule_does_not_veto_another_age(knowledge):
    card = _card_with_young_only_bark(knowledge)
    old_bark = _obs("obs-1", "bark.texture", "deep_longitudinal_fissures")
    old = _obs("obs-2", LIFE_STAGE_FEATURE, "old")

    assert self_contradiction_hits(card, (old_bark,)) == ()
    assert self_contradiction_hits(card, (old_bark, old)) == ()


def test_an_age_specific_strong_rule_vetoes_at_its_own_age(knowledge):
    card = _card_with_young_only_bark(knowledge)
    young_rough = _obs("obs-1", "bark.texture", "deep_longitudinal_fissures")
    young = _obs("obs-2", LIFE_STAGE_FEATURE, "young")

    assert self_contradiction_hits(card, (young_rough, young)) == ("obs-1",)


def test_an_any_age_strong_rule_still_vetoes(knowledge):
    """Beech keeps smooth bark at every age: scaly bark still rules it out."""
    packet = _packet(_obs("obs-1", "bark.texture", "fine_scales"))
    assert match_card(knowledge.taxon("fagus"), packet, SUBJECT).contradicts_own_card


def test_an_old_birch_base_is_not_a_veto(knowledge):
    """The dark rugged base of an old birch agrees with the white upper trunk."""
    packet = _packet(
        _obs("obs-1", "bark.pattern", "black_rugged_fissured_base_diamonds"),
        _obs("obs-2", LIFE_STAGE_FEATURE, "old"),
    )
    match = match_card(knowledge.taxon("betula"), packet, SUBJECT)
    assert not match.contradicts_own_card
    assert "obs-1" in match.supporting_hits


def test_moss_lichen_and_trunk_age_are_not_knowledge_gaps():
    epiphytes = _obs("obs-1", "bark.epiphytes", "moss_on_north_side")
    age = _obs("obs-2", LIFE_STAGE_FEATURE, "old")
    unknown_bark = _obs("obs-3", "bark.ridge_form", "rounded")

    weak, gaps = classify_vocabulary_diagnostics((epiphytes, age, unknown_bark))

    assert {o.observation_id for o in weak} == {"obs-1", "obs-2"}
    assert [o.observation_id for o in gaps] == ["obs-3"]
