"""The shared candidate admission boundary is deterministic and fail-closed."""

from __future__ import annotations

import pytest

from dendro_inspector.knowledge.candidate_validation import (
    candidate_support_tier,
    cards_in_play,
    validate_candidate_set,
    validate_candidate_set_with_report,
)
from dendro_inspector.knowledge.evidence_hierarchy import EvidenceTier
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
    WoodSurface,
)
from dendro_inspector.schemas.taxon import Resolution
from tests.conftest import _attachment, _wood_surface


def _obs(
    observation_id: str,
    feature: str,
    value: str,
    *,
    subject_id: str = "log_1",
    source: ObservationSource = ObservationSource.IMAGE,
    visibility: Visibility = Visibility.CLEAR,
    reliability: Reliability = Reliability.MEDIUM,
    wood_surface: WoodSurface = WoodSurface.PREPARED_END_GRAIN,
) -> Observation:
    return Observation(
        observation_id=observation_id,
        feature=feature,
        value=value,
        subject_id=subject_id,
        source=source,
        image_id="img-1" if source is ObservationSource.IMAGE else None,
        visibility=visibility,
        reliability=reliability,
        attachment=_attachment(feature, True),
        wood_surface=_wood_surface(feature, wood_surface),
    )


def _candidate(
    taxon: str,
    rank: int,
    *supporting_ids: str,
    contradicting_ids: tuple[str, ...] = (),
) -> Candidate:
    return Candidate(
        taxon=taxon,
        resolution=Resolution.GENUS,
        supporting_evidence_ids=supporting_ids,
        contradicting_evidence_ids=contradicting_ids,
        score=SupportStrength.MODERATE,
        rank=rank,
    )


def _packet(
    *observations: Observation,
    inferences: tuple[Inference, ...] = (),
    subjects: tuple[Subject, ...] = (Subject(subject_id="log_1"),),
) -> EvidencePacket:
    return EvidencePacket(subjects=subjects, observations=observations, inferences=inferences)


def test_unknown_taxon_is_removed(knowledge):
    evidence = _packet(_obs("o1", "needles.fascicles", "two"))
    candidate_set = CandidateSet(
        subject_id="log_1", candidates=(_candidate("unknown_taxon", 1, "o1"),)
    )

    result = validate_candidate_set_with_report(candidate_set, evidence, knowledge)

    assert result.candidate_set.candidates == ()
    assert result.rejected_taxa == ("unknown_taxon",)


def test_empty_or_unrelated_support_removes_a_candidate(knowledge):
    evidence = _packet(_obs("o1", "bark.flake_geometry", "thin_irregular_edge_lifting"))
    candidate_set = CandidateSet(subject_id="log_1", candidates=(_candidate("pinus", 1, "o1"),))

    result = validate_candidate_set_with_report(candidate_set, evidence, knowledge)

    assert result.candidate_set.candidates == ()
    assert result.dropped_evidence_ids == ("o1",)


def test_matching_inference_support_is_admitted(knowledge):
    evidence = _packet(
        _obs("o1", "needles.fascicles", "two"),
        inferences=(Inference(inference_id="i1", claim="pinus_compatible", derived_from=("o1",)),),
    )
    candidate_set = CandidateSet(subject_id="log_1", candidates=(_candidate("pinus", 1, "i1"),))

    validated = validate_candidate_set(candidate_set, evidence, knowledge)

    assert validated.leader is not None
    assert validated.leader.supporting_evidence_ids == ("i1",)
    assert candidate_support_tier(validated.leader, evidence, "log_1") is EvidenceTier.FOLIAGE


def test_attached_component_support_is_admitted_for_its_identity_root(knowledge):
    evidence = _packet(
        _obs("leaf", "leaf.shape", "simple_lobed", subject_id="shoot_1"),
        subjects=(
            Subject(subject_id="tree_1", kind=SubjectKind.STANDING_TREE),
            Subject(
                subject_id="shoot_1",
                kind=SubjectKind.BRANCH,
                parent_subject_id="tree_1",
            ),
        ),
    ).collapse_subject_components()
    candidate_set = CandidateSet(
        subject_id="tree_1", candidates=(_candidate("quercus", 1, "leaf"),)
    )

    validated = validate_candidate_set(candidate_set, evidence, knowledge)

    assert validated.leader is not None
    assert validated.leader.taxon == "quercus"
    assert validated.leader.supporting_evidence_ids == ("leaf",)


