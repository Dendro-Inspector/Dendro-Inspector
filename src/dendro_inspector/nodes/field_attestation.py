"""The uploader's word that every photograph shows one tree.

A single upload often holds the trunk, a leafy twig and the fruit picked up beneath the
tree. The extractor cannot see a fruit on the lawn connect to the trunk, so it records the
fruit as a detached part of its own, and the attachment rule rightly refuses to credit it:
"material on the ground belongs to whoever dropped it". For a stranger's photograph that is
the only safe reading.

The person who took the photographs was there. Domain prompt section 3 says the user may
know the fruits and may change the verdict with what they saw on site. When the upload
asserts field context and the packet holds exactly one standing tree, the visible detachable
evidence in the case is credited to that tree as ``USER_ATTESTED``: counted, but capped
(``evidence_hierarchy.observation_trust``), so it supports an identification without being
able to settle one or to carry high confidence. Loose-part subjects left empty are dropped,
so the answer describes one tree.

Nothing here happens on the model's say-so. The model cannot emit ``USER_ATTESTED``
(``GeneratedObservation`` rejects it), and with two trees in frame the statement is
ambiguous and nothing is moved.
"""

from __future__ import annotations

from dendro_inspector.knowledge.evidence_hierarchy import requires_attachment
from dendro_inspector.schemas.evidence import (
    AttachmentStatus,
    EvidencePacket,
    SubjectKind,
    Visibility,
)
from dendro_inspector.schemas.input import CaseInput

#: Loose parts the uploader's statement can credit to the tree. Logs, wood and other trees
#: are identity roots of their own and are never folded into it.
_LOOSE_KINDS = frozenset({SubjectKind.DETACHED_PART, SubjectKind.BRANCH, SubjectKind.UNKNOWN})


def attest_one_tree(case: CaseInput, packet: EvidencePacket) -> EvidencePacket:
    """Credit loose detachable evidence to the single tree the uploader vouched for."""
    if not case.user_has_field_context:
        return packet
    roots = [subject for subject in packet.subjects if subject.parent_subject_id is None]
    trees = [subject for subject in roots if subject.kind is SubjectKind.STANDING_TREE]
    if len(trees) != 1:
        return packet
    tree = trees[0].subject_id
    loose = {subject.subject_id for subject in roots if subject.kind in _LOOSE_KINDS}

    moved_from: set[str] = set()
    observations = []
    for observation in packet.observations:
        movable = (
            requires_attachment(observation.feature)
            and observation.attachment is not AttachmentStatus.CONFIRMED_ATTACHED
            and observation.visibility not in (Visibility.OBSCURED, Visibility.NOT_VISIBLE)
            and (observation.subject_id == tree or observation.subject_id in loose)
        )
        if not movable:
            observations.append(observation)
            continue
        if observation.subject_id != tree:
            moved_from.add(observation.subject_id)
        observations.append(
            observation.model_copy(
                update={"subject_id": tree, "attachment": AttachmentStatus.USER_ATTESTED}
            )
        )
    if not moved_from and observations == list(packet.observations):
        return packet

    # A loose-part subject is dropped only once nothing points at it any more; its
    # photographs become the tree's, so the image scope of the case is unchanged.
    still_used = {observation.subject_id for observation in observations}
    still_used.update(s.parent_subject_id for s in packet.subjects if s.parent_subject_id)
    dropped = moved_from - still_used
    gained = tuple(
        image_id
        for subject in packet.subjects
        if subject.subject_id in dropped
        for image_id in subject.image_ids
    )
    subjects = []
    for subject in packet.subjects:
        if subject.subject_id in dropped:
            continue
        if subject.subject_id == tree and gained:
            merged = tuple(dict.fromkeys((*subject.image_ids, *gained)))
            subject = subject.model_copy(update={"image_ids": merged})
        subjects.append(subject)
    payload = packet.model_dump(mode="python")
    payload["observations"] = [
        observation.model_dump(mode="python") for observation in observations
    ]
    payload["subjects"] = [subject.model_dump(mode="python") for subject in subjects]
    return EvidencePacket.model_validate(payload)


def attested_evidence_ids(packet: EvidencePacket, subject_id: str) -> tuple[str, ...]:
    """Observations credited to this subject on the uploader's word."""
    return tuple(
        observation.observation_id
        for observation in packet.observations_for(subject_id)
        if observation.attachment is AttachmentStatus.USER_ATTESTED
    )
