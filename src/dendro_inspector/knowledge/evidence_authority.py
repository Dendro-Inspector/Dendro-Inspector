"""Whether the code has a structural reason to doubt who owns a piece of evidence.

This module holds one question and one answer, so that two very different consumers cannot
drift apart:

* the attachment authority gate asks *may this evidence carry the claim?*;
* the photo planner asks *which photograph would settle it?*.

Those must be the same evidence. A system that withholds a verdict for a reason it then
declines to photograph is asking the user to guess what it wants.

**Risk is not sensitivity.** Sensitivity says the verdict depends on this observation, and
nearly every honest organ-level identification is sensitive — a leaf deciding the answer is
what leaves are for. Risk says there is something in the packet that makes its *ownership*
doubtful. Only the second is a reason to hold a claim back.

**Risk is per observation, never per subject.** One unplaceable branch on the left of the
frame says nothing about needles correctly traced to the trunk on the right, and a packet-
level flag like ``possible_multiple_taxa`` is a reason to look at ownership rather than a
verdict on all of it. It therefore appears here only in conjunction with structure that is
specific to the observation being judged.

**``source_component_id`` carries no authority in either direction.** It is the deterministic
rename of a ``parent_subject_id`` the model proposed. Reading it as corroboration credits one
stochastic assertion as two witnesses; reading it as guilt punishes the extractor for
describing anatomy precisely. It is absent from every rule below, deliberately.

The limit this module cannot cross: when the extractor states ``confirmed_attached`` about a
crossing branch and flags no ambiguity, no rule here can contradict it. Nothing in the packet
disagrees. Closing that class needs an independent provenance reviewer, a continuity
photograph, or the user — not another clause in this file.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from dendro_inspector.knowledge.evidence_hierarchy import requires_attachment
from dendro_inspector.schemas.evidence import (
    AttachmentStatus,
    EvidencePacket,
    Observation,
    SubjectKind,
    Visibility,
)


class AuthorityRiskReason(StrEnum):
    """Why ownership is in doubt. Closed vocabulary, so it is countable across a benchmark."""

    OWNERSHIP_UNRESOLVED = "ownership_unresolved"
    COMPETING_OWNERSHIP = "competing_ownership"


@dataclass(frozen=True, slots=True)
class AuthorityRisk:
    """The observations whose ownership is structurally in doubt, and why."""

    observations: tuple[Observation, ...] = ()
    reasons: tuple[AuthorityRiskReason, ...] = ()

    @property
    def risky_evidence_ids(self) -> frozenset[str]:
        return frozenset(observation.observation_id for observation in self.observations)

    def __bool__(self) -> bool:
        return bool(self.observations)


#: Identity roots a detachable organ can be *credited to* rather than belong to.
_IDENTITY_TARGETS: frozenset[SubjectKind] = frozenset({SubjectKind.STANDING_TREE, SubjectKind.LOG})

#: Subject kinds that stand alone in the packet when the extractor could not place them.
_UNPLACED_KINDS: frozenset[SubjectKind] = frozenset({SubjectKind.BRANCH, SubjectKind.DETACHED_PART})


def attachment_risk_for(evidence: EvidencePacket, subject_id: str) -> AuthorityRisk:
    """Return the detachable observations whose ownership this subject cannot take for granted.

    Two structural signals, both visible in the packet without asking a model anything:

    ``ownership_unresolved``
        The extractor recorded the observation against this very subject and answered the
        attachment question with ``unknown``. It is the extractor's own statement that it
        could not place the evidence — the strongest risk signal the packet can carry.

    ``competing_ownership``
        The observation lives on an independent branch or detached part — its own identity
        root, not a component of this subject — while this subject is a tree or a log the
        evidence would be credited to, and the packet says the frame may hold more than one
        taxon. The organ is in the picture; which stem it grew on is unestablished.
    """
    by_subject = {subject.subject_id: subject for subject in evidence.subjects}
    target = by_subject.get(subject_id)
    risky: list[Observation] = []
    reasons: list[AuthorityRiskReason] = []

    for observation in evidence.observations:
        if not requires_attachment(observation.feature) or observation.visibility in (
            Visibility.OBSCURED,
            Visibility.NOT_VISIBLE,
        ):
            continue

        if (
            observation.subject_id == subject_id
            and observation.attachment is AttachmentStatus.UNKNOWN
        ):
            risky.append(observation)
            reasons.append(AuthorityRiskReason.OWNERSHIP_UNRESOLVED)
            continue

        source = by_subject.get(observation.subject_id)
        if (
            evidence.possible_multiple_taxa
            and target is not None
            and target.kind in _IDENTITY_TARGETS
            and source is not None
            and source.parent_subject_id is None
            and source.kind in _UNPLACED_KINDS
            and observation.attachment
            in (AttachmentStatus.UNKNOWN, AttachmentStatus.CONFIRMED_ATTACHED)
        ):
            risky.append(observation)
            reasons.append(AuthorityRiskReason.COMPETING_OWNERSHIP)

    return AuthorityRisk(
        observations=tuple(risky),
        reasons=tuple(dict.fromkeys(reasons)),
    )
