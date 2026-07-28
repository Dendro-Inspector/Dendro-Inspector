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

from dendro_inspector.config import Role
from dendro_inspector.graph.executor import NodeContext
from dendro_inspector.graph.state import GraphState
from dendro_inspector.nodes._support import (
    case_context,
    case_image_inputs,
    evidence_value_vocabulary_context,
    locale_of,
)
from dendro_inspector.providers.base import request_structured
from dendro_inspector.schemas.evidence import EvidencePacket, GeneratedEvidencePacket

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
        return state.evolve(evidence=_empty_packet(state))

    context_parts = [
        case_context(state.case),
        evidence_value_vocabulary_context(ctx),
    ]
    corrections = corrections_context(state)
    if corrections:
        context_parts.append(corrections)

    provider = ctx.providers.get(Role.PRIMARY)
    generated = await request_structured(
        provider=provider,
        role=Role.PRIMARY.value,
        node=NODE,
        prompt=ctx.prompts.compose(
            NODE,
            context="\n\n".join(context_parts),
            locale=locale_of(state),
        ),
        images=case_image_inputs(state, ctx),
        response_model=GeneratedEvidencePacket,
        recorder=ctx.recorder,
        cache_prefix_chars=ctx.prompts.cacheable_prefix_chars(locale_of(state)),
        max_retries=ctx.config.provider_for(Role.PRIMARY).max_structured_retries,
    )

    packet = reconcile_packet(state, generated.to_evidence_packet())
    return state.evolve(evidence=packet)
