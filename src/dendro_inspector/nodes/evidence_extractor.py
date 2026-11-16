"""Evidence extractor.

Produces the packet everything downstream reasons over. Three properties are enforced here
rather than hoped for:

* the guard's findings are carried into the packet, so a later node cannot lose them;
* a corrupted or empty case short-circuits to an empty packet **without** a model call —
  a controlled failure, not an improvised answer;
* on a retry pass, the accepted corrections are appended to the prompt so the second
  extraction is actually different from the first.
"""

from __future__ import annotations

from dendro_inspector.config import Adapter, Role, TargetSelection
from dendro_inspector.graph.executor import NodeContext
from dendro_inspector.graph.state import GraphState
from dendro_inspector.nodes._support import (
    case_context,
    case_image_inputs,
    evidence_value_vocabulary_context,
    locale_of,
)
from dendro_inspector.nodes.field_attestation import apply_part_attestations
from dendro_inspector.providers.base import StructuredOutputError, request_structured
from dendro_inspector.schemas.evidence import (
    EvidencePacket,
    GeneratedEvidencePacket,
    TargetedEvidencePacket,
)

NODE = "evidence_extractor"


def corrections_context(state: GraphState) -> str:
    """Render accepted corrections for the retry pass."""
    if not state.corrections:
        return ""
    lines = [
        "## Required corrections from internal review",
        "",
        "The previous extraction was rejected. Address each item:",
        "",
    ]
    lines.extend(
        f"- **{directive.action.value}**"
        + (f" (subject `{directive.subject_id}`)" if directive.subject_id else "")
        + (f" (feature `{directive.target_feature}`)" if directive.target_feature else "")
        + f": {directive.rationale}"
        for directive in state.corrections
    )
    return "\n".join(lines)


MAIN_SUBJECT_CONTEXT = (
    "## Main subject (experiment)\n\n"
    "Also set `main_subject_id` to the `subject_id` of the one subject these photographs "
    "are of: the tree, log or part the photographer framed, usually the one that is "
    "centred, closest or shown in most of the photographs. A subject standing behind or "
    "beside it is not the main subject. Leave it out when several subjects are equally "
    "the subject or you cannot tell. This says which subject the request is about. It is "
    "not evidence for any taxon and must not change what you record about any subject."
)


def _empty_packet(state: GraphState) -> EvidencePacket:
    guard = state.guard
    return EvidencePacket(
        context_limitations=("input_unusable",),
        instruction_like_content_detected=bool(guard and guard.instruction_like_detected),
    )


def reconcile_packet(state: GraphState, packet: EvidencePacket) -> EvidencePacket:
    """Merge conservative facts a model is not allowed to erase."""
    guard = state.guard
    plan = state.plan
    updates: dict[str, bool] = {}
    if guard is not None and guard.instruction_like_detected:
        updates["instruction_like_content_detected"] = True
    if plan is not None and plan.split_firewood_input:
        updates["possible_multiple_taxa"] = True
    return packet.model_copy(update=updates) if updates else packet


async def run(state: GraphState, ctx: NodeContext) -> GraphState:
    guard = state.guard
    if guard is not None and not guard.safe_to_continue:
        packet = _empty_packet(state)
        ctx.recorder.record_evidence(packet, retry_index=state.retries)
        return state.evolve(evidence=packet, proposed_target_subject_id=None)

    context_parts = [
        case_context(state.case),
        evidence_value_vocabulary_context(ctx),
    ]
    corrections = corrections_context(state)
    if corrections:
        context_parts.append(corrections)
    # Off by default: the response schema then is the released one, field for field.
    propose_target = ctx.config.graph.target_selection is TargetSelection.EXTRACTOR
    if propose_target:
        context_parts.append(MAIN_SUBJECT_CONTEXT)

    provider = ctx.providers.get(Role.PRIMARY)
    images = case_image_inputs(state, ctx)
    # Explicit fixture replay consumes synthetic evidence, not photograph bytes. It may
    # reference declared fixture images, but still cannot invent an undeclared image id.
    replay = (
        ctx.config.provider_for(Role.PRIMARY).adapter is Adapter.FAKE
        and provider.adapter_name == "fake"
    )
    available_image_ids = frozenset(
        state.case.image_ids if replay else (image.image_id for image in images)
    )

    def validate_images(packet: GeneratedEvidencePacket) -> None:
        try:
            packet.validate_image_references(available_image_ids)
        except ValueError as exc:
            raise StructuredOutputError(str(exc)) from exc

    response = await request_structured(
        provider=provider,
        role=Role.PRIMARY.value,
        node=NODE,
        prompt=ctx.prompts.compose(
            NODE,
            context="\n\n".join(context_parts),
            locale=locale_of(state),
        ),
        images=images,
        response_model=TargetedEvidencePacket if propose_target else GeneratedEvidencePacket,
        recorder=ctx.recorder,
        cache_prefix_chars=ctx.prompts.cacheable_prefix_chars(locale_of(state)),
        max_retries=ctx.config.provider_for(Role.PRIMARY).max_structured_retries,
        validate_response=validate_images,
    )

    proposed_target: str | None = None
    generated: GeneratedEvidencePacket = response
    if isinstance(response, TargetedEvidencePacket):
        proposed_target = response.main_root_id()
        generated = response.generated()

    packet = reconcile_packet(state, generated.to_evidence_packet())
    packet, attestations = apply_part_attestations(state.case, packet)
    ctx.recorder.record_component_projections(packet)
    ctx.recorder.record_evidence(
        packet, generated=generated, retry_index=state.retries, attestations=attestations
    )
    return state.evolve(evidence=packet, proposed_target_subject_id=proposed_target)
