"""The evidence hierarchy — section 2 and section 6 of the domain prompt, as code."""

from __future__ import annotations

import pytest

from evil_duck_dendro.knowledge.evidence_hierarchy import (
    BAND_DECISIVE,
    EvidenceTier,
    EvidenceTrust,
    bark_only,
    best_tier,
    confidence_band,
    confidence_ceiling,
    effective_tier,
    observation_trust,
    project_evidence,
    requires_attachment,
    resolution_ceiling,
    tier_of_feature,
    unattached_observations,
)
from evil_duck_dendro.schemas.evidence import (
    AttachmentStatus,
    EvidencePacket,
    Inference,
    Observation,
    ObservationSource,
    Reliability,
    Subject,
    Visibility,
)
from evil_duck_dendro.schemas.taxon import Confidence, Resolution
from tests.conftest import _attachment

DETACHABLE = ("leaf", "needles", "fruit", "cones", "branch", "bud", "seed", "nut", "acorn")


def _obs(
    observation_id,
    feature,
    *,
    visibility=Visibility.CLEAR,
    reliability=Reliability.MEDIUM,
    source=ObservationSource.IMAGE,
    attached=True,
    subject_id="log_1",
):
    return Observation(
        observation_id=observation_id,
        feature=feature,
        value="value",
        subject_id=subject_id,
        source=source,
        image_id="img-1" if source is ObservationSource.IMAGE else None,
        visibility=visibility,
        reliability=reliability,
        attachment=_attachment(feature, attached),
    )


def _packet(*observations):
    return EvidencePacket(subjects=(Subject(subject_id="log_1"),), observations=observations)


class TestOrdering:
    def test_the_hierarchy_follows_the_domain_prompt(self):
        """Fruit beats foliage beats arrangement beats cut beats bark beats form beats site."""
        assert (
            EvidenceTier.FRUIT_SEED
            > EvidenceTier.FOLIAGE
            > EvidenceTier.LEAF_ARRANGEMENT
            > EvidenceTier.WOOD_CUT
            > EvidenceTier.BARK
            > EvidenceTier.SILHOUETTE
            > EvidenceTier.CONTEXT
        )

    @pytest.mark.parametrize(
        ("feature", "tier"),
        [
            ("acorn.presence", EvidenceTier.FRUIT_SEED),
            ("cones.scale_shape", EvidenceTier.FRUIT_SEED),
            ("leaf.shape", EvidenceTier.FOLIAGE),
            ("needles.fascicles", EvidenceTier.FOLIAGE),
            ("leaf.arrangement", EvidenceTier.LEAF_ARRANGEMENT),
            ("wood.tone", EvidenceTier.WOOD_CUT),
            ("pores.arrangement", EvidenceTier.WOOD_CUT),
            ("bark.texture", EvidenceTier.BARK),
            ("trunk.form", EvidenceTier.SILHOUETTE),
            ("context.site", EvidenceTier.CONTEXT),
        ],
    )
    def test_features_map_to_their_tier(self, feature, tier):
        assert tier_of_feature(feature) is tier

    def test_longest_prefix_wins(self):
        """`leaf.arrangement` is weaker than the leaf itself, and must not inherit its tier."""
        assert tier_of_feature("leaf.shape") is EvidenceTier.FOLIAGE
        assert tier_of_feature("leaf.arrangement") is EvidenceTier.LEAF_ARRANGEMENT

    def test_unknown_families_are_context_not_a_crash(self):
        assert tier_of_feature("mycology.spores") is EvidenceTier.CONTEXT


class TestCeilings:
    def test_bark_alone_cannot_reach_beyond_low_confidence(self):
        """FAILURE 8 — the single most common way this kind of system embarrasses itself."""
        assert confidence_ceiling(EvidenceTier.BARK) is Confidence.LOW

    def test_bark_alone_can_still_reach_genus(self):
        """Capped in confidence, not silenced: an oak candidate from bark stays a candidate."""
        assert resolution_ceiling(EvidenceTier.BARK) is Resolution.GENUS

    def test_only_fruit_unlocks_species(self):
        assert resolution_ceiling(EvidenceTier.FRUIT_SEED) is Resolution.SPECIES
        assert resolution_ceiling(EvidenceTier.FOLIAGE) is Resolution.SPECIES_GROUP

    def test_silhouette_and_context_cap_at_family(self):
        assert resolution_ceiling(EvidenceTier.SILHOUETTE) is Resolution.FAMILY
        assert resolution_ceiling(EvidenceTier.CONTEXT) is Resolution.FAMILY

    def test_the_top_band_is_reserved_for_a_fruit_in_frame(self):
        assert confidence_band(Confidence.HIGH, EvidenceTier.FRUIT_SEED) == BAND_DECISIVE
        assert confidence_band(Confidence.HIGH, EvidenceTier.FOLIAGE) != BAND_DECISIVE

    def test_bands_are_ranges_never_point_values(self):
        for tier in EvidenceTier:
            for confidence in Confidence:
                assert "–" in confidence_band(confidence, tier)


