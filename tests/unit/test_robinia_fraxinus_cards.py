"""Card regressions on synthetic evidence, independent of photographs or benchmarks."""

from __future__ import annotations

import asyncio
from datetime import date

import pytest

from dendro_inspector.graph.state import GraphState
from dendro_inspector.knowledge.candidate_validation import validate_candidate_set
from dendro_inspector.knowledge.evidence_hierarchy import requires_attachment
from dendro_inspector.knowledge.taxon_cards import match_card
from dendro_inspector.nodes.final_decision import decide_subject
from dendro_inspector.nodes.response_composer import run as compose_response
from dendro_inspector.schemas.candidates import Candidate, CandidateSet, SupportStrength
from dendro_inspector.schemas.evidence import (
    AttachmentStatus,
    EvidencePacket,
    Observation,
    ObservationSource,
    Reliability,
    Subject,
    SubjectKind,
)
from dendro_inspector.schemas.reference import ReferenceStatus
from dendro_inspector.schemas.taxon import Confidence, Resolution, ReviewState

SUBJECT = "tree"
MEASURED_POD = "flat_oblong_smooth_3_to_8_cm"


@pytest.mark.parametrize("taxon", ["robinia_pseudoacacia", "fraxinus"])
def test_reviewed_cards_record_the_review_scope_and_date(knowledge, taxon):
    card = knowledge.taxon(taxon)
    assert not card.placeholder_content
    assert card.provenance.confidence is Confidence.MEDIUM
    assert card.provenance.review_state is ReviewState.REVIEWED
    assert card.provenance.reviewed_by == "Codex (AI-assisted botanical and implementation review)"
    assert card.provenance.last_reviewed == date(2026, 11, 11)


def _packet(*readings: tuple[str, str]) -> EvidencePacket:
    return EvidencePacket(
        subjects=(Subject(subject_id=SUBJECT, kind=SubjectKind.STANDING_TREE),),
        observations=tuple(
            Observation(
                observation_id=f"obs-{index}",
                subject_id=SUBJECT,
                feature=feature,
                value=value,
                source=ObservationSource.IMAGE,
                image_id="img-1",
                reliability=Reliability.HIGH,
                attachment=(
                    AttachmentStatus.CONFIRMED_ATTACHED if requires_attachment(feature) else None
                ),
            )
            for index, (feature, value) in enumerate(readings)
        ),
    )


def _admit(knowledge, taxon: str, packet: EvidencePacket) -> CandidateSet:
    return validate_candidate_set(
        CandidateSet(
            subject_id=SUBJECT,
            candidates=(
                Candidate(
                    taxon=taxon,
                    resolution=knowledge.taxon(taxon).native_resolution,
                    score=SupportStrength.STRONG,
                    supporting_evidence_ids=tuple(o.observation_id for o in packet.observations),
                    rank=1,
                ),
            ),
        ),
        packet,
        knowledge,
    )


def _decide(context, case, taxon: str, packet: EvidencePacket):
    admitted = _admit(context.knowledge, taxon, packet)
    state = GraphState(case=case, evidence=packet, candidate_sets=(admitted,))
    return decide_subject(state, context, admitted)


@pytest.mark.parametrize("source_confidence", list(Confidence))
@pytest.mark.parametrize(
    ("taxon", "reading"),
    [
        ("fraxinus", ("samara.shape", "single_wing_paddle")),
        ("robinia_pseudoacacia", ("pod.shape", MEASURED_POD)),
    ],
)
def test_review_metadata_changes_status_and_warning_without_changing_identification(
    node_context, simple_case, monkeypatch, taxon, reading, source_confidence
):
    knowledge = node_context.knowledge
    card = knowledge.taxon(taxon)
    packet = _packet(reading)
    reviewed = _decide(node_context, simple_case, taxon, packet)
    assert reviewed.selected_taxon == taxon
    assert reviewed.reference_status is ReferenceStatus.LOCAL_REVIEWED

    def response_for(decision):
        state = GraphState(
            case=simple_case,
            evidence=packet,
            candidate_sets=(_admit(knowledge, taxon, packet),),
            decisions=(decision,),
        )
        response = asyncio.run(compose_response(state, node_context)).response
        assert response is not None
        return response.human_readable

    reviewed_text = response_for(reviewed)
    draft = card.model_copy(
        update={
            "placeholder_content": True,
            "provenance": card.provenance.model_copy(
                update={
                    "confidence": source_confidence,
                    "review_state": ReviewState.UNREVIEWED,
                    "reviewed_by": None,
                    "last_reviewed": None,
                }
            ),
        }
    )
    monkeypatch.setitem(knowledge._taxa, taxon, draft)
    unreviewed = _decide(node_context, simple_case, taxon, packet)
    assert unreviewed.reference_status is ReferenceStatus.LOCAL_UNREVIEWED
    assert reviewed.model_dump(exclude={"reference_status"}) == unreviewed.model_dump(
        exclude={"reference_status"}
    )
    # Promotion removes only the warning; the rendered identification stays identical.
    assert response_for(unreviewed).startswith(reviewed_text + "\n\n>")


