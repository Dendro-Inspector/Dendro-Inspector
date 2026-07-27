"""The shared candidate admission boundary is deterministic and fail-closed."""

from __future__ import annotations

import pytest

from evil_duck_dendro.knowledge.candidate_validation import (
    candidate_support_tier,
    validate_candidate_set,
    validate_candidate_set_with_report,
)
from evil_duck_dendro.knowledge.evidence_hierarchy import EvidenceTier
from evil_duck_dendro.schemas.candidates import Candidate, CandidateSet, SupportStrength
from evil_duck_dendro.schemas.evidence import (
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
from evil_duck_dendro.schemas.taxon import Resolution
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


def test_partial_matching_support_is_admitted_at_a_capped_tier(knowledge):
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
