"""Evidence quality gate.

Fully deterministic. The decision "can this photograph carry any taxonomic claim at all?"
is too important to delegate to the same model that just produced the evidence — asking it
would be asking a witness to rule on its own admissibility.

Since the domain prompt's evidence hierarchy landed, this node also records *how strong* a
claim is available, not merely whether one is. The tier it records is what caps resolution
and confidence downstream.
"""

from __future__ import annotations

from evil_duck_dendro.graph.executor import NodeContext
from evil_duck_dendro.graph.state import EvidenceQualityReport, GraphState
from evil_duck_dendro.knowledge.comparison_cards import (
    INSUFFICIENT_ALONE,
    relies_only_on_insufficient_features,
)
from evil_duck_dendro.knowledge.evidence_hierarchy import (
    EvidenceTier,
    best_tier,
    unattached_observations,
)
from evil_duck_dendro.schemas.evidence import EvidencePacket

NODE = "evidence_quality"

#: A subject whose visible evidence is at least half weak features is flagged as
#: colour-dependent. Flagged, not rejected: the reviewers decide what it costs.
COLOUR_DEPENDENCE_RATIO = 0.5


def _colour_dependent(evidence: EvidencePacket, subject_id: str) -> bool:
    observations = evidence.visible_observations_for(subject_id)
    if not observations:
        return False
    weak = sum(1 for o in observations if o.feature in INSUFFICIENT_ALONE)
    return weak / len(observations) >= COLOUR_DEPENDENCE_RATIO


def assess(
    evidence: EvidencePacket,
    *,
    min_observations: int,
    require_non_colour: bool,
) -> EvidenceQualityReport:
    """Pure quality assessment over an evidence packet."""
    reasons: list[str] = []

    if not evidence.subjects:
        return EvidenceQualityReport(
            sufficient=False, insufficient_reasons=("no_subject_identified",)
        )

    usable: list[str] = []
    colour_dependence = False
    tiers: dict[str, int] = {}
    unattached: list[str] = []

    for subject in evidence.subjects:
        subject_id = subject.subject_id
        visible = evidence.visible_observations_for(subject_id)
        tier = best_tier(evidence, subject_id)
        tiers[subject_id] = int(tier)
        unattached.extend(o.observation_id for o in unattached_observations(evidence, subject_id))

        if _colour_dependent(evidence, subject_id):
            colour_dependence = True

        if len(visible) < min_observations:
            reasons.append("too_few_resolvable_observations")
            continue
        if require_non_colour and relies_only_on_insufficient_features(evidence, subject_id):
            reasons.append("only_insufficient_features_visible")
            continue
        # Nothing above context survived. Either everything was unresolvable, or the only
        # foliage in frame could not be shown to grow on this trunk — which the domain
        # prompt treats as no foliage at all.
        if tier <= EvidenceTier.CONTEXT:
            reasons.append("no_evidence_above_context")
            continue
        usable.append(subject_id)

    if not usable and "no_usable_subject" not in reasons:
        reasons.append("no_usable_subject")

    return EvidenceQualityReport(
        sufficient=bool(usable),
        usable_subject_ids=tuple(usable),
        insufficient_reasons=tuple(dict.fromkeys(reasons)),
        colour_dependence_detected=colour_dependence,
        best_tier_by_subject=tiers,
        unattached_evidence_ids=tuple(unattached),
    )


async def run(state: GraphState, ctx: NodeContext) -> GraphState:
    evidence = state.evidence
    if evidence is None:
        return state.evolve(
            quality=EvidenceQualityReport(sufficient=False, insufficient_reasons=("no_evidence",))
        )
    report = assess(
        evidence,
        min_observations=ctx.config.graph.min_observations_for_candidates,
        require_non_colour=ctx.config.graph.require_non_colour_evidence,
    )
    return state.evolve(quality=report)
