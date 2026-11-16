"""Evidence extractor.

Produces the packet everything downstream reasons over. Three properties are enforced here
rather than hoped for:

* the guard's findings are carried into the packet, so a later node cannot lose them;
* a corrupted or empty case short-circuits to an empty packet **without** a model call —
  a controlled failure, not an improvised answer;
* on a retry pass, the accepted corrections are appended to the prompt so the second
  extraction is actually different from the first;
* a subject the model named and then recorded nothing about, on no photograph, is not
  carried into the packet, so it cannot become a verdict about nothing.
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
from dendro_inspector.observability.logging import get_logger
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


def drop_unseen_subjects(packet: EvidencePacket) -> tuple[EvidencePacket, tuple[str, ...]]:
    """Leave out subjects that were named and never seen; return their ids as well.

    A subject with no observation and no photograph it is said to appear in is a name,
    not something the extraction saw. Left in the packet it gets a verdict section of its
    own ("insufficient evidence") and makes a photograph of one tree look like a case with
    several subjects, so the one real subject is no longer treated as what was asked
    about. A subject that names a photograph is kept even with nothing read on it: it was
    seen, and saying that it could not be read is an answer.

    Runs on the collapsed packet, where a component's readings already sit on its
    identity root, and after the uploader's statements have been applied, so a tree that
    only holds credited parts counts as observed. When every subject is unseen the packet
    is returned as it is: there is then no real subject to protect, and the empty-case
    paths downstream expect the subjects they were given.
    """
    observed = {observation.subject_id for observation in packet.observations}
    unseen = tuple(
        subject.subject_id
        for subject in packet.subjects
        if subject.subject_id not in observed and not subject.image_ids
    )
    if not unseen or len(unseen) == len(packet.subjects):
        return packet, ()
    payload = packet.model_dump(mode="python")
    payload["subjects"] = tuple(
        subject for subject in packet.subjects if subject.subject_id not in unseen
    )
    return EvidencePacket.model_validate(payload), unseen


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
    packet, unseen = drop_unseen_subjects(packet)
    if unseen:
        # The raw packet in the trace's evidence history still lists them.
        get_logger(NODE).warning(
            "unseen_subjects_dropped",
            extra={"case_id": state.case.case_id, "subject_ids": list(unseen)},
        )
        if proposed_target in unseen:
            proposed_target = None
    ctx.recorder.record_component_projections(packet)
    ctx.recorder.record_evidence(
        packet, generated=generated, retry_index=state.retries, attestations=attestations
    )
    return state.evolve(evidence=packet, proposed_target_subject_id=proposed_target)
