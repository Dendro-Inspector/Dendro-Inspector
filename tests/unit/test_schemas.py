"""Contract enforcement at the type level."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dendro_inspector.schemas.candidates import Candidate, CandidateSet, SupportStrength
from dendro_inspector.schemas.evidence import (
    AttachmentStatus,
    EvidencePacket,
    GeneratedObservation,
    ImageLimitation,
    Inference,
    Observation,
    ObservationSource,
    Subject,
    SubjectKind,
    Visibility,
    WoodSurface,
    requires_wood_surface,
)
from dendro_inspector.schemas.taxon import (
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

    def test_legacy_wood_observation_defaults_surface_to_unknown(self):
        observation = Observation(
            observation_id="obs-wood",
            feature="wood.tone",
            value="light_yellow_honey",
            subject_id="log_1",
            source=ObservationSource.IMAGE,
            image_id="img-1",
        )
        assert observation.wood_surface is WoodSurface.UNKNOWN
        assert observation.model_dump(mode="json")["wood_surface"] == "unknown"

    def test_generated_wood_observation_requires_explicit_surface(self):
        with pytest.raises(ValidationError, match="must set wood_surface"):
            GeneratedObservation(
                observation_id="obs-wood",
                feature="wood.tone",
                value="light_yellow_honey",
                subject_id="log_1",
                source=ObservationSource.IMAGE,
                image_id="img-1",
            )

    def test_generated_observation_cannot_forge_component_provenance(self):
        with pytest.raises(ValidationError, match="must omit source_component_id"):
            GeneratedObservation(
                observation_id="obs-bark",
                feature="bark.texture",
                value="rough",
                subject_id="tree_1",
                source_component_id="branch_1",
                source=ObservationSource.IMAGE,
                image_id="img-1",
            )

    def test_non_wood_observation_must_not_claim_a_wood_surface(self):
        with pytest.raises(ValidationError, match="must omit wood_surface"):
            Observation(
                observation_id="obs-bark",
                feature="bark.texture",
                value="rough",
                subject_id="log_1",
                source=ObservationSource.IMAGE,
                image_id="img-1",
                wood_surface=WoodSurface.SPLIT_FACE,
            )

    @pytest.mark.parametrize(
        "feature",
        (
            "wood.tone",
            "cut.orientation",
            "rings.width",
            "pores.arrangement",
            "rays.visibility",
            "resin.presence",
            "heartwood.tone",
            "sapwood.width",
            "inner_bark.colour",
        ),
    )
    def test_wood_surface_family_predicate_covers_the_contract(self, feature):
        assert requires_wood_surface(feature)

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

    def test_duplicate_image_limitation_ids_are_rejected(self):
        with pytest.raises(ValidationError, match="duplicate image limitation image_id"):
            EvidencePacket(
                subjects=(_subject(),),
                observations=(_observation(),),
                image_limitations=(
                    ImageLimitation(image_id="img-1"),
                    ImageLimitation(image_id="img-1"),
                ),
            )

    def test_component_parent_must_exist(self):
        with pytest.raises(ValidationError, match="unknown parent subject"):
            EvidencePacket(
                subjects=(
                    Subject(
                        subject_id="shoot_1",
                        kind=SubjectKind.BRANCH,
                        parent_subject_id="missing_tree",
                    ),
                )
            )

    def test_component_parent_graph_must_be_acyclic(self):
        with pytest.raises(ValidationError, match="subject parent cycle"):
            EvidencePacket(
                subjects=(
                    Subject(subject_id="a", parent_subject_id="b"),
                    Subject(subject_id="b", parent_subject_id="a"),
                )
            )

    def test_components_collapse_into_their_identity_root(self):
        packet = EvidencePacket(
            subjects=(
                Subject(subject_id="tree_1", kind=SubjectKind.STANDING_TREE),
                Subject(
                    subject_id="shoot_1",
                    kind=SubjectKind.BRANCH,
                    parent_subject_id="tree_1",
                ),
                Subject(subject_id="tree_2", kind=SubjectKind.STANDING_TREE),
            ),
            observations=(
                Observation(
                    observation_id="attached_leaf",
                    feature="leaf.shape",
                    value="simple_lobed",
                    subject_id="shoot_1",
                    source=ObservationSource.IMAGE,
                    image_id="img-1",
                    attachment=AttachmentStatus.CONFIRMED_ATTACHED,
                ),
                _observation("other_bark", "tree_2"),
            ),
            possible_multiple_taxa=True,
        )

        canonical = packet.collapse_subject_components()

        assert [subject.subject_id for subject in canonical.subjects] == ["tree_1", "tree_2"]
        assert canonical.observations_for("tree_1")[0].observation_id == "attached_leaf"
        assert canonical.observations_for("tree_1")[0].source_component_id == "shoot_1"
        assert canonical.observations_for("tree_2")[0].observation_id == "other_bark"
        assert canonical.possible_multiple_taxa

    def test_multi_level_component_resolves_to_one_root(self):
        packet = EvidencePacket(
            subjects=(
                Subject(subject_id="tree", kind=SubjectKind.STANDING_TREE),
                Subject(
                    subject_id="branch",
                    kind=SubjectKind.BRANCH,
                    parent_subject_id="tree",
                ),
                Subject(
                    subject_id="twig",
                    kind=SubjectKind.BRANCH,
                    parent_subject_id="branch",
                ),
            ),
            observations=(
                Observation(
                    observation_id="leaf",
                    feature="leaf.shape",
                    value="simple_lobed",
                    subject_id="twig",
                    source=ObservationSource.IMAGE,
                    image_id="img-1",
                    attachment=AttachmentStatus.CONFIRMED_ATTACHED,
                ),
            ),
        )

        canonical = packet.collapse_subject_components()

        assert [subject.subject_id for subject in canonical.subjects] == ["tree"]
        assert canonical.observations[0].subject_id == "tree"
        assert canonical.observations[0].source_component_id == "twig"

    def test_crossing_branch_without_parent_remains_independent(self):
        packet = EvidencePacket(
            subjects=(
                Subject(subject_id="tree", kind=SubjectKind.STANDING_TREE),
                Subject(subject_id="crossing_branch", kind=SubjectKind.BRANCH),
            ),
            observations=(
                Observation(
                    observation_id="leaf",
                    feature="leaf.shape",
                    value="simple_lobed",
                    subject_id="crossing_branch",
                    source=ObservationSource.IMAGE,
                    image_id="img-1",
                    attachment=AttachmentStatus.UNKNOWN,
                ),
            ),
        )

        canonical = packet.collapse_subject_components()

        assert canonical is packet
        assert [subject.subject_id for subject in canonical.subjects] == [
            "tree",
            "crossing_branch",
        ]
        assert canonical.observations[0].subject_id == "crossing_branch"
        assert canonical.observations[0].source_component_id is None

    def test_upper_and_lower_bark_zones_remain_one_tree_identity(self):
        packet = EvidencePacket(
            subjects=(
                Subject(subject_id="tree", kind=SubjectKind.STANDING_TREE),
                Subject(
                    subject_id="upper_bark",
                    kind=SubjectKind.BARK_SURFACE,
                    parent_subject_id="tree",
                ),
                Subject(
                    subject_id="lower_bark",
                    kind=SubjectKind.BARK_SURFACE,
                    parent_subject_id="tree",
                ),
            ),
            observations=(
                _observation("upper", "upper_bark"),
                _observation("lower", "lower_bark"),
            ),
        )

        canonical = packet.collapse_subject_components()

        assert [subject.subject_id for subject in canonical.subjects] == ["tree"]
        assert {item.observation_id for item in canonical.observations_for("tree")} == {
            "upper",
            "lower",
        }

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
