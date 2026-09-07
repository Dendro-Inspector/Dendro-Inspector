"""The quality gate decides whether any claim is possible at all."""

from __future__ import annotations

from dendro_inspector.graph.state import EvidenceQualityReport
from dendro_inspector.knowledge.evidence_hierarchy import EvidenceTier
from dendro_inspector.nodes.evidence_quality import (
    assess as assess_quality,
)
from dendro_inspector.nodes.evidence_quality import (
    classify_vocabulary_diagnostics,
)
from dendro_inspector.schemas.evidence import (
    EvidencePacket,
    Observation,
    ObservationSource,
    Subject,
    SubjectKind,
    Visibility,
    WoodSurface,
)
from tests.conftest import _attachment, _wood_surface

DETACHABLE = ("leaf", "needles", "fruit", "cones", "branch", "bud", "seed", "nut")


def _obs(
    observation_id: str,
    feature: str,
    *,
    visibility: Visibility = Visibility.CLEAR,
    attached: bool = True,
    source: ObservationSource = ObservationSource.IMAGE,
    subject_id: str = "log_1",
    wood_surface: WoodSurface = WoodSurface.PREPARED_END_GRAIN,
) -> Observation:
    return Observation(
        observation_id=observation_id,
        feature=feature,
        value="some_value",
        subject_id=subject_id,
        source=source,
        image_id="img-1" if source is ObservationSource.IMAGE else None,
        visibility=visibility,
        attachment=_attachment(feature, attached),
        wood_surface=_wood_surface(feature, wood_surface),
    )


def _packet(
    *observations: Observation,
    subjects: tuple[Subject, ...] = (Subject(subject_id="log_1"),),
) -> EvidencePacket:
    return EvidencePacket(subjects=subjects, observations=observations)


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


def test_tone_suffix_is_classified_as_colour_dependence():
    report = assess(
        _packet(
            _obs("obs-1", "heartwood.tone"),
            _obs("obs-2", "lenticels.orientation"),
        )
    )
    assert report.sufficient
    assert report.colour_dependence_detected


def test_vocabulary_diagnostic_separates_weak_colour_from_possible_card_gap():
    colour = _obs("obs-colour", "bark.colour")
    structural = _obs("obs-structure", "bark.surface_cover")

    weak, possible_gaps = classify_vocabulary_diagnostics((colour, structural))

    assert tuple(observation.observation_id for observation in weak) == ("obs-colour",)
    assert tuple(observation.observation_id for observation in possible_gaps) == ("obs-structure",)


#: A vocabulary covering one of the two features the packets below observe. Written out
#: rather than loaded from the cards so the test states its own premise: these assertions
#: are about the classification, not about which taxa happen to ship in this build.
_VOCABULARY: dict[str, frozenset[str]] = {
    "needles.fascicles": frozenset({"some_value"}),
    "bark.texture": frozenset({"fine_scales"}),
}


def test_coverage_names_the_feature_no_card_declares():
    """The Case B signal: an observation whose feature path appears on no card at all."""
    report = assess_quality(
        _packet(
            _obs("obs-1", "needles.fascicles"),
            _obs("obs-2", "bark.flake_geometry"),
        ),
        min_observations=2,
        require_non_colour=True,
        vocabulary=_VOCABULARY,
    )

    coverage = report.knowledge_coverage
    assert coverage is not None
    assert coverage.has_potential_gap
    assert coverage.potential_gap_evidence_ids == ("obs-2",)
    assert coverage.features_absent_from_all_cards == ("bark.flake_geometry",)
    assert coverage.features_with_unknown_values == ()
    assert coverage.observations_total == 2


def test_coverage_distinguishes_an_unknown_value_from_an_unknown_feature():
    """A card knows the feature but not this value — a rule gap, not a missing card."""
    report = assess_quality(
        _packet(
            _obs("obs-1", "needles.fascicles"),
            _obs("obs-2", "bark.texture"),
        ),
        min_observations=2,
        require_non_colour=True,
        vocabulary=_VOCABULARY,
    )

    coverage = report.knowledge_coverage
    assert coverage is not None
    assert coverage.features_with_unknown_values == ("bark.texture",)
    assert coverage.features_absent_from_all_cards == ()


def test_coverage_is_recorded_even_when_nothing_is_missing():
    """A clean run still records a measurement, so a suite can use it as a denominator."""
    report = assess_quality(
        _packet(_obs("obs-1", "needles.fascicles"), _obs("obs-2", "needles.fascicles")),
        min_observations=2,
        require_non_colour=True,
        vocabulary=_VOCABULARY,
    )

    coverage = report.knowledge_coverage
    assert coverage is not None
    assert not coverage.has_potential_gap
    assert coverage.unmatchable_total == 0


