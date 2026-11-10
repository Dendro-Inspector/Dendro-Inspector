"""Candidate generator.

The model proposes ranked hypotheses; the shared deterministic admission boundary then
keeps only known taxa with exact, same-subject, trusted card support. It also derives missing
decisive features from the cards rather than the model's recollection of what it would need.

A proposed taxon with no card but with trusted evidence is retained beside the admitted set
as an open-world candidate, and checked against any configured reference source. It is never
admitted; see :class:`~dendro_inspector.schemas.candidates.OpenWorldCandidate`.
"""

from __future__ import annotations

import json
from dataclasses import asdict

from dendro_inspector.config import Role
from dendro_inspector.graph.executor import NodeContext
from dendro_inspector.graph.state import GraphState
from dendro_inspector.knowledge.candidate_validation import (
    cards_in_play,
    validate_candidate_set_with_report,
)
from dendro_inspector.knowledge.references import verify_open_world
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
from dendro_inspector.schemas.candidates import (
    CandidateProposal,
    CandidateSet,
    OpenWorldCandidate,
)
from dendro_inspector.schemas.evidence import EvidencePacket

NODE = "candidate_generator"

#: The heading the node prompt's open-world rule is keyed on. A contract test asserts the
#: prompt names it, so the rule and the section that switches it on cannot drift apart.
OPEN_WORLD_SECTION = "Open-world discovery"


def open_world_context(
    evidence: EvidencePacket, ctx: NodeContext, subject_ids: tuple[str, ...]
) -> str:
    """The section that switches on the prompt's rule for taxa with no card. Empty when off.

    Closed-world runs never carry it, so the rule stays inert there. Lists the subjects no
    card could be opened for: the model has nothing to rank for those, and without being
    told so it tends to return an empty list rather than the name the evidence suggests.
    """
    if not ctx.config.graph.open_world_discovery:
        return ""
    payload = {
        "enabled": True,
        "subjects_without_cards": [
            subject_id
            for subject_id in subject_ids
            if not cards_in_play(evidence, ctx.knowledge, (subject_id,))
        ],
    }
    body = json.dumps(payload, indent=2)
    return f"## {OPEN_WORLD_SECTION} (project data)\n\n```json\n{body}\n```"


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
                section
                for section in (
                    case_context(state.case),
                    evidence_context(evidence),
                    knowledge_context(
                        ctx, cards_in_play(evidence, ctx.knowledge, quality.usable_subject_ids)
                    ),
                    open_world_context(evidence, ctx, quality.usable_subject_ids),
                )
                if section
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
    open_world: list[OpenWorldCandidate] = []
    by_subject = {candidate_set.subject_id: candidate_set for candidate_set in proposal.sets}
    for subject in evidence.subjects:
        if subject.subject_id not in usable:
            continue
        candidate_set = by_subject.get(
            subject.subject_id, CandidateSet(subject_id=subject.subject_id)
        )
        proposed_sets.append(candidate_set)
        validation = validate_candidate_set_with_report(candidate_set, evidence, ctx.knowledge)
        ctx.recorder.record_candidate_validation(candidate_set, validation)
        if validation.dropped_evidence_ids or validation.rejected_taxa or validation.demoted_scores:
            logger.warning(
                "candidate_validation_filtered",
                extra={
                    "case_id": state.case.case_id,
                    "subject_id": candidate_set.subject_id,
                    "dropped_evidence_ids": list(validation.dropped_evidence_ids),
                    "dropped_support_details": [
                        asdict(detail) for detail in validation.dropped_support_details
                    ],
                    "malformed_evidence_ids": list(validation.malformed_evidence_ids),
                    "rejected_taxa": list(validation.rejected_taxa),
                    "open_world_taxa": [candidate.taxon for candidate in validation.open_world],
                    "demoted_scores": [
                        [taxon, proposed.value, effective.value]
                        for taxon, proposed, effective in validation.demoted_scores
                    ],
                },
            )
        final_sets.append(validation.candidate_set)
        open_world.extend(validation.open_world)

    # Closed-world runs never see them: not in state, not verified, not read by any decision
    # rule. That is the flag's whole contract — an uncovered proposal changes nothing unless
    # open-world discovery was asked for — and an integration test holds it over the corpus.
    if not ctx.config.graph.open_world_discovery:
        open_world.clear()
    return state.evolve(
        proposed_candidate_sets=tuple(proposed_sets),
        candidate_sets=tuple(final_sets),
        open_world_candidates=await verify_open_world(
            tuple(open_world), evidence, ctx.knowledge.vocabulary(), ctx.references
        ),
    )