@pytest.mark.parametrize(
    "attachment",
    (AttachmentStatus.UNKNOWN, AttachmentStatus.CONFIRMED_DETACHED),
)
def test_non_attached_component_evidence_stays_context_only(knowledge, attachment):
    leaf = _obs("leaf", "leaf.shape", "simple_lobed", subject_id="shoot_1").model_copy(
        update={"attachment": attachment}
    )
    evidence = _packet(
        leaf,
        subjects=(
            Subject(subject_id="tree_1", kind=SubjectKind.STANDING_TREE),
            Subject(
                subject_id="shoot_1",
                kind=SubjectKind.BRANCH,
                parent_subject_id="tree_1",
            ),
        ),
    ).collapse_subject_components()
    candidate_set = CandidateSet(
        subject_id="tree_1", candidates=(_candidate("quercus", 1, "leaf"),)
    )

    validated = validate_candidate_set(candidate_set, evidence, knowledge)

    assert validated.candidates == ()


def test_a_partial_but_confidently_read_feature_keeps_its_family_tier(knowledge):
    """Framing is not the same limit as doubt.

    This assertion used to read `EvidenceTier.BARK`: a partial view demoted a fascicle
    count to bark-equivalent authority even when the extractor said it was sure of the
    reading. A needle bundle at the edge of the frame is still foliage.
    """
    evidence = _packet(
        _obs(
            "o1",
            "needles.fascicles",
            "two",
            visibility=Visibility.PARTIAL,
            reliability=Reliability.HIGH,
        )
    )
    candidate_set = CandidateSet(subject_id="log_1", candidates=(_candidate("pinus", 1, "o1"),))

    validated = validate_candidate_set(candidate_set, evidence, knowledge)

    assert validated.leader is not None
    assert candidate_support_tier(validated.leader, evidence, "log_1") is EvidenceTier.FOLIAGE


def test_a_partial_view_without_a_confident_reading_stays_capped(knowledge):
    """`PARTIAL` at anything below HIGH keeps the old bark-equivalent cap.

    `PARTIAL` carries two meanings this schema cannot separate: "unambiguous but not
    filling the frame" and "partly hidden, so the reading is incomplete". The reliability
    the extractor attached is the only thing that distinguishes them, so a half-seen
    decisive feature it was merely moderately sure of does not get promoted.
    """
    evidence = _packet(
        _obs(
            "o1",
            "needles.fascicles",
            "two",
            visibility=Visibility.PARTIAL,
            reliability=Reliability.MEDIUM,
        )
    )
    candidate_set = CandidateSet(subject_id="log_1", candidates=(_candidate("pinus", 1, "o1"),))

    validated = validate_candidate_set(candidate_set, evidence, knowledge)

    assert validated.leader is not None
    assert candidate_support_tier(validated.leader, evidence, "log_1") is EvidenceTier.BARK


@pytest.mark.parametrize(
    ("source", "visibility"),
    [
        (ObservationSource.USER, Visibility.CLEAR),
        (ObservationSource.METADATA, Visibility.CLEAR),
        (ObservationSource.EXTERNAL_CONTEXT, Visibility.CLEAR),
        (ObservationSource.IMAGE, Visibility.OBSCURED),
        (ObservationSource.IMAGE, Visibility.NOT_VISIBLE),
    ],
)
def test_contextual_evidence_cannot_support_a_candidate(knowledge, source, visibility):
    evidence = _packet(
        _obs(
            "o1",
            "needles.fascicles",
            "two",
            source=source,
            visibility=visibility,
        )
    )
    candidate_set = CandidateSet(subject_id="log_1", candidates=(_candidate("pinus", 1, "o1"),))

    assert validate_candidate_set(candidate_set, evidence, knowledge).candidates == ()


def test_survivors_keep_order_and_receive_dense_ranks(knowledge):
    evidence = _packet(
        _obs("o_unknown", "needles.fascicles", "two"),
        _obs("o_pinus", "needles.fascicles", "two"),
        _obs("o_picea", "needles.attachment", "single_on_woody_peg"),
    )
    candidate_set = CandidateSet(
        subject_id="log_1",
        candidates=(
            _candidate("unknown_taxon", 1, "o_unknown"),
            _candidate("pinus", 2, "o_pinus"),
            _candidate("picea", 3, "o_picea"),
        ),
    )

    validated = validate_candidate_set(candidate_set, evidence, knowledge)

    assert [(candidate.taxon, candidate.rank) for candidate in validated.ordered] == [
        ("pinus", 1),
        ("picea", 2),
    ]