class TestAttachment:
    @pytest.mark.parametrize(
        "feature", ["leaf.shape", "fruit.type", "cones.size", "needles.fascicles"]
    )
    def test_detachable_families_require_attachment(self, feature):
        assert requires_attachment(feature)

    @pytest.mark.parametrize("feature", ["bark.texture", "wood.tone", "trunk.form", "context.site"])
    def test_fixed_features_do_not(self, feature):
        assert not requires_attachment(feature)

    def test_unattached_foliage_demotes_to_context(self):
        """FAILURE 6 — a leaf that may belong to the neighbour decides nothing."""
        assert effective_tier(_obs("o1", "leaf.shape", attached=False)) is EvidenceTier.CONTEXT
        assert effective_tier(_obs("o1", "leaf.shape", attached=True)) is EvidenceTier.FOLIAGE

    def test_unresolvable_observations_count_for_nothing(self):
        hidden = _obs("o1", "fruit.type", visibility=Visibility.NOT_VISIBLE)
        assert effective_tier(hidden) is EvidenceTier.CONTEXT

    def test_unattached_evidence_is_listed_for_the_report(self):
        packet = _packet(
            _obs("o1", "bark.texture"),
            _obs("o2", "leaf.shape", attached=False),
            _obs("o3", "fruit.type", attached=True),
        )
        assert [o.observation_id for o in unattached_observations(packet, "log_1")] == ["o2"]


