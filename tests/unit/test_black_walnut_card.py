"""The Juglans nigra card, on constructed evidence (AGENTS.md section 16: non-golden tests).

Added after a live upload of a planted walnut that the pack could not name: the trunk was
called Fraxinus from diamond-fissured bark, while the nuts and pinnate leaves pointed at
Juglans. These tests state the card's rules on synthetic packets, not on that upload.
"""

from __future__ import annotations

from dendro_inspector.knowledge.candidate_validation import validate_candidate_set
from dendro_inspector.knowledge.taxon_cards import match_card
from dendro_inspector.schemas.candidates import Candidate, CandidateSet, SupportStrength
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
from dendro_inspector.schemas.taxon import Resolution

SUBJECT = "standing_tree"


def _obs(observation_id: str, feature: str, value: str) -> Observation:
    detachable = feature.split(".")[0] in {"leaf", "leaflet", "nut", "fruit"}
    return Observation(
        observation_id=observation_id,
        feature=feature,
        value=value,
        subject_id=SUBJECT,
        source=ObservationSource.IMAGE,
        image_id="img-1",
        visibility=Visibility.CLEAR,
        reliability=Reliability.HIGH,
        attachment=AttachmentStatus.CONFIRMED_ATTACHED if detachable else None,
    )


def _packet(*observations: Observation) -> EvidencePacket:
    return EvidencePacket(
        subjects=(Subject(subject_id=SUBJECT, kind=SubjectKind.STANDING_TREE),),
        observations=observations,
    )


def _proposal(taxon: str, *support: str) -> CandidateSet:
    return CandidateSet(
        subject_id=SUBJECT,
        candidates=(
            Candidate(
                taxon=taxon,
                resolution=Resolution.SPECIES,
                supporting_evidence_ids=support,
                score=SupportStrength.STRONG,
                rank=1,
            ),
        ),
    )


def test_the_card_loads_with_the_genus_it_shares_with_common_walnut(knowledge):
    card = knowledge.taxon("juglans_nigra")
    assert card.native_resolution is Resolution.SPECIES
    assert card.identity_at_or_broader(Resolution.GENUS).taxon_id == "juglans"
    assert "juglans_nigra" in knowledge.taxon("juglans_regia").common_confusions
    assert "juglans_nigra" in knowledge.taxon("fraxinus").common_confusions


def test_nut_and_leaflet_characters_are_admitted_as_support(knowledge):
    evidence = _packet(
        _obs("obs-1", "nut.shell_surface", "deeply_corrugated_ridged"),
        _obs("obs-2", "leaflet.count", "fifteen_to_twenty_three"),
        _obs("obs-3", "bark.pattern", "diamond_fissures"),
    )

    validated = validate_candidate_set(
        _proposal("juglans_nigra", "obs-1", "obs-2", "obs-3"), evidence, knowledge
    )

    [candidate] = validated.candidates
    assert set(candidate.supporting_evidence_ids) == {"obs-1", "obs-2", "obs-3"}


def test_opposite_leaves_contradict_walnut(knowledge):
    """The character that separates walnut from ash, whose pinnate leaves are opposite."""
    evidence = _packet(
        _obs("obs-1", "leaf.type", "compound_pinnate"),
        _obs("obs-2", "leaf.arrangement", "opposite"),
    )

    assert match_card(knowledge.taxon("juglans_nigra"), evidence, SUBJECT).has_contradiction
    assert not match_card(knowledge.taxon("fraxinus"), evidence, SUBJECT).has_contradiction


def test_diamond_bark_alone_does_not_pick_walnut_over_ash(knowledge):
    """Shared by both: it supports either, decides neither."""
    evidence = _packet(_obs("obs-1", "bark.pattern", "diamond_fissures"))
    walnut = match_card(knowledge.taxon("juglans_nigra"), evidence, SUBJECT)
    ash = match_card(knowledge.taxon("fraxinus"), evidence, SUBJECT)
    assert not walnut.has_contradiction and not ash.has_contradiction