def test_colour_is_never_reported_as_a_recoverable_coverage_gap():
    """Colour is unmatchable by policy. Counting it would inflate the gap with noise."""
    report = assess_quality(
        _packet(_obs("obs-1", "needles.fascicles"), _obs("obs-2", "bark.colour")),
        min_observations=1,
        require_non_colour=False,
        vocabulary=_VOCABULARY,
    )

    coverage = report.knowledge_coverage
    assert coverage is not None
    assert coverage.intentionally_weak_evidence_ids == ("obs-2",)
    assert not coverage.has_potential_gap


def test_coverage_split_partitions_the_unmatchable_ids():
    """The classification is a partition of the raw list, not a second measurement."""
    report = assess_quality(
        _packet(
            _obs("obs-1", "needles.fascicles"),
            _obs("obs-2", "bark.colour"),
            _obs("obs-3", "bark.flake_geometry"),
        ),
        min_observations=1,
        require_non_colour=False,
        vocabulary=_VOCABULARY,
    )

    coverage = report.knowledge_coverage
    assert coverage is not None
    assert set(coverage.intentionally_weak_evidence_ids) | set(
        coverage.potential_gap_evidence_ids
    ) == set(report.unmatchable_evidence_ids)
    assert coverage.unmatchable_total == len(report.unmatchable_evidence_ids)


def test_corroborated_material_group_is_not_blanket_rejected():
    packet = _packet(
        _obs("obs-1", "bark.texture", subject_id="pile"),
        _obs(
            "obs-2",
            "resin.presence",
            subject_id="pile",
            wood_surface=WoodSurface.ROUGH_END_GRAIN,
        ),
        subjects=(Subject(subject_id="pile", kind=SubjectKind.MATERIAL_GROUP),),
    )

    report = assess(packet)

    assert report.sufficient
    assert report.usable_subject_ids == ("pile",)
    assert report.tier_for("pile") == int(EvidenceTier.BARK)


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


def test_a_coverage_gap_alone_does_not_stop_the_run():
    """Both halves of the gate are required.

    A run can hold a coverage gap and still have a card worth ranking — that is the common
    case, and stopping there would abort every case that merely observed one unusual
    feature. The exit is for the run where there is nothing left to rank.
    """
    report = assess_quality(
        _packet(
            _obs("obs-1", "needles.fascicles"),
            _obs("obs-2", "bark.flake_geometry"),
        ),
        min_observations=2,
        require_non_colour=True,
        vocabulary=_VOCABULARY,
        cards_available=lambda _subject_id: True,
    )

    assert report.knowledge_coverage is not None
    assert report.knowledge_coverage.has_potential_gap
    assert report.sufficient
    assert report.coverage_gap_subject_ids == ()
    assert "knowledge_coverage_gap" not in report.insufficient_reasons


def test_an_empty_card_set_alone_does_not_claim_a_coverage_gap():
    """No card matched and nothing was outside the vocabulary either.

    That is ordinary insufficiency — the frame showed nothing the cards care about — and it
    must keep its existing reason rather than being relabelled as a knowledge-base limit.
    """
    report = assess_quality(
        _packet(_obs("obs-1", "needles.fascicles"), _obs("obs-2", "needles.fascicles")),
        min_observations=2,
        require_non_colour=True,
        vocabulary=_VOCABULARY,
        cards_available=lambda _subject_id: False,
    )

    assert report.coverage_gap_subject_ids == ()
    assert "knowledge_coverage_gap" not in report.insufficient_reasons


def test_the_gate_fires_only_for_the_subject_that_owns_the_gap():
    """Two trees, one describable. The describable one must survive.

    A run-wide gate would throw away a perfectly good verdict on a second subject because
    the first subject's bark happened to be unusual.
    """
    packet = EvidencePacket(
        subjects=(Subject(subject_id="log_1"), Subject(subject_id="log_2")),
        observations=(
            _obs("obs-1", "bark.flake_geometry", subject_id="log_1"),
            _obs("obs-2", "bark.flake_geometry", subject_id="log_1"),
            _obs("obs-3", "needles.fascicles", subject_id="log_2"),
            _obs("obs-4", "needles.fascicles", subject_id="log_2"),
        ),
    )

    report = assess_quality(
        packet,
        min_observations=2,
        require_non_colour=True,
        vocabulary=_VOCABULARY,
        cards_available=lambda subject_id: subject_id == "log_2",
    )

    assert report.coverage_gap_subject_ids == ("log_1",)
    assert report.usable_subject_ids == ("log_2",)
    assert report.sufficient


def test_without_a_card_predicate_the_gate_is_inert():
    """Callers with no knowledge base to consult keep the previous behaviour exactly."""
    report = assess_quality(
        _packet(
            _obs("obs-1", "bark.flake_geometry"),
            _obs("obs-2", "bark.surface_marks"),
        ),
        min_observations=2,
        require_non_colour=True,
        vocabulary=_VOCABULARY,
    )

    assert report.coverage_gap_subject_ids == ()
    assert "knowledge_coverage_gap" not in report.insufficient_reasons
