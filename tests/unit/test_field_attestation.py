"""The uploader's word that every photo shows one tree (field context).

Built on synthetic packets shaped like a common upload: a trunk, plus nuts and a leaf
picked up beneath it that the extractor recorded as a separate detached part.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dendro_inspector.knowledge.evidence_hierarchy import EvidenceTrust, observation_trust
from dendro_inspector.nodes.field_attestation import attest_one_tree, attested_evidence_ids
from dendro_inspector.schemas.evidence import (
    AttachmentStatus,
    EvidencePacket,
    GeneratedObservation,
    Observation,
    ObservationSource,
    Reliability,
    Subject,
    SubjectKind,
    Visibility,
)
from dendro_inspector.schemas.input import CaseInput

TREE = "tree_1"
LOOSE = "nuts_1"


def _obs(oid: str, feature: str, value: str, subject: str, **extra: object) -> Observation:
    detachable = feature.split(".")[0] in {"leaf", "leaflet", "nut", "fruit"}
    payload: dict[str, object] = {
        "observation_id": oid,
        "feature": feature,
        "value": value,
        "subject_id": subject,
        "source": ObservationSource.IMAGE,
        "image_id": "img-1",
        "visibility": Visibility.CLEAR,
        "reliability": Reliability.HIGH,
        "attachment": AttachmentStatus.CONFIRMED_DETACHED if detachable else None,
    }
    payload.update(extra)
    return Observation.model_validate(payload)


def _packet(*, trees: int = 1) -> EvidencePacket:
    subjects = [Subject(subject_id=TREE, kind=SubjectKind.STANDING_TREE, image_ids=("img-1",))]
    if trees == 2:
        subjects.append(Subject(subject_id="tree_2", kind=SubjectKind.STANDING_TREE))
    subjects.append(Subject(subject_id=LOOSE, kind=SubjectKind.DETACHED_PART, image_ids=("img-2",)))
    return EvidencePacket(
        subjects=tuple(subjects),
        observations=(
            _obs("obs-bark", "bark.pattern", "diamond_fissures", TREE),
            _obs("obs-nut", "nut.shell_surface", "deeply_corrugated_ridged", LOOSE),
            _obs("obs-leaf", "leaflet.count", "fifteen_to_twenty_three", LOOSE),
            _obs(
                "obs-hidden",
                "fruit.husk",
                "indehiscent_green_globose",
                LOOSE,
                visibility=Visibility.OBSCURED,
            ),
        ),
    )


def _case(*, field_context: bool) -> CaseInput:
    return CaseInput(case_id="case-1", user_has_field_context=field_context)


def test_without_field_context_nothing_moves():
    packet = _packet()
    assert attest_one_tree(_case(field_context=False), packet) is packet


def test_with_two_trees_the_statement_is_ambiguous_and_nothing_moves():
    packet = _packet(trees=2)
    assert attest_one_tree(_case(field_context=True), packet) is packet


def test_one_tree_takes_the_loose_parts_as_user_attested():
    attested = attest_one_tree(_case(field_context=True), _packet())

    by_id = {o.observation_id: o for o in attested.observations}
    assert by_id["obs-nut"].subject_id == TREE
    assert by_id["obs-nut"].attachment is AttachmentStatus.USER_ATTESTED
    assert by_id["obs-leaf"].attachment is AttachmentStatus.USER_ATTESTED
    # Obscured evidence is not vouched for, and keeps its subject.
    assert by_id["obs-hidden"].subject_id == LOOSE
    assert by_id["obs-bark"].attachment is None
    assert {s.subject_id for s in attested.subjects} == {TREE, LOOSE}
    assert attested_evidence_ids(attested, TREE) == ("obs-nut", "obs-leaf")


def test_an_emptied_loose_subject_is_dropped_and_its_photos_go_to_the_tree():
    base = _packet()
    packet = base.model_copy(
        update={
            "observations": tuple(o for o in base.observations if o.observation_id != "obs-hidden")
        }
    )
    attested = attest_one_tree(_case(field_context=True), packet)

    assert [s.subject_id for s in attested.subjects] == [TREE]
    assert attested.subjects[0].image_ids == ("img-1", "img-2")


def test_attested_evidence_is_capped_support_not_decisive():
    attested = attest_one_tree(_case(field_context=True), _packet())
    nut = next(o for o in attested.observations if o.observation_id == "obs-nut")
    assert observation_trust(nut) is EvidenceTrust.CAPPED_POSITIVE

    detached = next(o for o in _packet().observations if o.observation_id == "obs-nut")
    assert observation_trust(detached) is EvidenceTrust.CONTEXT_ONLY


def test_a_model_cannot_claim_user_attested_itself():
    with pytest.raises(ValidationError, match="user_attested"):
        GeneratedObservation.model_validate(
            {
                "observation_id": "obs-1",
                "feature": "nut.shell_surface",
                "value": "deeply_corrugated_ridged",
                "subject_id": TREE,
                "source": "image",
                "image_id": "img-1",
                "attachment": "user_attested",
            }
        )