def test_evidence_ids_are_deduplicated_and_card_checked(knowledge):
    evidence = _packet(
        _obs("support", "needles.attachment", "single_on_woody_peg"),
        _obs("contradiction", "needles.fascicles", "two"),
        _obs("unrelated", "bark.flake_geometry", "thin_irregular_edge_lifting"),
    )
    candidate_set = CandidateSet(
        subject_id="log_1",
        candidates=(
            _candidate(
                "picea",
                1,
                "support",
                "support",
                contradicting_ids=("contradiction", "unrelated", "contradiction"),
            ),
        ),
    )

    result = validate_candidate_set_with_report(candidate_set, evidence, knowledge)

    assert result.candidate_set.leader is not None
    assert result.candidate_set.leader.supporting_evidence_ids == ("support",)
    assert result.candidate_set.leader.contradicting_evidence_ids == ("contradiction",)
    assert result.dropped_evidence_ids == ("unrelated",)


def test_cross_subject_support_is_removed(knowledge):
    evidence = _packet(
        _obs("o1", "needles.fascicles", "two", subject_id="log_2"),
        subjects=(Subject(subject_id="log_1"), Subject(subject_id="log_2")),
    )
    candidate_set = CandidateSet(subject_id="log_1", candidates=(_candidate("pinus", 1, "o1"),))

    assert validate_candidate_set(candidate_set, evidence, knowledge).candidates == ()


def test_inference_with_unrelated_source_is_not_candidate_specific(knowledge):
    evidence = _packet(
        _obs("o1", "needles.fascicles", "two"),
        _obs("o2", "bark.flake_geometry", "thin_irregular_edge_lifting"),
        inferences=(
            Inference(inference_id="i1", claim="pinus_compatible", derived_from=("o1", "o2")),
        ),
    )
    candidate_set = CandidateSet(subject_id="log_1", candidates=(_candidate("pinus", 1, "i1"),))

    assert validate_candidate_set(candidate_set, evidence, knowledge).candidates == ()


def test_mixed_subject_inference_is_removed(knowledge):
    evidence = _packet(
        _obs("o1", "needles.fascicles", "two"),
        _obs("o2", "needles.fascicles", "two", subject_id="log_2"),
        inferences=(
            Inference(inference_id="i1", claim="pinus_compatible", derived_from=("o1", "o2")),
        ),
        subjects=(Subject(subject_id="log_1"), Subject(subject_id="log_2")),
    )
    candidate_set = CandidateSet(subject_id="log_1", candidates=(_candidate("pinus", 1, "i1"),))

    assert validate_candidate_set(candidate_set, evidence, knowledge).candidates == ()


def test_all_candidates_removed_leaves_an_explicit_empty_set(knowledge):
    evidence = _packet(_obs("o1", "bark.flake_geometry", "thin_irregular_edge_lifting"))
    candidate_set = CandidateSet(
        subject_id="log_1",
        candidates=(
            _candidate("pinus", 1, "o1"),
            _candidate("unknown_taxon", 2, "o1"),
        ),
    )

    validated = validate_candidate_set(candidate_set, evidence, knowledge)

    assert validated.subject_id == "log_1"
    assert validated.candidates == ()


def test_colour_only_candidate_is_rejected_even_when_the_card_matches(knowledge):
    evidence = _packet(
        _obs(
            "tone",
            "heartwood.tone",
            "warm_yellow_orange",
            wood_surface=WoodSurface.SPLIT_FACE,
        )
    )
    candidate_set = CandidateSet(
        subject_id="log_1",
        candidates=(_candidate("prunus", 1, "tone"),),
    )

    result = validate_candidate_set_with_report(candidate_set, evidence, knowledge)

    assert result.candidate_set.candidates == ()
    assert result.rejected_taxa == ("prunus",)
    assert result.dropped_evidence_ids == ("tone",)


def test_colour_plus_context_is_not_structural_corroboration(knowledge):
    evidence = _packet(
        _obs("tone", "heartwood.tone", "warm_yellow_orange"),
        _obs("site", "context.site", "garden_roadside"),
    )
    candidate_set = CandidateSet(
        subject_id="log_1",
        candidates=(_candidate("prunus", 1, "tone", "site"),),
    )

    result = validate_candidate_set_with_report(candidate_set, evidence, knowledge)

    assert result.candidate_set.candidates == ()
    assert set(result.dropped_evidence_ids) == {"tone", "site"}


