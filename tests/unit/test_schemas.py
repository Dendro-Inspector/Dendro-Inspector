"""Contract enforcement at the type level."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from evil_duck_dendro.schemas.candidates import Candidate, CandidateSet, SupportStrength
from evil_duck_dendro.schemas.evidence import (
    EvidencePacket,
    Inference,
    Observation,
    ObservationSource,
    Subject,
    Visibility,
)
from evil_duck_dendro.schemas.taxon import (
    Confidence,
    Resolution,
    lower_confidence,
    lower_resolution,
    resolution_rank,
)


def _subject(subject_id: str = "log_1") -> Subject:
    return Subject(subject_id=subject_id)


def _observation(observation_id: str = "obs-1", subject_id: str = "log_1") -> Observation:
    return Observation(
        observation_id=observation_id,
        feature="bark.flake_geometry",
        value="thin_irregular_edge_lifting",
        subject_id=subject_id,
        source=ObservationSource.IMAGE,
        image_id="img-1",
    )


class TestObservationContract:
    def test_prose_cannot_be_stored_as_an_observation_value(self):
        """The central rule: structured fields hold tokens, never sentences."""
        with pytest.raises(ValidationError):
            Observation(
                observation_id="obs-1",
                feature="bark.flake_geometry",
                value="This is Pinus because the bark is red",
                subject_id="log_1",
                source=ObservationSource.IMAGE,
                image_id="img-1",
            )

    def test_feature_must_be_a_namespaced_path(self):
        with pytest.raises(ValidationError):
            Observation(
                observation_id="obs-1",
                feature="bark",  # no namespace segment
                value="rough",
                subject_id="log_1",
                source=ObservationSource.IMAGE,
                image_id="img-1",
            )

    def test_image_sourced_observation_must_name_its_image(self):
        with pytest.raises(ValidationError, match="must name the image_id"):
            Observation(
                observation_id="obs-1",
                feature="bark.flake_geometry",
                value="scaly",
                subject_id="log_1",
                source=ObservationSource.IMAGE,
            )

    def test_observations_are_immutable(self):
        observation = _observation()
        with pytest.raises(ValidationError):
            observation.value = "something_else"


class TestEvidencePacketIntegrity:
    def test_inference_must_reference_existing_observations(self):
        with pytest.raises(ValidationError, match="unknown observations"):
            EvidencePacket(
                subjects=(_subject(),),
                observations=(_observation(),),
                inferences=(
                    Inference(
                        inference_id="inf-1",
                        claim="compatible_with_pinus",
                        derived_from=("obs-does-not-exist",),
                    ),
                ),
            )

    def test_inference_with_no_basis_is_rejected(self):
        with pytest.raises(ValidationError):
            Inference(inference_id="inf-1", claim="compatible_with_pinus", derived_from=())

    def test_observation_must_belong_to_a_declared_subject(self):
        with pytest.raises(ValidationError, match="unknown subject"):
            EvidencePacket(
                subjects=(_subject("log_1"),),
                observations=(_observation(subject_id="log_2"),),
            )

    def test_duplicate_observation_ids_are_rejected(self):
        with pytest.raises(ValidationError, match="duplicate observation_id"):
            EvidencePacket(
                subjects=(_subject(),),
                observations=(_observation(), _observation()),
            )

    def test_observations_are_scoped_per_subject(self):
        packet = EvidencePacket(
            subjects=(_subject("log_1"), _subject("log_2")),
            observations=(
                _observation("obs-1", "log_1"),
                _observation("obs-2", "log_2"),
            ),
        )
        assert [o.observation_id for o in packet.observations_for("log_1")] == ["obs-1"]
        assert [o.observation_id for o in packet.observations_for("log_2")] == ["obs-2"]

    def test_not_visible_observations_are_excluded_from_visible(self):
        """`not visible` must never be usable as positive evidence."""
        hidden = _observation("obs-2").model_copy(update={"visibility": Visibility.NOT_VISIBLE})
        packet = EvidencePacket(
            subjects=(_subject(),),
            observations=(_observation("obs-1"), hidden),
        )
        assert len(packet.observations_for("log_1")) == 2
        assert len(packet.visible_observations_for("log_1")) == 1


class TestCandidateContract:
    def test_ranks_must_be_dense_and_one_based(self):
        with pytest.raises(ValidationError, match="dense and 1-based"):
            CandidateSet(
                subject_id="log_1",
                candidates=(
                    Candidate(taxon="pinus", resolution=Resolution.GENUS, rank=1),
                    Candidate(taxon="picea", resolution=Resolution.GENUS, rank=3),
                ),
            )

    def test_duplicate_taxa_are_rejected(self):
        with pytest.raises(ValidationError, match="duplicate taxon"):
            CandidateSet(
                subject_id="log_1",
                candidates=(
                    Candidate(taxon="pinus", resolution=Resolution.GENUS, rank=1),
                    Candidate(taxon="pinus", resolution=Resolution.SPECIES, rank=2),
                ),
            )

    def test_leaders_are_close_when_scores_match(self):
        close = CandidateSet(
            subject_id="log_1",
            candidates=(
                Candidate(
                    taxon="pinus",
                    resolution=Resolution.GENUS,
                    rank=1,
                    score=SupportStrength.MODERATE,
                ),
                Candidate(
                    taxon="picea",
                    resolution=Resolution.GENUS,
                    rank=2,
                    score=SupportStrength.MODERATE,
                ),
            ),
        )
        clear = CandidateSet(
            subject_id="log_1",
            candidates=(
                Candidate(
                    taxon="pinus", resolution=Resolution.GENUS, rank=1, score=SupportStrength.STRONG
                ),
                Candidate(
                    taxon="picea", resolution=Resolution.GENUS, rank=2, score=SupportStrength.WEAK
                ),
            ),
        )
        assert close.leaders_are_close()
        assert not clear.leaders_are_close()


class TestTaxonomicOrdering:
    def test_resolution_ranking_is_broadest_to_narrowest(self):
        assert resolution_rank(Resolution.UNKNOWN) < resolution_rank(Resolution.FAMILY)
        assert resolution_rank(Resolution.GENUS) < resolution_rank(Resolution.SPECIES)

    def test_lowering_bottoms_out_rather_than_wrapping(self):
        assert lower_resolution(Resolution.SPECIES) is Resolution.SPECIES_GROUP
        assert lower_resolution(Resolution.FAMILY) is Resolution.UNKNOWN
        assert lower_resolution(Resolution.UNKNOWN) is Resolution.UNKNOWN
        assert lower_confidence(Confidence.LOW) is Confidence.LOW
