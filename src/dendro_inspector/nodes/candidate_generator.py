"""Candidate generator.

The model proposes ranked hypotheses; the shared deterministic admission boundary then
keeps only known taxa with exact, same-subject, trusted card support. It also derives missing
decisive features from the cards rather than the model's recollection of what it would need.
"""

from __future__ import annotations

from dendro_inspector.config import Role
from dendro_inspector.graph.executor import NodeContext
from dendro_inspector.graph.state import GraphState
from dendro_inspector.knowledge.candidate_validation import (
    cards_in_play,
    validate_candidate_set_with_report,
)
from dendro_inspector.nodes._support import (
    case_context,
    case_image_inputs,
    evidence_context,
    knowledge_context,
    locale_of,
)
from dendro_inspector.observability.logging import get_logger
from dendro_inspector.providers.base import (
    OUTPUT_EVIDENCE_IDS,
    OUTPUT_SUBJECT_IDS,
    request_structured,
)
from dendro_inspector.schemas.candidates import CandidateProposal, CandidateSet

NODE = "candidate_generator"


async def run(state: GraphState, ctx: NodeContext) -> GraphState:
    evidence = state.evidence
    quality = state.quality
    if evidence is None or quality is None:
        return state

    provider = ctx.providers.get(Role.PRIMARY)
    proposal = await request_structured(
        provider=provider,
        role=Role.PRIMARY.value,
        node=NODE,
        prompt=ctx.prompts.compose(
            NODE,
            context="\n\n".join(
                [
                    case_context(state.case),
                    evidence_context(evidence),
                    knowledge_context(
                        ctx, cards_in_play(evidence, ctx.knowledge, quality.usable_subject_ids)
                    ),
                ]
            ),
            locale=locale_of(state),
        ),
        images=case_image_inputs(state, ctx),
        response_model=CandidateProposal,
        # Both identifier spaces belong to this code, not to the model. An adapter that can
        # constrain output natively removes a whole class of unusable answer before it is
        # generated; one that cannot ignores these, and adjudication still has the last word.
        metadata={
            OUTPUT_SUBJECT_IDS: sorted(quality.usable_subject_ids),
            OUTPUT_EVIDENCE_IDS: sorted(
                {observation.observation_id for observation in evidence.observations}
                | {inference.inference_id for inference in evidence.inferences}
            ),
        },
        recorder=ctx.recorder,
        cache_prefix_chars=ctx.prompts.cacheable_prefix_chars(locale_of(state)),
        max_retries=ctx.config.provider_for(Role.PRIMARY).max_structured_retries,
    )

    logger = get_logger(NODE)
    usable = set(quality.usable_subject_ids)
    proposed_sets: list[CandidateSet] = []
    final_sets: list[CandidateSet] = []
    by_subject = {candidate_set.subject_id: candidate_set for candidate_set in proposal.sets}
    for subject in evidence.subjects:
        if subject.subject_id not in usable:
            continue
        candidate_set = by_subject.get(
            subject.subject_id, CandidateSet(subject_id=subject.subject_id)
        )
        proposed_sets.append(candidate_set)
        validation = validate_candidate_set_with_report(candidate_set, evidence, ctx.knowledge)
        if validation.dropped_evidence_ids or validation.rejected_taxa or validation.demoted_scores:
            logger.warning(
                "candidate_validation_filtered",
                extra={
                    "case_id": state.case.case_id,
                    "subject_id": candidate_set.subject_id,
                    "dropped_evidence_ids": list(validation.dropped_evidence_ids),
                    "malformed_evidence_ids": list(validation.malformed_evidence_ids),
                    "rejected_taxa": list(validation.rejected_taxa),
                    "demoted_scores": [
                        [taxon, proposed.value, effective.value]
                        for taxon, proposed, effective in validation.demoted_scores
                    ],
                },
            )
        final_sets.append(validation.candidate_set)

    return state.evolve(
        proposed_candidate_sets=tuple(proposed_sets),
        candidate_sets=tuple(final_sets),
    )