def test_colour_with_exact_structural_support_survives_conservatively(knowledge):
    evidence = _packet(
        _obs(
            "tone",
            "heartwood.tone",
            "warm_yellow_orange",
            wood_surface=WoodSurface.SPLIT_FACE,
        ),
        _obs("lenticels", "lenticels.orientation", "horizontal"),
    )
    candidate_set = CandidateSet(
        subject_id="log_1",
        candidates=(_candidate("prunus", 1, "tone", "lenticels"),),
    )

    validated = validate_candidate_set(candidate_set, evidence, knowledge)

    assert validated.leader is not None
    assert validated.leader.supporting_evidence_ids == ("tone", "lenticels")
    assert candidate_support_tier(validated.leader, evidence, "log_1") is EvidenceTier.BARK


def test_colour_spelling_is_not_rewritten_at_the_admission_boundary(knowledge):
    evidence = _packet(
        _obs("tone", "heartwood.color", "warm_yellow_orange"),
        _obs("lenticels", "lenticels.orientation", "horizontal"),
    )
    candidate_set = CandidateSet(
        subject_id="log_1",
        candidates=(_candidate("prunus", 1, "tone", "lenticels"),),
    )

    validated = validate_candidate_set(candidate_set, evidence, knowledge)

    assert validated.leader is not None
    assert validated.leader.supporting_evidence_ids == ("lenticels",)


def test_corroborated_material_group_candidate_remains_admissible(knowledge):
    evidence = _packet(
        _obs("bark", "bark.texture", "scaly_plates", subject_id="pile"),
        _obs(
            "resin",
            "resin.presence",
            "present",
            subject_id="pile",
            wood_surface=WoodSurface.ROUGH_END_GRAIN,
        ),
        subjects=(Subject(subject_id="pile", kind=SubjectKind.MATERIAL_GROUP),),
    )
    candidate_set = CandidateSet(
        subject_id="pile",
        candidates=(_candidate("pinus", 1, "bark", "resin"),),
    )

    validated = validate_candidate_set(candidate_set, evidence, knowledge)

    assert validated.leader is not None
    assert validated.leader.taxon == "pinus"
    assert candidate_support_tier(validated.leader, evidence, "pile") is EvidenceTier.BARK


def test_strong_label_on_supporting_only_hit_is_demoted(knowledge):
    """A `strong` label is not evidence. Only what the card admits decides strength.

    `bark.texture = scaly_plates` is one `supporting_features` entry of the Pinus card and
    matches no `strong_positive_features` entry, so C1's table derives `weak`: `moderate`
    needs a strong-feature hit or two supporting hits.
    """
    evidence = _packet(_obs("bark", "bark.texture", "scaly_plates"))
    candidate_set = CandidateSet(
        subject_id="log_1",
        candidates=(
            Candidate(
                taxon="pinus",
                resolution=Resolution.GENUS,
                supporting_evidence_ids=("bark",),
                score=SupportStrength.STRONG,
                rank=1,
            ),
        ),
    )

    validated = validate_candidate_set(candidate_set, evidence, knowledge)

    assert validated.leader is not None
    assert validated.leader.score is SupportStrength.WEAK


