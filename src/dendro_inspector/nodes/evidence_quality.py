"""Evidence quality gate.

Fully deterministic. The decision "can this photograph carry any taxonomic claim at all?"
is too important to delegate to the same model that just produced the evidence — asking it
would be asking a witness to rule on its own admissibility.

Since the domain prompt's evidence hierarchy landed, this node also records *how strong* a
claim is available, not merely whether one is. The tier it records is what caps resolution
and confidence downstream.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from dendro_inspector.graph.executor import NodeContext
from dendro_inspector.graph.state import EvidenceQualityReport, GraphState
from dendro_inspector.knowledge.candidate_validation import cards_in_play
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
from dendro_inspector.schemas.evidence import EvidencePacket, KnowledgeCoverage, Observation

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


def summarise_coverage(
    evidence: EvidencePacket,
    vocabulary: Mapping[str, frozenset[str]],
    unmatchable: tuple[Observation, ...],
) -> KnowledgeCoverage:
    """Classify what this packet observed that the cards cannot represent.

    One measurement with three readers — the trace, the reader-facing limitations and the
    structured log. Before this existed the classification was assembled inline while
    building log extras, so the only consumer of the most actionable diagnostic the graph
    produces was a warning line nothing downstream could see.

    ``unmatchable`` is passed in rather than recomputed because the caller needs the same
    tuple for :attr:`EvidenceQualityReport.unmatchable_evidence_ids`, and a second call
    would be a second place for the two to drift apart.
    """
    weak, possible_gaps = classify_vocabulary_diagnostics(unmatchable)
    return KnowledgeCoverage(
        observations_total=len(evidence.observations),
        intentionally_weak_evidence_ids=tuple(
            sorted(observation.observation_id for observation in weak)
        ),
        potential_gap_evidence_ids=tuple(
            sorted(observation.observation_id for observation in possible_gaps)
        ),
        features_absent_from_all_cards=tuple(
            sorted(
                {
                    observation.feature
                    for observation in possible_gaps
                    if observation.feature not in vocabulary
                }
            )
        ),
        features_with_unknown_values=tuple(
            sorted(
                {
                    observation.feature
                    for observation in possible_gaps
                    if observation.feature in vocabulary
                }
            )
        ),
    )


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
    cards_available: Callable[[str], bool] | None = None,
) -> EvidenceQualityReport:
    """Pure quality assessment over an evidence packet.

    ``cards_available`` answers, for one subject, whether the knowledge base holds any card
    the admission boundary could open on this evidence. Passed in as a predicate rather
    than resolved here so this function stays free of the knowledge base and testable
    without one. Omitting it disables the coverage-gap gate, which is what every caller
    that has no cards to consult should get.
    """
    reasons: list[str] = []

    if not evidence.subjects:
        return EvidenceQualityReport(
            sufficient=False, insufficient_reasons=("no_subject_identified",)
        )

    usable: list[str] = []
    colour_dependence = False
    tiers: dict[str, int] = {}
    unattached: list[str] = []
    coverage_gap_subjects: list[str] = []

    unmatchable_obs = (
        unmatchable_observations(evidence, vocabulary) if vocabulary is not None else ()
    )
    coverage = (
        summarise_coverage(evidence, vocabulary, unmatchable_obs)
        if vocabulary is not None
        else None
    )
    gap_owners = {
        observation.subject_id
        for observation in unmatchable_obs
        if coverage is not None
        and observation.observation_id in coverage.potential_gap_evidence_ids
    }

    for subject in evidence.subjects:
        subject_id = subject.subject_id
        visible = positive_observations_for(evidence, subject_id)
        tier = best_tier(evidence, subject_id)
        tiers[subject_id] = int(tier)
        unattached.extend(o.observation_id for o in unattached_observations(evidence, subject_id))

        if _colour_dependent(evidence, subject_id):
            colour_dependence = True

        subject_usable = True
        # The coverage-gap exit. Both halves are required. No card the boundary could open
        # means the candidate generator has nothing to rank and admission would reject
        # whatever it invented, so the call is provably wasted; the gap is what makes the
        # cause the cards rather than the photograph, and decides what the reader is told.
        if (
            cards_available is not None
            and not cards_available(subject_id)
            and subject_id in gap_owners
        ):
            reasons.append("knowledge_coverage_gap")
            coverage_gap_subjects.append(subject_id)
            subject_usable = False
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

    # The catch-all is skipped when the coverage gate already accounts for every subject.
    # "No subject carried usable evidence" blames the frame, and on a coverage gap the
    # frame was fine — that sentence is what sends someone back to re-shoot a photograph
    # that was never the problem.
    fully_explained_by_coverage = bool(coverage_gap_subjects) and len(coverage_gap_subjects) == len(
        evidence.subjects
    )
    if not usable and "no_usable_subject" not in reasons and not fully_explained_by_coverage:
        reasons.append("no_usable_subject")

    return EvidenceQualityReport(
        sufficient=bool(usable),
        usable_subject_ids=tuple(usable),
        insufficient_reasons=tuple(dict.fromkeys(reasons)),
        colour_dependence_detected=colour_dependence,
        best_tier_by_subject=tiers,
        unattached_evidence_ids=tuple(unattached),
        unmatchable_evidence_ids=tuple(o.observation_id for o in unmatchable_obs),
        coverage_gap_subject_ids=tuple(coverage_gap_subjects),
        knowledge_coverage=coverage,
    )


async def run(state: GraphState, ctx: NodeContext) -> GraphState:
    evidence = state.evidence
    if evidence is None:
        return state.evolve(
            quality=EvidenceQualityReport(sufficient=False, insufficient_reasons=("no_evidence",))
        )
    vocabulary = card_value_vocabulary(ctx.knowledge.taxa(ctx.knowledge.available_taxon_ids()))

    def cards_available(subject_id: str) -> bool:
        """Whether the admission boundary could open any card for this subject.

        The same function the candidate generator uses to choose which cards to show, so
        the gate cannot conclude "nothing to rank" while the generator would have found
        something to rank.
        """
        return bool(cards_in_play(evidence, ctx.knowledge, (subject_id,)))

    report = assess(
        evidence,
        min_observations=ctx.config.graph.min_observations_for_candidates,
        require_non_colour=ctx.config.graph.require_non_colour_evidence,
        vocabulary=vocabulary,
        cards_available=cards_available,
    )
    coverage = report.knowledge_coverage
    if coverage is not None:
        # Recorded before the log line, and unconditionally: a run whose evidence fits the
        # cards perfectly is itself a measurement, and a trace field that appears only on
        # bad runs cannot be aggregated across a suite.
        ctx.recorder.record_knowledge_coverage(coverage)
    if coverage is not None and coverage.unmatchable_total:
        # Logged here rather than at the admission boundary because by then the reason is
        # gone: the candidate is simply rejected, and "the model saw nothing useful" and
        # "the cards describe nothing the model saw" look identical in the output.
        get_logger(NODE).warning(
            "evidence_outside_card_vocabulary",
            extra={
                "case_id": state.case.case_id,
                "unmatchable": coverage.unmatchable_total,
                "observations": coverage.observations_total,
                "intentionally_weak": len(coverage.intentionally_weak_evidence_ids),
                "intentionally_weak_evidence_ids": list(coverage.intentionally_weak_evidence_ids),
                "potential_coverage_gaps": len(coverage.potential_gap_evidence_ids),
                "potential_coverage_gap_evidence_ids": list(coverage.potential_gap_evidence_ids),
                "potential_gap_features_absent_from_all_cards": list(
                    coverage.features_absent_from_all_cards
                ),
                "potential_gap_features_with_unknown_values": list(
                    coverage.features_with_unknown_values
                ),
            },
        )
    return state.evolve(quality=report)
