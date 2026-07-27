"""Additional photo planner.

Reached when the evidence cannot carry any taxonomic claim. Its job is to convert "not
enough" into "here is the specific photograph that would fix it" — which is the difference
between a useful abstention and a shrug.

It produces terminal decisions itself: the graph goes straight from here to the response
composer, so there is no later node to fill them in.
"""

from __future__ import annotations

from dendro_inspector.graph.executor import NodeContext
from dendro_inspector.graph.state import GraphState
from dendro_inspector.knowledge.evidence_hierarchy import BAND_INSUFFICIENT
from dendro_inspector.schemas.decisions import (
    DecisionStatus,
    FinalDecision,
    PhotoRequest,
    UserClaimVerdict,
)
from dendro_inspector.schemas.evidence import SubjectKind
from dendro_inspector.schemas.input import DeclaredObjectType

NODE = "photo_planner"

#: What to ask for when the input is a particular kind of close-up that cannot carry an ID.
_BY_DECLARED_TYPE: dict[DeclaredObjectType, tuple[str, str]] = {
    DeclaredObjectType.BARK: (
        "needle_or_leaf_macro",
        "Bark alone rarely separates conifer genera; foliage usually does.",
    ),
    DeclaredObjectType.WOOD: (
        "prepared_end_grain_macro",
        "A cleanly cut, wetted end grain at macro distance resolves wood anatomy.",
    ),
    DeclaredObjectType.SPLIT_FIREWOOD: (
        "prepared_end_grain_and_bark_circumference",
        "Label one piece, then photograph a clean perpendicular end grain and the bark around "
        "that same piece; other pieces in the pile may be different taxa.",
    ),
    DeclaredObjectType.LOG: (
        "cut_end_and_branch_scar",
        "The cut end plus a branch scar gives structure that bark texture cannot.",
    ),
    DeclaredObjectType.LEAF: (
        "whole_leaf_flat_with_scale",
        "A flat, fully-in-frame leaf with a scale reference resolves shape and venation.",
    ),
    DeclaredObjectType.STANDING_TREE: (
        "foliage_close_up_and_whole_crown",
        "Crown silhouette plus a foliage close-up covers habit and detail together.",
    ),
}

_DEFAULT_REQUEST = (
    "foliage_close_up_with_scale",
    "A sharp, evenly lit close-up of foliage with a scale reference is the highest-value "
    "single photograph for most identifications.",
)

_REASON_TEXT: dict[str, str] = {
    "no_subject_identified": "No distinct subject could be separated from the background.",
    "too_few_resolvable_observations": "Too few features were resolvable in the frame.",
    "only_insufficient_features_visible": (
        "Only weak features (such as bark or wood colour) were resolvable. These vary with "
        "age, aspect, moisture and lighting, and cannot carry an identification alone."
    ),
    "no_usable_subject": "No subject in the frame carried usable evidence.",
    "no_evidence": "No evidence could be extracted from the input.",
    "input_unusable": "The request contained neither a readable image nor usable text.",
}

_SUBJECT_KIND_TO_OBJECT_TYPE: dict[SubjectKind, DeclaredObjectType] = {
    SubjectKind.SPLIT_WOOD: DeclaredObjectType.SPLIT_FIREWOOD,
    SubjectKind.WOOD_SURFACE: DeclaredObjectType.WOOD,
    SubjectKind.BARK_SURFACE: DeclaredObjectType.BARK,
    SubjectKind.LOG: DeclaredObjectType.LOG,
    SubjectKind.STANDING_TREE: DeclaredObjectType.STANDING_TREE,
}


def effective_object_type(
    state: GraphState,
    subject_id: str | None = None,
) -> DeclaredObjectType:
    """Use the declared type, or infer a follow-up-safe kind for one subject only."""
    declared = state.case.declared_object_type
    if declared is not DeclaredObjectType.UNKNOWN:
        return declared
    evidence = state.evidence
    if evidence is None:
        return declared

    subjects = (
        tuple(subject for subject in evidence.subjects if subject.subject_id == subject_id)
        if subject_id is not None
        else evidence.subjects
    )
    if not subjects:
        return declared
    inferred = {
        _SUBJECT_KIND_TO_OBJECT_TYPE[subject.kind]
        for subject in subjects
        if subject.kind in _SUBJECT_KIND_TO_OBJECT_TYPE
    }
    return inferred.pop() if len(inferred) == 1 else declared


def choose_request(
    state: GraphState,
    ctx: NodeContext,
    subject_id: str | None = None,
) -> PhotoRequest:
    """Pick the single most useful next photograph for one subject."""
    follow_ups = ctx.knowledge.follow_up_for(ctx.knowledge.available_taxon_ids())
    object_type = effective_object_type(state, subject_id)
    target, reason = _BY_DECLARED_TYPE.get(object_type, _DEFAULT_REQUEST)
    if object_type is DeclaredObjectType.UNKNOWN and follow_ups:
        target = follow_ups[0]
    return PhotoRequest(target=target, reason=reason, subject_id=subject_id)


def limitation_text(state: GraphState) -> tuple[str, ...]:
    quality = state.quality
    reasons = quality.insufficient_reasons if quality else ()
    described = tuple(_REASON_TEXT.get(reason, reason) for reason in reasons)
    guard = state.guard
    if guard is not None and guard.missing_images:
        described = (
            *described,
            f"{len(guard.missing_images)} referenced image file(s) could not be read.",
        )
    return described or ("Evidence was insufficient for a taxonomic claim.",)


async def run(state: GraphState, ctx: NodeContext) -> GraphState:
    subjects = state.subject_ids or ("case",)
    quality = state.quality

    # The photograph cannot support any assessment of the user's version, in either
    # direction. That is `not_evaluable`, not `possible` — calling it possible would credit
    # the system with an opinion it does not have.
    verdict = (
        UserClaimVerdict.NOT_EVALUABLE if state.case.user_claim else UserClaimVerdict.NOT_PROVIDED
    )

    return state.evolve(
        decisions=tuple(
            FinalDecision(
                subject_id=subject_id,
                status=DecisionStatus.INSUFFICIENT_EVIDENCE,
                unresolved_questions=limitation_text(state),
                best_next_photo=choose_request(state, ctx, subject_id),
                arbiter_used=state.arbiter_used,
                user_claim_verdict=verdict,
                evidence_tier=quality.tier_for(subject_id) if quality else 1,
                confidence_band=BAND_INSUFFICIENT,
            )
            for subject_id in subjects
        )
    )
