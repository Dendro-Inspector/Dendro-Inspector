"""Targeted follow-up photography."""

from __future__ import annotations

import asyncio

import pytest

from dendro_inspector.graph.state import GraphState
from dendro_inspector.nodes.photo_planner import choose_request, run
from dendro_inspector.schemas.evidence import (
    EvidencePacket,
    Observation,
    ObservationSource,
    Subject,
    SubjectKind,
    Visibility,
)
from dendro_inspector.schemas.input import DeclaredObjectType


def test_split_firewood_requests_matching_views_of_one_labelled_piece(simple_case, node_context):
    case = simple_case.model_copy(
        update={"declared_object_type": DeclaredObjectType.SPLIT_FIREWOOD}
    )

    request = choose_request(GraphState(case=case), node_context)

    assert request.target == "prepared_end_grain_and_bark_circumference"
    assert "same piece" in request.reason


def test_unknown_declared_type_uses_extracted_split_wood(simple_case, node_context):
    case = simple_case.model_copy(update={"declared_object_type": DeclaredObjectType.UNKNOWN})
    state = GraphState(
        case=case,
        evidence=EvidencePacket(
            subjects=(Subject(subject_id="piece_1", kind=SubjectKind.SPLIT_WOOD),)
        ),
    )

    request = choose_request(state, node_context)

    assert request.target == "prepared_end_grain_and_bark_circumference"


def test_unknown_declared_type_uses_extracted_wood_surface(simple_case, node_context):
    case = simple_case.model_copy(update={"declared_object_type": DeclaredObjectType.UNKNOWN})
    state = GraphState(
        case=case,
        evidence=EvidencePacket(
            subjects=(Subject(subject_id="cut_face_1", kind=SubjectKind.WOOD_SURFACE),)
        ),
    )

    request = choose_request(state, node_context)

    assert request.target == "prepared_end_grain_macro"


def test_bark_follow_up_is_neutral_without_a_taxon_candidate(simple_case, node_context):
    case = simple_case.model_copy(update={"declared_object_type": DeclaredObjectType.UNKNOWN})
    state = GraphState(
        case=case,
        evidence=EvidencePacket(
            subjects=(Subject(subject_id="bark_1", kind=SubjectKind.BARK_SURFACE),)
        ),
    )

    request = choose_request(state, node_context, "bark_1")

    assert request.target == "attached_foliage_or_reproductive_structure"
    assert "conifer" not in request.reason.lower()
    assert "same subject" in request.reason.lower()


def test_multi_tree_bark_view_proves_ownership_before_requesting_foliage_detail(
    simple_case, node_context
):
    case = simple_case.model_copy(update={"declared_object_type": DeclaredObjectType.STANDING_TREE})
    state = GraphState(
        case=case,
        evidence=EvidencePacket(
            subjects=(Subject(subject_id="tree_1", kind=SubjectKind.STANDING_TREE),),
            observations=(
                Observation(
                    observation_id="obs-bark",
                    feature="bark.texture",
                    value="coarse_furrowed",
                    subject_id="tree_1",
                    source=ObservationSource.IMAGE,
                    image_id="img-1",
                ),
            ),
            possible_multiple_taxa=True,
        ),
    )

    request = choose_request(state, node_context, "tree_1")

    assert request.target == "attached_foliage_or_reproductive_structure"
    assert "same subject" in request.reason.lower()


def test_inferred_photo_type_is_scoped_to_each_subject(simple_case, node_context):
    case = simple_case.model_copy(update={"declared_object_type": DeclaredObjectType.UNKNOWN})
    state = GraphState(
        case=case,
        evidence=EvidencePacket(
            subjects=(
                Subject(subject_id="firewood", kind=SubjectKind.SPLIT_WOOD),
                Subject(subject_id="tree", kind=SubjectKind.STANDING_TREE),
            )
        ),
    )

    firewood = choose_request(state, node_context, "firewood")
    tree = choose_request(state, node_context, "tree")
    ambiguous = choose_request(state, node_context)

    assert firewood.target == "prepared_end_grain_and_bark_circumference"
    assert firewood.subject_id == "firewood"
    assert tree.target == "foliage_close_up_and_whole_crown"
    assert tree.subject_id == "tree"
    assert ambiguous.target != firewood.target


@pytest.mark.parametrize("visibility", [Visibility.CLEAR, Visibility.PARTIAL])
def test_visible_firewood_group_requests_one_labelled_piece(simple_case, node_context, visibility):
    case = simple_case.model_copy(update={"declared_object_type": DeclaredObjectType.UNKNOWN})
    state = GraphState(
        case=case,
        evidence=EvidencePacket(
            subjects=(
                Subject(subject_id="pile", kind=SubjectKind.MATERIAL_GROUP),
                Subject(subject_id="tree", kind=SubjectKind.STANDING_TREE),
            ),
            observations=(
                Observation(
                    observation_id="material",
                    feature="context.material",
                    value="split_firewood",
                    subject_id="pile",
                    visibility=visibility,
                    source=ObservationSource.IMAGE,
                    image_id="img-1",
                ),
            ),
        ),
    )
    request = choose_request(state, node_context, "pile")
    assert request.target == "prepared_end_grain_and_bark_circumference"
    assert request.subject_id == "pile"
    assert "same piece" in request.reason
    terminal = asyncio.run(run(state, node_context))
    assert terminal.decisions[0].best_next_photo == request
    assert terminal.decisions[0].selected_taxon is None
    assert choose_request(state, node_context, "tree").target == "foliage_close_up_and_whole_crown"
    assert choose_request(state, node_context).target == "foliage_close_up_with_scale"


def test_material_group_does_not_borrow_another_subjects_material(simple_case, node_context):
    case = simple_case.model_copy(update={"declared_object_type": DeclaredObjectType.UNKNOWN})
    state = GraphState(
        case=case,
        evidence=EvidencePacket(
            subjects=(
                Subject(subject_id="pile", kind=SubjectKind.MATERIAL_GROUP),
                Subject(subject_id="piece", kind=SubjectKind.SPLIT_WOOD),
            ),
            observations=(
                Observation(
                    observation_id="material",
                    feature="context.material",
                    value="split_firewood",
                    subject_id="piece",
                    source=ObservationSource.IMAGE,
                    image_id="img-1",
                ),
            ),
        ),
    )
    assert choose_request(state, node_context, "pile").target == "foliage_close_up_with_scale"


@pytest.mark.parametrize(
    ("source", "visibility", "material"),
    [
        (ObservationSource.IMAGE, Visibility.NOT_VISIBLE, "split_firewood"),
        (ObservationSource.IMAGE, Visibility.OBSCURED, "split_firewood"),
        (ObservationSource.USER, Visibility.CLEAR, "split_firewood"),
        (ObservationSource.IMAGE, Visibility.CLEAR, "leaves"),
    ],
)
def test_material_group_requires_visible_firewood_evidence(
    simple_case, node_context, source, visibility, material
):
    case = simple_case.model_copy(update={"declared_object_type": DeclaredObjectType.UNKNOWN})
    state = GraphState(
        case=case,
        evidence=EvidencePacket(
            subjects=(Subject(subject_id="pile", kind=SubjectKind.MATERIAL_GROUP),),
            observations=(
                Observation(
                    observation_id="material",
                    feature="context.material",
                    value=material,
                    subject_id="pile",
                    source=source,
                    visibility=visibility,
                    image_id="img-1" if source is ObservationSource.IMAGE else None,
                ),
            ),
        ),
    )
    assert choose_request(state, node_context, "pile").target == "foliage_close_up_with_scale"
