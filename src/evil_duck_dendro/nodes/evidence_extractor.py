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

from evil_duck_dendro.config import Role
from evil_duck_dendro.graph.executor import NodeContext
from evil_duck_dendro.graph.state import GraphState
from evil_duck_dendro.nodes._support import case_context, image_inputs, locale_of
from evil_duck_dendro.providers.base import request_structured
from evil_duck_dendro.schemas.evidence import EvidencePacket

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


async def run(state: GraphState, ctx: NodeContext) -> GraphState:
    guard = state.guard
    if guard is not None and not guard.safe_to_continue:
        return state.evolve(evidence=_empty_packet(state))

    context_parts = [case_context(state.case)]
    corrections = corrections_context(state)
    if corrections:
        context_parts.append(corrections)

    provider = ctx.providers.get(Role.PRIMARY)
    packet = await request_structured(
        provider=provider,
        role=Role.PRIMARY.value,
        node=NODE,
        prompt=ctx.prompts.compose(
            NODE,
            context="\n\n".join(context_parts),
            locale=locale_of(state),
        ),
        images=image_inputs(state.case),
        response_model=EvidencePacket,
        recorder=ctx.recorder,
        max_retries=ctx.config.provider_for(Role.PRIMARY).max_structured_retries,
    )

    # The guard's verdict is authoritative: a model that omitted the injection signal
    # does not get to erase it from the record.
    if guard is not None and guard.instruction_like_detected:
        packet = packet.model_copy(update={"instruction_like_content_detected": True})

    return state.evolve(evidence=packet)
