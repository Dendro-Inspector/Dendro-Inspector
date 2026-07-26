"""The quality gate decides whether any claim is possible at all."""

from __future__ import annotations

from evil_duck_dendro.graph.state import EvidenceQualityReport
from evil_duck_dendro.knowledge.evidence_hierarchy import EvidenceTier
from evil_duck_dendro.nodes.evidence_quality import assess as assess_quality
from evil_duck_dendro.schemas.evidence import (
    EvidencePacket,
    Observation,
    ObservationSource,
    Subject,
    Visibility,
)
from tests.conftest import _attachment

DETACHABLE = ("leaf", "needles", "fruit", "cones", "branch", "bud", "seed", "nut")


def _obs(
    observation_id: str,
    feature: str,
    *,
    visibility: Visibility = Visibility.CLEAR,
    attached: bool = True,
    source: ObservationSource = ObservationSource.IMAGE,
) -> Observation:
    return Observation(
        observation_id=observation_id,
        feature=feature,
        value="some_value",
        subject_id="log_1",
        source=source,
        image_id="img-1" if source is ObservationSource.IMAGE else None,
        visibility=visibility,
        attachment=_attachment(feature, attached),
    )


def _packet(*observations: Observation) -> EvidencePacket:
    return EvidencePacket(subjects=(Subject(subject_id="log_1"),), observations=observations)


def assess(packet: EvidencePacket) -> EvidenceQualityReport:
    """Assess with the project defaults, so each test states only what it varies."""
    return assess_quality(packet, min_observations=2, require_non_colour=True)


def test_no_subject_is_insufficient():
    report = assess(EvidencePacket())
    assert not report.sufficient
    assert "no_subject_identified" in report.insufficient_reasons


def test_too_few_resolvable_observations_is_insufficient():
    report = assess(_packet(_obs("obs-1", "bark.flake_geometry")))
    assert not report.sufficient
    assert "too_few_resolvable_observations" in report.insufficient_reasons


def test_not_visible_observations_do_not_count_towards_sufficiency():
    """Two observations, but only one could actually be seen."""
    report = assess(
        _packet(
            _obs("obs-1", "bark.flake_geometry"),
            _obs("obs-2", "needles.fascicles", visibility=Visibility.NOT_VISIBLE),
        ),
    )
    assert not report.sufficient


def test_colour_only_evidence_is_insufficient():
    report = assess(
        _packet(_obs("obs-1", "bark.colour"), _obs("obs-2", "wood.colour")),
    )
    assert not report.sufficient
    assert "only_insufficient_features_visible" in report.insufficient_reasons


def test_contextual_structural_evidence_cannot_rescue_colour_only_image_support():
    report = assess(
        _packet(
            _obs("obs-1", "bark.colour"),
            _obs("obs-2", "wood.colour"),
            _obs("obs-3", "bark.flake_geometry", source=ObservationSource.USER),
        ),
    )
    assert not report.sufficient
    assert "only_insufficient_features_visible" in report.insufficient_reasons


def test_colour_dependence_is_flagged_but_does_not_block():
    """Flagged, not rejected: the reviewers decide what colour dependence costs."""
    report = assess(
        _packet(
            _obs("obs-1", "bark.colour"),
            _obs("obs-2", "wood.colour"),
            _obs("obs-3", "bark.flake_geometry"),
        ),
    )
    assert report.sufficient
    assert report.colour_dependence_detected


def test_structural_evidence_passes_cleanly():
    report = assess(
        _packet(_obs("obs-1", "bark.flake_geometry"), _obs("obs-2", "needles.fascicles")),
    )
    assert report.sufficient
    assert report.usable_subject_ids == ("log_1",)
    assert not report.colour_dependence_detected


def test_only_usable_subjects_are_listed():
    packet = EvidencePacket(
        subjects=(Subject(subject_id="log_1"), Subject(subject_id="log_2")),
        observations=(
            _obs("obs-1", "bark.flake_geometry"),
            _obs("obs-2", "needles.fascicles"),
            Observation(
                observation_id="obs-3",
                feature="bark.colour",
                value="grey",
                subject_id="log_2",
                source=ObservationSource.IMAGE,
                image_id="img-1",
            ),
        ),
    )
    report = assess(packet)
    assert report.sufficient
    assert report.usable_subject_ids == ("log_1",)


class TestEvidenceHierarchy:
    def test_the_best_available_tier_is_recorded_per_subject(self):
        report = assess(
            _packet(_obs("obs-1", "bark.flake_geometry"), _obs("obs-2", "needles.fascicles"))
        )
        assert report.tier_for("log_1") == int(EvidenceTier.FOLIAGE)

    def test_bark_only_evidence_records_the_bark_tier(self):
        report = assess(
            _packet(_obs("obs-1", "bark.flake_geometry"), _obs("obs-2", "bark.pattern"))
        )
        assert report.tier_for("log_1") == int(EvidenceTier.BARK)

    def test_unattached_foliage_does_not_raise_the_tier(self):
        """FAILURE 6 — a leaf that may belong to the neighbouring tree counts as context."""
        report = assess(
            _packet(
                _obs("obs-1", "bark.flake_geometry"),
                _obs("obs-2", "leaf.shape", attached=False),
            )
        )
        assert report.tier_for("log_1") == int(EvidenceTier.BARK)
        assert report.unattached_evidence_ids == ("obs-2",)

    def test_attached_foliage_does_raise_the_tier(self):
        report = assess(
            _packet(
                _obs("obs-1", "bark.flake_geometry"),
                _obs("obs-2", "leaf.shape", attached=True),
            )
        )
        assert report.tier_for("log_1") == int(EvidenceTier.FOLIAGE)
        assert report.unattached_evidence_ids == ()

    def test_fruit_outranks_foliage(self):
        report = assess(_packet(_obs("obs-1", "leaf.shape"), _obs("obs-2", "fruit.type")))
        assert report.tier_for("log_1") == int(EvidenceTier.FRUIT_SEED)

    def test_only_unattached_foliage_is_not_usable_at_all(self):
        report = assess(
            _packet(
                _obs("obs-1", "leaf.shape", attached=False),
                _obs("obs-2", "leaf.arrangement", attached=False),
            )
        )
        assert not report.sufficient
        assert "no_evidence_above_context" in report.insufficient_reasons