class TestTrustProjection:
    @pytest.mark.parametrize(
        ("source", "visibility", "reliability", "attached", "trust", "tier"),
        [
            (
                ObservationSource.IMAGE,
                Visibility.CLEAR,
                Reliability.MEDIUM,
                True,
                EvidenceTrust.FULL_POSITIVE,
                EvidenceTier.FOLIAGE,
            ),
            (
                ObservationSource.IMAGE,
                Visibility.CLEAR,
                Reliability.HIGH,
                True,
                EvidenceTrust.FULL_POSITIVE,
                EvidenceTier.FOLIAGE,
            ),
            (
                ObservationSource.IMAGE,
                Visibility.PARTIAL,
                Reliability.HIGH,
                True,
                EvidenceTrust.CAPPED_POSITIVE,
                EvidenceTier.BARK,
            ),
            (
                ObservationSource.IMAGE,
                Visibility.CLEAR,
                Reliability.LOW,
                True,
                EvidenceTrust.CAPPED_POSITIVE,
                EvidenceTier.BARK,
            ),
            (
                ObservationSource.IMAGE,
                Visibility.OBSCURED,
                Reliability.HIGH,
                True,
                EvidenceTrust.CONTEXT_ONLY,
                EvidenceTier.CONTEXT,
            ),
            (
                ObservationSource.IMAGE,
                Visibility.NOT_VISIBLE,
                Reliability.HIGH,
                True,
                EvidenceTrust.CONTEXT_ONLY,
                EvidenceTier.CONTEXT,
            ),
            (
                ObservationSource.USER,
                Visibility.CLEAR,
                Reliability.HIGH,
                True,
                EvidenceTrust.CONTEXT_ONLY,
                EvidenceTier.CONTEXT,
            ),
            (
                ObservationSource.METADATA,
                Visibility.CLEAR,
                Reliability.HIGH,
                True,
                EvidenceTrust.CONTEXT_ONLY,
                EvidenceTier.CONTEXT,
            ),
            (
                ObservationSource.EXTERNAL_CONTEXT,
                Visibility.CLEAR,
                Reliability.HIGH,
                True,
                EvidenceTrust.CONTEXT_ONLY,
                EvidenceTier.CONTEXT,
            ),
            (
                ObservationSource.IMAGE,
                Visibility.CLEAR,
                Reliability.HIGH,
                False,
                EvidenceTrust.CONTEXT_ONLY,
                EvidenceTier.CONTEXT,
            ),
        ],
    )
    def test_source_visibility_reliability_and_attachment_define_trust(
        self, source, visibility, reliability, attached, trust, tier
    ):
        observation = _obs(
            "o1",
            "leaf.shape",
            source=source,
            visibility=visibility,
            reliability=reliability,
            attached=attached,
        )
        assert observation_trust(observation) is trust
        assert effective_tier(observation) is tier

    def test_confirmed_detached_evidence_is_context_only(self):
        observation = _obs("o1", "fruit.type").model_copy(
            update={"attachment": AttachmentStatus.CONFIRMED_DETACHED}
        )
        assert observation_trust(observation) is EvidenceTrust.CONTEXT_ONLY

    def test_inference_inherits_the_weakest_source_trust(self):
        packet = EvidencePacket(
            subjects=(Subject(subject_id="log_1"),),
            observations=(
                _obs("o1", "leaf.shape", reliability=Reliability.HIGH),
                _obs("o2", "bark.texture", reliability=Reliability.LOW),
            ),
            inferences=(
                Inference(inference_id="i1", claim="compatible", derived_from=("o1", "o2")),
            ),
        )
        projection = project_evidence(packet, "i1", "log_1")
        assert projection.trust is EvidenceTrust.CAPPED_POSITIVE
        assert projection.tier is EvidenceTier.BARK
        assert tuple(o.observation_id for o in projection.source_observations) == ("o1", "o2")

    def test_inference_inherits_the_weakest_source_tier(self):
        packet = EvidencePacket(
            subjects=(Subject(subject_id="log_1"),),
            observations=(
                _obs("o1", "fruit.type"),
                _obs("o2", "trunk.form"),
            ),
            inferences=(
                Inference(inference_id="i1", claim="compatible", derived_from=("o1", "o2")),
            ),
        )
        projection = project_evidence(packet, "i1", "log_1")
        assert projection.trust is EvidenceTrust.FULL_POSITIVE
        assert projection.tier is EvidenceTier.SILHOUETTE

    def test_one_contextual_source_makes_the_whole_inference_contextual(self):
        packet = EvidencePacket(
            subjects=(Subject(subject_id="log_1"),),
            observations=(
                _obs("o1", "leaf.shape"),
                _obs("o2", "bark.texture", source=ObservationSource.USER),
            ),
            inferences=(
                Inference(inference_id="i1", claim="compatible", derived_from=("o1", "o2")),
            ),
        )
        assert project_evidence(packet, "i1", "log_1").trust is EvidenceTrust.CONTEXT_ONLY

    def test_cross_subject_inference_fails_closed(self):
        packet = EvidencePacket(
            subjects=(Subject(subject_id="log_1"), Subject(subject_id="log_2")),
            observations=(
                _obs("o1", "bark.texture"),
                _obs("o2", "bark.texture", subject_id="log_2"),
            ),
            inferences=(
                Inference(inference_id="i1", claim="compatible", derived_from=("o1", "o2")),
            ),
        )
        projection = project_evidence(packet, "i1", "log_1")
        assert not projection.source_observations
        assert projection.trust is EvidenceTrust.CONTEXT_ONLY


class TestSubjectLevel:
    def test_best_tier_is_the_strongest_available(self):
        packet = _packet(_obs("o1", "bark.texture"), _obs("o2", "leaf.shape"))
        assert best_tier(packet, "log_1") is EvidenceTier.FOLIAGE

    def test_bark_only_is_detected(self):
        assert bark_only(_packet(_obs("o1", "bark.texture"), _obs("o2", "trunk.form")), "log_1")

    def test_foliage_is_not_bark_only(self):
        assert not bark_only(_packet(_obs("o1", "bark.texture"), _obs("o2", "leaf.shape")), "log_1")

    def test_a_subject_with_no_evidence_is_context(self):
        assert best_tier(EvidencePacket(), "nobody") is EvidenceTier.CONTEXT

    def test_tiers_are_scoped_per_subject(self):
        packet = EvidencePacket(
            subjects=(Subject(subject_id="log_1"), Subject(subject_id="log_2")),
            observations=(
                _obs("o1", "leaf.shape"),
                Observation(
                    observation_id="o2",
                    feature="bark.texture",
                    value="rough",
                    subject_id="log_2",
                    source=ObservationSource.IMAGE,
                    image_id="img-1",
                ),
            ),
        )
        assert best_tier(packet, "log_1") is EvidenceTier.FOLIAGE
        assert best_tier(packet, "log_2") is EvidenceTier.BARK
