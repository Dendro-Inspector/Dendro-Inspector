"""Targeted follow-up photography."""

from __future__ import annotations

from dendro_inspector.graph.state import GraphState
from dendro_inspector.nodes.photo_planner import choose_request
from dendro_inspector.schemas.evidence import EvidencePacket, Subject, SubjectKind
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
