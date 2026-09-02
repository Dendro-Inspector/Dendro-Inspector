"""Reviewer model context is an explicit, deterministic projection."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, cast

import pytest

from dendro_inspector.graph.definition import NodeName
from dendro_inspector.graph.executor import _context_for_node
from dendro_inspector.graph.projections import ReviewProjectionError, build_review_projection
from dendro_inspector.graph.state import GraphState
from dendro_inspector.nodes import _support as support
from dendro_inspector.schemas.candidates import Candidate, CandidateSet, SupportStrength
from dendro_inspector.schemas.decisions import DecisionStatus, FinalDecision
from dendro_inspector.schemas.evidence import (
    AttachmentStatus,
    EvidencePacket,
    Inference,
    Observation,
    ObservationSource,
    Subject,
    SubjectKind,
)
from dendro_inspector.schemas.input import CaseInput, ImageRef
from dendro_inspector.schemas.reviews import Reviewer
from dendro_inspector.schemas.taxon import Confidence, Resolution


def _observation(
    observation_id: str,
    feature: str,
    value: str,
    subject_id: str,
    image_id: str,
    attachment: AttachmentStatus | None = None,
) -> Observation:
    return Observation(
        observation_id=observation_id,
        feature=feature,
        value=value,
        subject_id=subject_id,
        source=ObservationSource.IMAGE,
        image_id=image_id,
        attachment=attachment,
    )


def _image(tmp_path: Any, image_id: str, *, on_disk: bool = True) -> ImageRef:
    path = tmp_path / f"{image_id}.jpg"
    if on_disk:
        path.write_bytes(b"jpeg-bytes")
    return ImageRef(image_id=image_id, path=path)


def _state(tmp_path: Any) -> GraphState:
    images = tuple(_image(tmp_path, image_id) for image_id in ("leaf-img", "bark-img", "other-img"))
    evidence = EvidencePacket(
        subjects=(
            Subject(
                subject_id="tree_1",
                kind=SubjectKind.STANDING_TREE,
                image_ids=("leaf-img", "bark-img"),
            ),
            Subject(
                subject_id="tree_2",
                kind=SubjectKind.STANDING_TREE,
                image_ids=("other-img",),
            ),
        ),
        observations=(
            _observation(
                "leaf-1",
                "leaf.arrangement",
                "opposite",
                "tree_1",
                "leaf-img",
                AttachmentStatus.CONFIRMED_ATTACHED,
            ),
            _observation("bark-1", "bark.texture", "scaly_plates", "tree_1", "bark-img"),
            _observation("other-1", "bark.texture", "smooth", "tree_2", "other-img"),
        ),
    )
    candidates = (
        CandidateSet(
            subject_id="tree_1",
            candidates=(
                Candidate(
                    taxon="fraxinus",
                    resolution=Resolution.GENUS,
                    supporting_evidence_ids=("leaf-1",),
                    score=SupportStrength.MODERATE,
                    rank=1,
                ),
            ),
        ),
    )
    return GraphState(
        case=CaseInput(case_id="projection-case", images=images),
        evidence=evidence,
        candidate_sets=candidates,
    )


def test_projection_does_not_let_candidates_hide_subjects_or_images(tmp_path: Any) -> None:
    projection = build_review_projection(
        NodeName.BOTANICAL_REVIEWER, _state(tmp_path), cast(Any, None)
    )

    assert projection.candidate_subject_ids == ("tree_1",)
    assert "other-1" in projection.evidence_ids
    assert "other-img" in projection.image_ids


def test_bark_survives_the_botanical_projection(tmp_path: Any) -> None:
    """Card strong-positives live in bark/trunk/lenticels; the boundary must not drop them."""
    projection = build_review_projection(
        NodeName.BOTANICAL_REVIEWER, _state(tmp_path), cast(Any, None)
    )

    assert projection.evidence_ids == ("leaf-1", "bark-1", "other-1")
    assert projection.image_ids == ("leaf-img", "bark-img", "other-img")


def test_an_unreadable_photograph_is_not_claimed_as_reviewed(tmp_path: Any) -> None:
    """The provider is sent readable files only; the record must not outrun the payload."""
    base = _state(tmp_path)
    case = base.case.model_copy(
        update={
            "images": (
                _image(tmp_path, "leaf-img"),
                _image(tmp_path, "missing-img", on_disk=False),
            )
        }
    )
    state = base.evolve(case=case.model_dump())

    projection = build_review_projection(NodeName.BOTANICAL_REVIEWER, state, cast(Any, None))

    assert projection.image_ids == ("leaf-img",)
    assert "missing-img" in case.image_ids


def test_boundary_preserves_existing_card_visibility(tmp_path: Any) -> None:
    state = _state(tmp_path)
    botanical = build_review_projection(NodeName.BOTANICAL_REVIEWER, state, cast(Any, None))
    confusion = build_review_projection(NodeName.CONFUSION_REVIEWER, state, cast(Any, None))

    assert botanical.include_comparison_cards
    assert botanical.include_regional_pack
    assert confusion.include_comparison_cards
    assert confusion.include_regional_pack


def test_cited_inference_pulls_in_its_source_observations(tmp_path: Any) -> None:
    base = _state(tmp_path)
    assert base.evidence is not None
    evidence = base.evidence.model_copy(
        update={
            "inferences": (
                Inference(
                    inference_id="inf-1",
                    claim="opposite_scaly_plated_stem",
                    derived_from=("leaf-1", "bark-1"),
                ),
            )
        }
    )
    candidate_set = base.candidate_sets[0]
    candidate = candidate_set.candidates[0].model_copy(
        update={"supporting_evidence_ids": ("inf-1",)}
    )
    state = base.evolve(
        evidence=evidence.model_dump(),
        candidate_sets=(
            candidate_set.model_copy(update={"candidates": (candidate,)}).model_dump(),
        ),
    )

    projection = build_review_projection(NodeName.BOTANICAL_REVIEWER, state, cast(Any, None))

    assert "inf-1" in projection.evidence_ids
    assert "leaf-1" in projection.evidence_ids
    assert "bark-1" in projection.evidence_ids


def test_missing_evidence_fails_closed() -> None:
    state = GraphState(case=CaseInput(case_id="missing-evidence"))

    with pytest.raises(ReviewProjectionError, match="requires evidence"):
        build_review_projection(NodeName.CONFIDENCE_REVIEWER, state, cast(Any, None))


def test_empty_candidate_world_remains_reviewable(tmp_path: Any) -> None:
    base = _state(tmp_path)
    state = base.evolve(candidate_sets=())

    projection = build_review_projection(NodeName.CONFIDENCE_REVIEWER, state, cast(Any, None))

    assert projection.candidate_sets == ()
    assert projection.evidence_ids == ("leaf-1", "bark-1", "other-1")


def test_arbiter_projection_reads_the_stored_provisional_decision(
    tmp_path: Any, node_context: Any
) -> None:
    state = _state(tmp_path).evolve(
        provisional_decisions=(
            FinalDecision(
                subject_id="tree_1",
                selected_taxon="picea",
                selected_taxon_display_name="Stored Picea verdict",
                resolution=Resolution.GENUS,
                confidence=Confidence.LOW,
                status=DecisionStatus.PROBABLE,
            ),
        )
    )

    projection = build_review_projection(NodeName.ARBITER, state, node_context)

    assert len(projection.proposed_assessments) == 1
    assert projection.proposed_assessments[0].selected_taxon == "picea"
    assert projection.proposed_assessments[0].confidence is Confidence.LOW


def test_arbiter_projection_without_a_provisional_decision_fails_closed(
    tmp_path: Any, node_context: Any
) -> None:
    with pytest.raises(ReviewProjectionError, match="requires provisional decisions"):
        build_review_projection(NodeName.ARBITER, _state(tmp_path), node_context)


def test_non_reviewer_node_gets_the_context_unchanged(tmp_path: Any, node_context: Any) -> None:
    state = _state(tmp_path)

    assert _context_for_node(NodeName.EVIDENCE_EXTRACTOR, state, node_context) is node_context


def test_reviewer_mismatch_is_refused(tmp_path: Any, node_context: Any) -> None:
    state = _state(tmp_path)
    ctx = replace(
        node_context,
        review_projection=build_review_projection(NodeName.CONFUSION_REVIEWER, state, node_context),
    )

    with pytest.raises(ReviewProjectionError, match="expected 'botanical'"):
        import asyncio

        asyncio.run(
            support.review_call(ctx, node="botanical_reviewer", reviewer=Reviewer.BOTANICAL)
        )


def test_review_call_without_a_projection_is_refused(node_context: Any) -> None:
    import asyncio

    with pytest.raises(ReviewProjectionError, match="without a reviewer projection"):
        asyncio.run(
            support.review_call(
                node_context, node="botanical_reviewer", reviewer=Reviewer.BOTANICAL
            )
        )


def test_trace_records_the_projection_for_reviewer_nodes(tmp_path: Any, node_context: Any) -> None:
    state = _state(tmp_path)
    _context_for_node(NodeName.BOTANICAL_REVIEWER, state, node_context)
    node_context.recorder.record_node("botanical_reviewer")

    trace = node_context.recorder.build()
    event = next(event for event in trace.events if event.node == "botanical_reviewer")

    assert event.reviewer_projection is not None
    assert event.reviewer_projection.reviewer == "botanical"
    assert event.reviewer_projection.evidence_ids == ("leaf-1", "bark-1", "other-1")
    assert event.reviewer_projection.include_comparison_cards
    assert event.reviewer_projection.include_regional_pack