@pytest.mark.parametrize(
    "readings",
    [
        (("leaf.arrangement", "opposite"), ("bark.pattern", "diamond_fissures")),
        (("leaf.arrangement", "opposite"), ("leaf.type", "compound_pinnate")),
        (("bud.colour", "black_velvety"), ("bark.pattern", "diamond_fissures")),
    ],
)
def test_ash_compatible_characters_do_not_identify_the_genus(readings, node_context, simple_case):
    decision = _decide(node_context, simple_case, "fraxinus", _packet(*readings))
    assert decision.selected_taxon is None


def test_brown_buds_do_not_veto_ash_samaras(node_context, simple_case):
    packet = _packet(
        ("samara.shape", "single_wing_paddle"),
        ("bark.pattern", "diamond_fissures"),
        ("bud.colour", "brown"),
    )
    assert not match_card(
        node_context.knowledge.taxon("fraxinus"), packet, SUBJECT
    ).contradicts_own_card
    assert _decide(node_context, simple_case, "fraxinus", packet).selected_taxon == "fraxinus"


def test_a_generic_flat_pod_does_not_identify_robinia(node_context, simple_case):
    packet = _packet(("pod.shape", "flat_oblong_legume"), ("leaf.type", "compound_pinnate"))
    assert _decide(node_context, simple_case, "robinia_pseudoacacia", packet).selected_taxon is None


def test_the_detailed_pod_reading_can_support_robinia(node_context, simple_case):
    packet = _packet(("pod.shape", MEASURED_POD), ("leaf.type", "compound_pinnate"))
    decision = _decide(node_context, simple_case, "robinia_pseudoacacia", packet)
    assert decision.selected_taxon == "robinia_pseudoacacia"
    assert decision.resolution is Resolution.SPECIES


def test_generic_thorns_preserve_but_do_not_strengthen_robinia(node_context, simple_case):
    readings = (("pod.shape", MEASURED_POD), ("leaf.type", "compound_pinnate"))
    before = _decide(node_context, simple_case, "robinia_pseudoacacia", _packet(*readings))
    after = _decide(
        node_context,
        simple_case,
        "robinia_pseudoacacia",
        _packet(*readings, ("branch.thorns", "present")),
    )
    assert after.selected_taxon == before.selected_taxon == "robinia_pseudoacacia"
    assert after.confidence is before.confidence


def test_accepted_generic_reading_does_not_hide_a_different_contradiction(knowledge):
    packet = _packet(
        ("pod.shape", MEASURED_POD),
        ("branch.thorns", "present"),
        ("branch.thorns", "branched_trunk_thorns"),
    )
    assert not _admit(knowledge, "robinia_pseudoacacia", packet).candidates


def test_spines_keep_the_existing_silhouette_ceiling(node_context, simple_case):
    decision = _decide(
        node_context,
        simple_case,
        "robinia_pseudoacacia",
        _packet(("branch.thorns", "paired_stipular_spines")),
    )
    assert decision.resolution is Resolution.FAMILY


def test_card_positive_features_have_defined_evidence_families(knowledge):
    from dendro_inspector.knowledge.evidence_hierarchy import family_of, known_families

    unknown = {
        (card.taxon_id, feature.feature)
        for card in knowledge.taxa(knowledge.available_taxon_ids())
        for feature in (*card.strong_positive_features, *card.supporting_features)
        if family_of(feature.feature) not in known_families()
    }
    assert not unknown


def test_an_attached_ash_leaf_names_the_genus_without_samaras(node_context, simple_case):
    """Male ash trees never fruit; finely serrate leaflets on an opposite leaf decide it."""
    packet = _packet(
        ("leaf.arrangement", "opposite"),
        ("leaf.type", "compound_pinnate"),
        ("leaflet.shape", "ovate_lanceolate_serrate"),
    )
    assert _decide(node_context, simple_case, "fraxinus", packet).selected_taxon == "fraxinus"


def test_box_elder_leaflets_rule_out_ash(node_context, simple_case):
    """Acer negundo is opposite and pinnate too, with few coarse or lobed leaflets."""
    packet = _packet(
        ("leaf.arrangement", "opposite"),
        ("leaf.type", "compound_pinnate"),
        ("leaflet.shape", "coarse_serrate_or_lobed"),
    )
    ash = node_context.knowledge.taxon("fraxinus")
    assert match_card(ash, packet, SUBJECT).contradicts_own_card
    assert _decide(node_context, simple_case, "fraxinus", packet).selected_taxon is None