class TestAdjudicatedSupportStrength:
    """C1: the card decides strength; the model's label may only lower it.

    One row of the specification's table per test, plus the two directions that matter more
    than the table — a label is never raised, and the demotion is reported rather than
    applied silently.
    """

    def _set(self, score: SupportStrength, *support_ids: str) -> CandidateSet:
        return CandidateSet(
            subject_id="log_1",
            candidates=(
                Candidate(
                    taxon="pinus",
                    resolution=Resolution.GENUS,
                    supporting_evidence_ids=support_ids,
                    score=score,
                    rank=1,
                ),
            ),
        )

    def _score(self, knowledge, evidence, candidate_set: CandidateSet) -> SupportStrength:
        validated = validate_candidate_set(candidate_set, evidence, knowledge)
        assert validated.leader is not None
        return validated.leader.score

    def test_a_full_trust_strong_hit_meeting_the_requirement_is_strong(self, knowledge):
        """`needles.fascicles` is a Pinus strong-positive feature and satisfies its
        `needles_or_cones` requirement, so the card grants the label the model claimed."""
        evidence = _packet(_obs("needles", "needles.fascicles", "two"))

        assert (
            self._score(knowledge, evidence, self._set(SupportStrength.STRONG, "needles"))
            is SupportStrength.STRONG
        )

    def test_two_supporting_hits_reach_moderate_and_no_further(self, knowledge):
        evidence = _packet(
            _obs("bark", "bark.texture", "scaly_plates"),
            _obs("trunk", "trunk.form", "straight_long"),
        )

        assert (
            self._score(knowledge, evidence, self._set(SupportStrength.STRONG, "bark", "trunk"))
            is SupportStrength.MODERATE
        )

    def test_one_supporting_hit_is_weak(self, knowledge):
        evidence = _packet(_obs("bark", "bark.texture", "scaly_plates"))

        assert (
            self._score(knowledge, evidence, self._set(SupportStrength.STRONG, "bark"))
            is SupportStrength.WEAK
        )

    def test_a_weak_label_is_never_raised_by_the_card(self, knowledge):
        """The model looked at the photograph. A doubt the card cannot express is still a
        doubt, and adjudication only ever runs downward."""
        evidence = _packet(_obs("needles", "needles.fascicles", "two"))

        assert (
            self._score(knowledge, evidence, self._set(SupportStrength.WEAK, "needles"))
            is SupportStrength.WEAK
        )

    def test_the_demotion_is_reported_not_applied_silently(self, knowledge):
        evidence = _packet(_obs("bark", "bark.texture", "scaly_plates"))

        report = validate_candidate_set_with_report(
            self._set(SupportStrength.STRONG, "bark"), evidence, knowledge
        )

        assert report.demoted_scores == (("pinus", SupportStrength.STRONG, SupportStrength.WEAK),)

    def test_an_undemoted_candidate_is_not_reported(self, knowledge):
        evidence = _packet(_obs("needles", "needles.fascicles", "two"))

        report = validate_candidate_set_with_report(
            self._set(SupportStrength.STRONG, "needles"), evidence, knowledge
        )

        assert report.demoted_scores == ()


