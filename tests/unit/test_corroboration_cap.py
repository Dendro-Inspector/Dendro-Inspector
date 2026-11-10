"""High confidence needs corroboration the engine can count, not a reviewer's restraint.

golden-060 (post-deploy Opus batch, 2026-11-10): a sapling's Quercus candidate was admitted
on one observation, `leaf.shape = simple_lobed`, read clearly at high reliability and
attached. Its other three readings were refused (`feature_not_declared`). The deterministic
ledger went `seed high -> tier_cap (not applied) -> requirement_cap (not applied)`; only a
`reviewer_recommendation` took it to `low`. Nothing in the engine itself stopped one lobed
leaf — FAILURE 5's exact example — from becoming `identified / high`.

Section 6 is the rule: 85-94 is "leaves and bark visible", and "only part of the features
visible" is 70-84. These tests run with no reviewer synthesis at all, so the cap they check
is the engine's own.
"""

from __future__ import annotations

from dendro_inspector.graph.state import GraphState
from dendro_inspector.knowledge.candidate_validation import validate_candidate_set
from dendro_inspector.nodes.final_decision import decide_subject
from dendro_inspector.schemas.candidates import Candidate, CandidateSet, SupportStrength
from dendro_inspector.schemas.evidence import (
    AttachmentStatus,
    EvidencePacket,
    Inference,
    Observation,
    ObservationSource,
    Reliability,
    Subject,
    SubjectKind,
    Visibility,
)
from dendro_inspector.schemas.taxon import Confidence, Resolution

SUBJECT = "foreground_sapling_1"


def _observation(observation_id: str, feature: str, value: str) -> Observation:
    detachable = feature.split(".")[0] in {"leaf", "acorn"}
    return Observation(
        observation_id=observation_id,
        feature=feature,
        value=value,
        subject_id=SUBJECT,
        source=ObservationSource.IMAGE,
        image_id="img-1",
        visibility=Visibility.CLEAR,
        reliability=Reliability.HIGH,
        attachment=AttachmentStatus.CONFIRMED_ATTACHED if detachable else None,
    )


def _decide(simple_case, node_context, knowledge, observations, support, inferences=()):
    evidence = EvidencePacket(
        subjects=(Subject(subject_id=SUBJECT, kind=SubjectKind.STANDING_TREE),),
        observations=tuple(observations),
        inferences=tuple(inferences),
    )
    proposed = CandidateSet(
        subject_id=SUBJECT,
        candidates=(
            Candidate(
                taxon="quercus",
                resolution=Resolution.GENUS,
                supporting_evidence_ids=tuple(support),
                score=SupportStrength.STRONG,
                rank=1,
            ),
        ),
    )
    validated = validate_candidate_set(proposed, evidence, knowledge)
    state = GraphState(case=simple_case, evidence=evidence, candidate_sets=(validated,))
    return decide_subject(state, node_context, validated)


def test_one_clearly_read_leaf_shape_caps_at_medium(simple_case, node_context, knowledge):
    """golden-060's sapling, without the reviewer that happened to rescue it."""
    decision = _decide(
        simple_case,
        node_context,
        knowledge,
        [_observation("obs-6", "leaf.shape", "simple_lobed")],
        ["obs-6"],
    )

    assert decision.selected_taxon == "quercus"
    assert decision.confidence is Confidence.MEDIUM


def test_the_same_feature_read_twice_is_still_one_feature(simple_case, node_context, knowledge):
    """Repetition, and an inference restating it, are not independent evidence."""
    observations = [
        _observation("obs-6", "leaf.shape", "simple_lobed"),
        _observation("obs-7", "leaf.shape", "simple_lobed"),
    ]
    inference = Inference(
        inference_id="inf-1",
        claim="morphology_is_compatible_with_quercus",
        derived_from=("obs-6", "obs-7"),
    )
    decision = _decide(
        simple_case,
        node_context,
        knowledge,
        observations,
        ["obs-6", "obs-7", "inf-1"],
        inferences=[inference],
    )

    assert decision.confidence is Confidence.MEDIUM


def test_two_independent_features_may_reach_high(simple_case, node_context, knowledge):
    decision = _decide(
        simple_case,
        node_context,
        knowledge,
        [
            _observation("obs-6", "leaf.shape", "simple_lobed"),
            _observation("obs-1", "bark.texture", "deep_longitudinal_fissures"),
        ],
        ["obs-6", "obs-1"],
    )

    assert decision.confidence is Confidence.HIGH


def test_an_acorn_closes_the_question_on_its_own(simple_case, node_context, knowledge):
    """Section 6 / 17: fruit is the declared exception to corroboration."""
    decision = _decide(
        simple_case,
        node_context,
        knowledge,
        [_observation("obs-2", "acorn.presence", "present")],
        ["obs-2"],
    )

    assert decision.confidence is Confidence.HIGH
