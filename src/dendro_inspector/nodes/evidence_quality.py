"""Evidence quality gate.

Fully deterministic. The decision "can this photograph carry any taxonomic claim at all?"
is too important to delegate to the same model that just produced the evidence — asking it
would be asking a witness to rule on its own admissibility.

Since the domain prompt's evidence hierarchy landed, this node also records *how strong* a
claim is available, not merely whether one is. The tier it records is what caps resolution
and confidence downstream.
"""

from __future__ import annotations

from collections.abc import Mapping

from dendro_inspector.graph.executor import NodeContext
from dendro_inspector.graph.state import EvidenceQualityReport, GraphState
from dendro_inspector.knowledge.comparison_cards import (
    INSUFFICIENT_ALONE,
    relies_only_on_insufficient_features,
)
from dendro_inspector.knowledge.evidence_hierarchy import (
    EvidenceTier,
    best_tier,
    contextual_observations_for,
    is_colour_feature,
    positive_observations_for,
    unattached_observations,
)
from dendro_inspector.knowledge.taxon_cards import (
    card_value_vocabulary,
    unmatchable_observations,
)
from dendro_inspector.observability.logging import get_logger
from dendro_inspector.schemas.evidence import EvidencePacket, Observation

NODE = "evidence_quality"

#: A subject whose visible evidence is at least half weak features is flagged as
#: colour-dependent. Flagged, not rejected: the reviewers decide what it costs.
COLOUR_DEPENDENCE_RATIO = 0.5


def classify_vocabulary_diagnostics(
    observations: tuple[Observation, ...],
) -> tuple[tuple[Observation, ...], tuple[Observation, ...]]:
    """Split unmatchable evidence into known-weak signals and possible card gaps.

    This is diagnostic classification only. It does not admit or reject evidence. Colour
    and the project's explicit insufficient-alone features are weak by existing policy;
    every other trusted, resolvable unmatchable observation remains a possible knowledge
    coverage gap until a card author reviews it.
    """
    weak: list[Observation] = []
    possible_gaps: list[Observation] = []
    for observation in observations:
        target = (
            weak
            if observation.feature in INSUFFICIENT_ALONE or is_colour_feature(observation.feature)
            else possible_gaps
        )
        target.append(observation)
    return tuple(weak), tuple(possible_gaps)


def _colour_dependent(evidence: EvidencePacket, subject_id: str) -> bool:
    observations = contextual_observations_for(evidence, subject_id)
    if not observations:
        return False
    weak = sum(
        1
        for observation in observations
        if observation.feature in INSUFFICIENT_ALONE or is_colour_feature(observation.feature)
    )
    return weak / len(observations) >= COLOUR_DEPENDENCE_RATIO


def assess(
    evidence: EvidencePacket,
    *,
    min_observations: int,
    require_non_colour: bool,
    vocabulary: Mapping[str, frozenset[str]] | None = None,
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
        visible = positive_observations_for(evidence, subject_id)
        tier = best_tier(evidence, subject_id)
        tiers[subject_id] = int(tier)
        unattached.extend(o.observation_id for o in unattached_observations(evidence, subject_id))

        if _colour_dependent(evidence, subject_id):
            colour_dependence = True

        subject_usable = True
        if len(visible) < min_observations:
            reasons.append("too_few_resolvable_observations")
            subject_usable = False
        if require_non_colour and relies_only_on_insufficient_features(evidence, subject_id):
            reasons.append("only_insufficient_features_visible")
            subject_usable = False
        # Nothing above context survived. Either everything was unresolvable, or the only
        # foliage in frame could not be shown to grow on this trunk — which the domain
        # prompt treats as no foliage at all.
        if tier <= EvidenceTier.CONTEXT:
            reasons.append("no_evidence_above_context")
            subject_usable = False
        if subject_usable:
            usable.append(subject_id)

    if not usable and "no_usable_subject" not in reasons:
        reasons.append("no_usable_subject")

    unmatchable = (
        tuple(o.observation_id for o in unmatchable_observations(evidence, vocabulary))
        if vocabulary is not None
        else ()
    )

    return EvidenceQualityReport(
        sufficient=bool(usable),
        usable_subject_ids=tuple(usable),
        insufficient_reasons=tuple(dict.fromkeys(reasons)),
        colour_dependence_detected=colour_dependence,
        best_tier_by_subject=tiers,
        unattached_evidence_ids=tuple(unattached),
        unmatchable_evidence_ids=unmatchable,
    )


async def run(state: GraphState, ctx: NodeContext) -> GraphState:
    evidence = state.evidence
    if evidence is None:
        return state.evolve(
            quality=EvidenceQualityReport(sufficient=False, insufficient_reasons=("no_evidence",))
        )
    vocabulary = card_value_vocabulary(ctx.knowledge.taxa(ctx.knowledge.available_taxon_ids()))
    report = assess(
        evidence,
        min_observations=ctx.config.graph.min_observations_for_candidates,
        require_non_colour=ctx.config.graph.require_non_colour_evidence,
        vocabulary=vocabulary,
    )
    if report.unmatchable_evidence_ids:
        # Logged here rather than at the admission boundary because by then the reason is
        # gone: the candidate is simply rejected, and "the model saw nothing useful" and
        # "the cards describe nothing the model saw" look identical in the output.
        by_id = {o.observation_id: o for o in evidence.observations}
        unmatchable = tuple(
            by_id[observation_id]
            for observation_id in report.unmatchable_evidence_ids
            if observation_id in by_id
        )
        weak, possible_gaps = classify_vocabulary_diagnostics(unmatchable)
        get_logger(NODE).warning(
            "evidence_outside_card_vocabulary",
            extra={
                "case_id": state.case.case_id,
                "unmatchable": len(report.unmatchable_evidence_ids),
                "observations": len(evidence.observations),
                "intentionally_weak": len(weak),
                "intentionally_weak_evidence_ids": sorted(
                    observation.observation_id for observation in weak
                ),
                "potential_coverage_gaps": len(possible_gaps),
                "potential_coverage_gap_evidence_ids": sorted(
                    observation.observation_id for observation in possible_gaps
                ),
                "potential_gap_features_absent_from_all_cards": sorted(
                    {
                        observation.feature
                        for observation in possible_gaps
                        if observation.feature not in vocabulary
                    }
                ),
                "potential_gap_features_with_unknown_values": sorted(
                    {
                        observation.feature
                        for observation in possible_gaps
                        if observation.feature in vocabulary
                    }
                ),
            },
        )
    return state.evolve(quality=report)