class TestEvidenceContradictingACardsOwnStrongFeature:
    """A card the evidence already disagrees with must not be opened by a generic feature.

    From live case ``20260510_100131``. The subject was a scaly-barked conifer trunk;
    ``fagus`` was retrieved and admitted on ``trunk.form = straight_cylindrical`` — a
    supporting feature that fits most trees — while the same packet carried
    ``bark.texture = fine_scales`` and the ``fagus`` card declares
    ``bark.texture: smooth_grey`` as strong-positive. Beech bark is smooth. That bark was
    not. It still travelled into four model calls.
    """

    def test_fagus_is_not_admitted_from_a_cylindrical_trunk_alone(self, knowledge):
        evidence = _packet(
            _obs("obs-1", "bark.texture", "fine_scales"),
            _obs("obs-2", "trunk.form", "straight_cylindrical"),
        )
        candidate_set = CandidateSet(
            subject_id="log_1",
            candidates=(
                Candidate(
                    taxon="fagus",
                    resolution=Resolution.GENUS,
                    score=SupportStrength.WEAK,
                    rank=1,
                    supporting_evidence_ids=("obs-2",),
                ),
            ),
        )

        result = validate_candidate_set_with_report(candidate_set, evidence, knowledge)

        assert result.candidate_set.candidates == ()
        assert "fagus" in result.rejected_taxa

    def test_the_generic_feature_alone_would_otherwise_have_admitted_it(self, knowledge):
        """Without the contradicting bark reading, trunk form still opens fagus.

        Stated so the test above cannot pass for the wrong reason. This is the behaviour
        being narrowed; if this case ever stops admitting fagus, the narrowing went further
        than intended and the other test would no longer be evidence of anything.
        """
        evidence = _packet(_obs("obs-1", "trunk.form", "straight_cylindrical"))
        candidate_set = CandidateSet(
            subject_id="log_1",
            candidates=(
                Candidate(
                    taxon="fagus",
                    resolution=Resolution.GENUS,
                    score=SupportStrength.WEAK,
                    rank=1,
                    supporting_evidence_ids=("obs-1",),
                ),
            ),
        )

        validated = validate_candidate_set(candidate_set, evidence, knowledge)

        assert [candidate.taxon for candidate in validated.candidates] == ["fagus"]

    def test_a_strong_positive_match_still_admits_normally(self, knowledge):
        """The rule must not touch a card the evidence actually agrees with."""
        evidence = _packet(_obs("obs-1", "bark.texture", "smooth_grey"))
        candidate_set = CandidateSet(
            subject_id="log_1",
            candidates=(
                Candidate(
                    taxon="fagus",
                    resolution=Resolution.GENUS,
                    score=SupportStrength.MODERATE,
                    rank=1,
                    supporting_evidence_ids=("obs-1",),
                ),
            ),
        )

        validated = validate_candidate_set(candidate_set, evidence, knowledge)

        assert [candidate.taxon for candidate in validated.candidates] == ["fagus"]

    def test_a_card_whose_strong_path_was_never_observed_is_unaffected(self, knowledge):
        """Picea survives the same frame, and should.

        Its only strong feature is ``needles.attachment``, which this photograph does not
        show at all. Silence on a decisive path is not disagreement with it — that
        distinction is the whole rule, and collapsing it would abstain on every bark photo.
        """
        evidence = _packet(_obs("obs-1", "bark.texture", "fine_scales"))
        candidate_set = CandidateSet(
            subject_id="log_1",
            candidates=(
                Candidate(
                    taxon="picea",
                    resolution=Resolution.GENUS,
                    score=SupportStrength.WEAK,
                    rank=1,
                    supporting_evidence_ids=("obs-1",),
                ),
            ),
        )

        validated = validate_candidate_set(candidate_set, evidence, knowledge)

        assert [candidate.taxon for candidate in validated.candidates] == ["picea"]

    # --- The four cases that must NOT veto. Each is silence, not disagreement, and
    # --- promoting silence into contradiction would abstain on nearly every photograph.

    def test_an_unreadable_value_is_silence_not_disagreement(self, knowledge):
        """`bark.texture = not_resolvable` means "I looked and could not tell".

        Treating it as a value that conflicts with `smooth_grey` would turn absence of
        evidence into evidence of absence — the exact inversion this veto must not make.
        """
        evidence = _packet(
            _obs("obs-1", "bark.texture", "not_resolvable"),
            _obs("obs-2", "trunk.form", "straight_cylindrical"),
        )

        assert "fagus" in cards_in_play(evidence, knowledge, ("log_1",))

    def test_an_untrusted_conflicting_observation_does_not_veto(self, knowledge):
        """An obscured reading cannot carry positive support, so it cannot carry a veto."""
        evidence = _packet(
            _obs("obs-1", "bark.texture", "fine_scales", visibility=Visibility.OBSCURED),
            _obs("obs-2", "trunk.form", "straight_cylindrical"),
        )

        assert "fagus" in cards_in_play(evidence, knowledge, ("log_1",))

    def test_an_unobserved_decisive_path_does_not_veto(self, knowledge):
        """Picea's only strong path is `needles.attachment`; a bark photo cannot show it."""
        evidence = _packet(_obs("obs-1", "bark.texture", "fine_scales"))

        assert "picea" in cards_in_play(evidence, knowledge, ("log_1",))

    def test_one_tree_s_bark_does_not_veto_another_tree_s_card(self, knowledge):
        """Two trunks in frame: the scaly one must not disqualify the smooth one.

        `cards_in_play` accepts several subjects at once. Pooling their observations before
        testing the veto let a conflict on one subject remove a card the other subject
        positively supported.
        """
        evidence = EvidencePacket(
            subjects=(Subject(subject_id="log_1"), Subject(subject_id="log_2")),
            observations=(
                _obs("obs-1", "bark.texture", "fine_scales", subject_id="log_1"),
                _obs("obs-2", "bark.texture", "smooth_grey", subject_id="log_2"),
            ),
        )

        assert "fagus" in cards_in_play(evidence, knowledge, ("log_1", "log_2"))
        assert "fagus" in cards_in_play(evidence, knowledge, ("log_2",))
        assert "fagus" not in cards_in_play(evidence, knowledge, ("log_1",))

    def test_retrieval_and_admission_agree(self, knowledge):
        """Retrieval must not offer a card admission would reject, or vice versa."""
        evidence = _packet(
            _obs("obs-1", "bark.texture", "fine_scales"),
            _obs("obs-2", "trunk.form", "straight_cylindrical"),
        )

        assert "fagus" not in cards_in_play(evidence, knowledge, ("log_1",))
        assert "picea" in cards_in_play(evidence, knowledge, ("log_1",))
