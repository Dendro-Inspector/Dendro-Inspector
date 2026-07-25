"""Candidate generator.

The model proposes ranked hypotheses; this node then enforces two invariants in code,
because both are cheap to check and expensive to get wrong:

* **no evidence leakage between subjects** — a candidate for ``foreground_log_1`` may only
  cite evidence belonging to ``foreground_log_1``. Foreign ids are stripped and recorded;
* **missing decisive features come from the cards**, not from the model's recollection of
  what it would need.
"""

from __future__ import annotations

from evil_duck_dendro.config import Role
from evil_duck_dendro.graph.executor import NodeContext
from evil_duck_dendro.graph.state import GraphState
from evil_duck_dendro.knowledge.taxon_cards import missing_decisive_features
from evil_duck_dendro.nodes._support import (
    case_context,
    evidence_context,
    image_inputs,
    knowledge_context,
    locale_of,
)
from evil_duck_dendro.observability.logging import get_logger
from evil_duck_dendro.providers.base import request_structured
from evil_duck_dendro.schemas.candidates import Candidate, CandidateProposal, CandidateSet
from evil_duck_dendro.schemas.evidence import EvidencePacket

NODE = "candidate_generator"


def allowed_evidence_ids(evidence: EvidencePacket, subject_id: str) -> frozenset[str]:
    """Evidence ids a candidate for ``subject_id`` may legitimately cite."""
    observation_ids = {o.observation_id for o in evidence.observations_for(subject_id)}
    inference_ids = {
        inference.inference_id
        for inference in evidence.inferences
        if set(inference.derived_from) <= observation_ids and inference.derived_from
    }
    return frozenset(observation_ids | inference_ids)


def strip_foreign_evidence(
    candidate_set: CandidateSet,
    evidence: EvidencePacket,
) -> tuple[CandidateSet, tuple[str, ...]]:
    """Remove evidence ids that do not belong to this subject. Returns what was stripped."""
    allowed = allowed_evidence_ids(evidence, candidate_set.subject_id)
    stripped: list[str] = []
    cleaned: list[Candidate] = []

    for candidate in candidate_set.ordered:
        supporting = tuple(i for i in candidate.supporting_evidence_ids if i in allowed)
        contradicting = tuple(i for i in candidate.contradicting_evidence_ids if i in allowed)
        stripped.extend(
            i
            for i in (*candidate.supporting_evidence_ids, *candidate.contradicting_evidence_ids)
            if i not in allowed
        )
        cleaned.append(
            candidate.model_copy(
                update={
                    "supporting_evidence_ids": supporting,
                    "contradicting_evidence_ids": contradicting,
                }
            )
        )
    return candidate_set.model_copy(update={"candidates": tuple(cleaned)}), tuple(stripped)


def enrich_missing_features(
    candidate_set: CandidateSet,
    evidence: EvidencePacket,
    ctx: NodeContext,
) -> CandidateSet:
    """Fill ``missing_decisive_features`` from the taxon card, when the project has one."""
    enriched: list[Candidate] = []
    for candidate in candidate_set.ordered:
        card = ctx.knowledge.try_taxon(candidate.taxon)
        if card is None:
            enriched.append(candidate)
            continue
        missing = missing_decisive_features(card, evidence, candidate_set.subject_id)
        merged = tuple(dict.fromkeys((*candidate.missing_decisive_features, *missing)))
        enriched.append(candidate.model_copy(update={"missing_decisive_features": merged}))
    return candidate_set.model_copy(update={"candidates": tuple(enriched)})


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
                    knowledge_context(ctx, ctx.knowledge.available_taxon_ids()),
                ]
            ),
            locale=locale_of(state),
        ),
        images=image_inputs(state.case),
        response_model=CandidateProposal,
        recorder=ctx.recorder,
        max_retries=ctx.config.provider_for(Role.PRIMARY).max_structured_retries,
    )

    logger = get_logger(NODE)
    usable = set(quality.usable_subject_ids)
    final_sets: list[CandidateSet] = []
    for candidate_set in proposal.sets:
        if candidate_set.subject_id not in usable:
            continue
        cleaned, stripped = strip_foreign_evidence(candidate_set, evidence)
        if stripped:
            logger.warning(
                "evidence_leak_stripped",
                extra={
                    "case_id": state.case.case_id,
                    "subject_id": candidate_set.subject_id,
                    "stripped_ids": list(stripped),
                },
            )
        final_sets.append(enrich_missing_features(cleaned, evidence, ctx))

    return state.evolve(candidate_sets=tuple(final_sets))
