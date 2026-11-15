"""The uploader's word that a photographed part belongs to a photographed tree.

A single upload often holds the trunk, a leafy twig and the fruit picked up beneath the
tree. The extractor cannot see a fruit on the lawn connect to the trunk, so it records the
fruit as a detached part of its own, and the attachment rule rightly refuses to credit it:
"material on the ground belongs to whoever dropped it". For a stranger's photograph that is
the only safe reading.

The person who took the photographs was there. Domain prompt section 3 says the user may
know the fruits and may change the verdict with what they saw on site. That knowledge
reaches the evidence only as an explicit statement, a ``PartAttestation``: these photographs
show parts of the tree in those photographs. A reading it covers is credited to that tree as
``USER_ATTESTED``: counted, but capped (``evidence_hierarchy.observation_trust``), so it
supports an identification without being able to settle one or to carry high confidence.

The statement is read narrowly, because a wrong credit puts one tree's leaf on another
tree's trunk:

* It names its tree through photographs. Exactly one standing tree must be found in them;
  with none or several the statement names no tree and nothing is moved.
* It covers readings taken from the part photographs it names, filed under a loose part
  (or lying on the tree itself). A reading filed under another tree, a log or a wood sample
  is an identity of its own and is never folded in.
* It speaks to whose part this is, not to what the photograph shows. A reading the
  extractor could not place (``attachment: unknown`` — frame edge, background, a
  neighbouring branch), could not see, or did not trust stays where it was filed.

The general field-context flag (``CaseInput.user_has_field_context``) credits nothing here,
and neither does submitting several photographs together: neither says which part belongs
to which tree. Every statement leaves an ``AttestationOutcome``, so a credited reading can
be traced to the statement behind it and a refused one keeps its reason.

Nothing here happens on the model's say-so. The model cannot emit ``USER_ATTESTED``
(``GeneratedObservation`` rejects it).
"""

from __future__ import annotations

from collections.abc import Iterable

from dendro_inspector.knowledge.evidence_hierarchy import requires_attachment
from dendro_inspector.schemas.evidence import (
    AttachmentStatus,
    AttestationOutcome,
    AttestationSkipReason,
    AttestationStatus,
    CreditedObservation,
    EvidencePacket,
    Observation,
    ObservationSource,
    Reliability,
    SkippedObservation,
    Subject,
    SubjectKind,
    Visibility,
)
from dendro_inspector.schemas.input import CaseInput, PartAttestation

#: Loose parts a statement can credit to the tree. Logs, wood and other trees are identity
#: roots of their own and are never folded into it.
_LOOSE_KINDS = frozenset({SubjectKind.DETACHED_PART, SubjectKind.BRANCH, SubjectKind.UNKNOWN})


def apply_part_attestations(
    case: CaseInput, packet: EvidencePacket
) -> tuple[EvidencePacket, tuple[AttestationOutcome, ...]]:
    """Credit attested loose parts to their trees and say what each statement did."""
    if not case.part_attestations:
        return packet, ()

    roots = {
        subject.subject_id: subject
        for subject in packet.subjects
        if subject.parent_subject_id is None
    }
    observations = list(packet.observations)
    outcomes: list[AttestationOutcome] = []
    emptied_into: dict[str, str] = {}
    for attestation in case.part_attestations:
        tree, status = _named_tree(attestation, roots.values())
        credited: list[CreditedObservation] = []
        skipped: list[SkippedObservation] = []
        if tree is not None:
            for index, observation in enumerate(observations):
                verdict = _verdict(observation, attestation, tree, roots)
                if verdict is None:
                    continue
                if isinstance(verdict, AttestationSkipReason):
                    skipped.append(
                        SkippedObservation(
                            observation_id=observation.observation_id,
                            subject_id=observation.subject_id,
                            reason=verdict,
                        )
                    )
                    continue
                credited.append(
                    CreditedObservation(
                        observation_id=observation.observation_id,
                        from_subject_id=observation.subject_id,
                        read_as=verdict,
                    )
                )
                if observation.subject_id != tree.subject_id:
                    emptied_into[observation.subject_id] = tree.subject_id
                observations[index] = observation.model_copy(
                    update={
                        "subject_id": tree.subject_id,
                        "attachment": AttachmentStatus.USER_ATTESTED,
                    }
                )
            status = AttestationStatus.APPLIED if credited else AttestationStatus.NOTHING_CREDITED
        outcomes.append(
            AttestationOutcome(
                attestation_id=attestation.attestation_id,
                source=attestation.source.value,
                part_image_ids=attestation.part_image_ids,
                tree_image_ids=attestation.tree_image_ids,
                status=status,
                target_subject_id=tree.subject_id if tree is not None else None,
                credited=tuple(credited),
                skipped=tuple(skipped),
            )
        )

    if not any(outcome.credited for outcome in outcomes):
        return packet, tuple(outcomes)
    return _rebuilt(packet, observations, emptied_into), tuple(outcomes)


def _named_tree(
    attestation: PartAttestation, roots: Iterable[Subject]
) -> tuple[Subject | None, AttestationStatus]:
    """The one standing tree shown in the statement's tree photographs, if there is one."""
    named = set(attestation.tree_image_ids)
    trees = [
        subject
        for subject in roots
        if subject.kind is SubjectKind.STANDING_TREE and named.intersection(subject.image_ids)
    ]
    if not trees:
        return None, AttestationStatus.NO_TREE
    if len(trees) > 1:
        return None, AttestationStatus.AMBIGUOUS_TREE
    return trees[0], AttestationStatus.APPLIED


def _verdict(
    observation: Observation,
    attestation: PartAttestation,
    tree: Subject,
    roots: dict[str, Subject],
) -> AttachmentStatus | AttestationSkipReason | None:
    """What the statement does with one reading.

    ``None``: the statement does not concern it. A skip reason: it is in an attested
    photograph and stays where it was filed. An attachment status: it is credited, and this
    is what the extractor had reported.
    """
    if observation.source is not ObservationSource.IMAGE:
        return None
    if observation.image_id not in attestation.part_image_ids:
        return None
    if not requires_attachment(observation.feature) or observation.attachment is None:
        return None
    if observation.attachment is AttachmentStatus.USER_ATTESTED:
        return None

    on_tree = observation.subject_id == tree.subject_id
    if on_tree and observation.attachment is AttachmentStatus.CONFIRMED_ATTACHED:
        # Already the tree's own, at its own tier. The statement adds nothing.
        return None
    owner = roots.get(observation.subject_id)
    if not on_tree and (owner is None or owner.kind not in _LOOSE_KINDS):
        return AttestationSkipReason.OTHER_IDENTITY
    if observation.visibility in (Visibility.OBSCURED, Visibility.NOT_VISIBLE):
        return AttestationSkipReason.NOT_VISIBLE
    if observation.attachment is AttachmentStatus.UNKNOWN:
        return AttestationSkipReason.ATTACHMENT_UNKNOWN
    if observation.reliability is Reliability.LOW:
        return AttestationSkipReason.LOW_RELIABILITY
    return observation.attachment


def _rebuilt(
    packet: EvidencePacket, observations: list[Observation], emptied_into: dict[str, str]
) -> EvidencePacket:
    """The packet with credited readings moved and emptied loose parts dropped.

    A loose-part subject is dropped only once nothing points at it any more; its
    photographs become the tree's, so the image scope of the case is unchanged.
    """
    still_used = {observation.subject_id for observation in observations}
    still_used.update(s.parent_subject_id for s in packet.subjects if s.parent_subject_id)
    gained: dict[str, list[str]] = {}
    for subject in packet.subjects:
        tree_id = emptied_into.get(subject.subject_id)
        if tree_id is not None and subject.subject_id not in still_used:
            gained.setdefault(tree_id, []).extend(subject.image_ids)
    dropped = {subject_id for subject_id in emptied_into if subject_id not in still_used}
    subjects = []
    for subject in packet.subjects:
        if subject.subject_id in dropped:
            continue
        extra = gained.get(subject.subject_id)
        if extra:
            merged = tuple(dict.fromkeys((*subject.image_ids, *extra)))
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
