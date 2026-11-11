"""Offline evidence-coverage audit: what each observation could and could not do.

The quality gate's :class:`~dendro_inspector.schemas.evidence.KnowledgeCoverage` answers one
question per run — how much trusted evidence fell outside every card. This module answers the
question a card author asks next, one observation at a time: *why* did this reading carry
nothing, and would a card edit change that? It reuses the same trust projection, the same
card vocabulary and the same admission functions the graph uses, so it can never disagree
with a live run about what was admissible. It decides nothing and is never on the graph's
path.

A coverage finding is not a botanical finding. ``valid_but_uncovered`` says the cards cannot
represent a reading; it does not say the reading is diagnostic of anything, or that the taxon
the model proposed with it is right. Whether a reading deserves a card rule is a question
for a source, not for this report (``AGENTS.md`` section 16).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum

from dendro_inspector.knowledge.candidate_validation import (
    support_is_colour_only,
    validate_candidate_set_with_report,
)
from dendro_inspector.knowledge.comparison_cards import INSUFFICIENT_ALONE, NON_DIAGNOSTIC_CONTEXT
from dendro_inspector.knowledge.evidence_hierarchy import (
    EvidenceTier,
    EvidenceTrust,
    family_of,
    is_colour_feature,
    known_families,
    positive_observations_for,
    project_observation,
    requires_attachment,
)
from dendro_inspector.knowledge.loader import KnowledgeBase
from dendro_inspector.knowledge.taxon_cards import (
    card_value_vocabulary,
    self_contradiction_hits,
)
from dendro_inspector.schemas.candidates import CandidateSet
from dendro_inspector.schemas.evidence import (
    AttachmentStatus,
    EvidencePacket,
    Observation,
    is_positive_reading,
)
from dendro_inspector.schemas.taxon import Resolution, TaxonCard


class CoverageStatus(StrEnum):
    """What one observation could do against today's knowledge base, most useful first."""

    VALID_AND_SUPPORTED = "valid_and_supported"
    """A trusted reading some card declares as strong or supporting evidence."""
    CONTRADICTORY_EVIDENCE = "contradictory_evidence"
    """Supports no card, but contradicts at least one, explicitly or on its own terms."""
    AMBIGUOUS_MAPPING = "ambiguous_mapping"
    """Not declared as read, but the same token is declared under another feature path, or
    the shared vocabulary relates it to a declared value. A naming question for an author."""
    VALID_BUT_UNCOVERED = "valid_but_uncovered"
    """A trusted reading of a known organ that no card declares. A coverage question."""
    INVALID_VOCABULARY = "invalid_vocabulary"
    """Names an organ (feature family) neither the cards nor the evidence hierarchy know."""
    SUBJECT_ATTACHMENT_UNCERTAIN = "subject_attachment_uncertain"
    """Detachable evidence not shown to belong to its subject; it supports nothing."""
    CONTEXT_ONLY = "context_only"
    """Site, season or other context: informs plausibility, never supports a taxon."""
    UNTRUSTED_READING = "untrusted_reading"
    """Read at a reliability or visibility too low to support an identification."""
    NOT_A_READING = "not_a_reading"
    """The value reports a failure to read the feature (``unresolved``, ``not_visible``)."""


class CandidateOutcome(StrEnum):
    """What admission did to one proposed taxon, with the first condition that failed."""

    ADMITTED = "admitted"
    NO_CARD = "no_card"
    NO_CITED_SUPPORT = "no_cited_support"
    ALL_SUPPORT_DROPPED = "all_support_dropped"
    COLOUR_ONLY = "colour_only"
    SELF_CONTRADICTION = "self_contradiction"


@dataclass(frozen=True, slots=True)
class CardRole:
    """One card's use of an observation."""

    taxon_id: str
    role: str  # "strong" | "supporting" | "contradiction" | "self_contradiction"
    genus: str | None


@dataclass(frozen=True, slots=True)
class ObservationAudit:
    observation_id: str
    subject_id: str
    feature: str
    value: str
    status: CoverageStatus
    reason: str
    roles: tuple[CardRole, ...] = ()

    @property
    def supporting_genera(self) -> tuple[str, ...]:
        """Distinct genera this reading supports. More than one: it discriminates none."""
        return tuple(
            dict.fromkeys(
                role.genus or role.taxon_id
                for role in self.roles
                if role.role in ("strong", "supporting")
            )
        )

    @property
    def diagnostic_for(self) -> tuple[str, ...]:
        """Cards that call this reading strong-positive."""
        return tuple(role.taxon_id for role in self.roles if role.role == "strong")


@dataclass(frozen=True, slots=True)
class CandidateAudit:
    subject_id: str
    taxon: str
    outcome: CandidateOutcome
    cited: tuple[str, ...]
    surviving: tuple[str, ...]
    dropped: tuple[tuple[str, str], ...] = ()
    """``(evidence_id, reason)`` for each cited id admission removed."""
    detail: str = ""


@dataclass(frozen=True, slots=True)
class CoverageAudit:
    observations: tuple[ObservationAudit, ...]
    candidates: tuple[CandidateAudit, ...] = field(default=())

    def by_status(self) -> dict[str, int]:
        counts: dict[str, int] = {status.value: 0 for status in CoverageStatus}
        for audit in self.observations:
            counts[audit.status.value] += 1
        return counts


def _genus(card: TaxonCard) -> str | None:
    identity = card.identity_at_or_broader(Resolution.GENUS)
    return identity.taxon_id if identity is not None else None


def _roles(observation: Observation, cards: Iterable[TaxonCard]) -> tuple[CardRole, ...]:
    roles = []
    for card in cards:
        genus = _genus(card)
        for role, expectations in (
            ("strong", card.strong_positive_features),
            ("supporting", card.supporting_features),
            ("contradiction", card.contradictions),
        ):
            if any(
                observation.feature == expectation.feature
                and observation.value in expectation.values
                for expectation in expectations
            ):
                roles.append(CardRole(card.taxon_id, role, genus))
        if self_contradiction_hits(card, (observation,)):
            roles.append(CardRole(card.taxon_id, "self_contradiction", genus))
    return tuple(roles)


def _cards(knowledge: KnowledgeBase) -> tuple[TaxonCard, ...]:
    return tuple(
        card
        for taxon_id in knowledge.available_taxon_ids()
        if (card := knowledge.try_taxon(taxon_id)) is not None
    )


def audit_observation(
    observation: Observation,
    knowledge: KnowledgeBase,
    cards: tuple[TaxonCard, ...] | None = None,
) -> ObservationAudit:
    """Classify one observation; the reason names the exact condition that decided it."""
    cards = cards if cards is not None else _cards(knowledge)
    vocabulary = card_value_vocabulary(cards)
    families = {family_of(feature) for feature in vocabulary}
    roles = _roles(observation, cards)
    supports = [role for role in roles if role.role in ("strong", "supporting")]
    # Only a card's *explicit* contradictions make a reading contradictory evidence. Differing
    # from some card's strong value (``self_contradiction``) is recorded in ``roles`` but is
    # not a status: nearly every bark reading differs from some card's bark, and letting that
    # decide the status hid the coverage gap the same reading also exposes.
    contradicts = [role for role in roles if role.role == "contradiction"]
    disagrees = sorted({role.taxon_id for role in roles if role.role == "self_contradiction"})
    also = f"; differs from the strong path of {', '.join(disagrees)}" if disagrees else ""

    def audit(status: CoverageStatus, reason: str) -> ObservationAudit:
        return ObservationAudit(
            observation.observation_id,
            observation.subject_id,
            observation.feature,
            observation.value,
            status,
            reason,
            roles,
        )

    if not is_positive_reading(observation.value):
        return audit(CoverageStatus.NOT_A_READING, "value reports a failure to read")
    projection = project_observation(observation)
    organ = family_of(observation.feature)
    if organ not in families and organ not in known_families():
        return audit(CoverageStatus.INVALID_VOCABULARY, f"no card or tier describes {organ!r}")
    if (
        projection.tier is EvidenceTier.CONTEXT
        and projection.trust is not EvidenceTrust.CONTEXT_ONLY
    ):
        return audit(CoverageStatus.CONTEXT_ONLY, "context tier: never identification evidence")
    if not projection.supports_identification:
        unattached = requires_attachment(observation.feature) and observation.attachment not in (
            AttachmentStatus.CONFIRMED_ATTACHED,
            AttachmentStatus.USER_ATTESTED,
        )
        if unattached:
            return audit(
                CoverageStatus.SUBJECT_ATTACHMENT_UNCERTAIN,
                f"detachable feature with attachment "
                f"{observation.attachment.value if observation.attachment else 'unrecorded'}",
            )
        return audit(
            CoverageStatus.UNTRUSTED_READING,
            f"trust {projection.trust.name.lower()} "
            f"(visibility {observation.visibility.value}, "
            f"reliability {observation.reliability.value})",
        )
    if supports:
        genera = {role.genus or role.taxon_id for role in supports}
        kind = "strong" if any(role.role == "strong" for role in supports) else "supporting"
        return audit(
            CoverageStatus.VALID_AND_SUPPORTED,
            f"{kind} on {len(supports)} card(s) across {len(genera)} genus/genera",
        )
    if contradicts:
        return audit(
            CoverageStatus.CONTRADICTORY_EVIDENCE,
            "contradicts " + ", ".join(sorted({role.taxon_id for role in contradicts})),
        )
    misfiled = sorted(
        feature
        for feature, values in vocabulary.items()
        if feature != observation.feature
        and family_of(feature) == family_of(observation.feature)
        and observation.value in values
    )
    if misfiled:
        return audit(
            CoverageStatus.AMBIGUOUS_MAPPING,
            f"value declared under {', '.join(misfiled)}, not {observation.feature}{also}",
        )
    related = sorted(
        value
        for value in vocabulary.get(observation.feature, frozenset())
        if knowledge.vocabulary().compatible(observation.feature, observation.value, value)
    )
    if related:
        return audit(
            CoverageStatus.AMBIGUOUS_MAPPING,
            f"vocabulary relates it to declared {', '.join(related)}; relations never match",
        )
    if observation.feature in NON_DIAGNOSTIC_CONTEXT:
        return audit(CoverageStatus.CONTEXT_ONLY, "describes the site or trunk age, not the taxon")
    if organ not in families:
        return audit(
            CoverageStatus.VALID_BUT_UNCOVERED, f"no card describes the organ {organ!r}{also}"
        )
    if observation.feature in INSUFFICIENT_ALONE or is_colour_feature(observation.feature):
        return audit(
            CoverageStatus.VALID_BUT_UNCOVERED, f"colour: insufficient alone by policy{also}"
        )
    if observation.feature not in vocabulary:
        return audit(CoverageStatus.VALID_BUT_UNCOVERED, f"feature absent from all cards{also}")
    return audit(CoverageStatus.VALID_BUT_UNCOVERED, f"value not declared on any card{also}")


def audit_candidates(
    evidence: EvidencePacket,
    proposals: Iterable[CandidateSet],
    knowledge: KnowledgeBase,
) -> tuple[CandidateAudit, ...]:
    """Admit each proposal with today's rules and name the first condition that failed."""
    audits: list[CandidateAudit] = []
    for proposal in proposals:
        report = validate_candidate_set_with_report(proposal, evidence, knowledge)
        admitted = {candidate.taxon: candidate for candidate in report.candidate_set.ordered}
        details: dict[str, list[tuple[str, str]]] = {}
        for detail in report.dropped_support_details:
            reason: str = detail.reason
            if detail.feature:
                reason = f"{reason}: {detail.feature}={detail.value}"
            details.setdefault(detail.taxon, []).append((detail.evidence_id, reason))
        positive = positive_observations_for(evidence, proposal.subject_id)
        for candidate in proposal.ordered:
            cited = tuple(dict.fromkeys(candidate.supporting_evidence_ids))
            dropped = tuple(details.get(candidate.taxon, ()))
            dropped_ids = {evidence_id for evidence_id, _ in dropped}
            surviving = tuple(
                evidence_id for evidence_id in cited if evidence_id not in dropped_ids
            )
            card = knowledge.try_taxon(candidate.taxon)
            outcome, detail_text = CandidateOutcome.ADMITTED, ""
            if candidate.taxon in admitted:
                surviving = admitted[candidate.taxon].supporting_evidence_ids
            elif card is None:
                outcome = CandidateOutcome.NO_CARD
                detail_text = "no taxon card; at most an open-world hypothesis"
            elif not cited:
                outcome = CandidateOutcome.NO_CITED_SUPPORT
                detail_text = "the proposal cited no evidence ids"
            elif not surviving:
                outcome = CandidateOutcome.ALL_SUPPORT_DROPPED
            elif support_is_colour_only(surviving, evidence, proposal.subject_id):
                outcome = CandidateOutcome.COLOUR_ONLY
            else:
                hits = self_contradiction_hits(card, positive)
                outcome = CandidateOutcome.SELF_CONTRADICTION
                detail_text = "contradicted on its own strong-positive path by " + ", ".join(hits)
            audits.append(
                CandidateAudit(
                    proposal.subject_id,
                    candidate.taxon,
                    outcome,
                    cited,
                    surviving,
                    dropped,
                    detail_text,
                )
            )
    return tuple(audits)


def audit_packet(
    evidence: EvidencePacket,
    knowledge: KnowledgeBase,
    proposals: Iterable[CandidateSet] = (),
) -> CoverageAudit:
    """Audit every observation, and every proposal if any are given."""
    cards = _cards(knowledge)
    return CoverageAudit(
        observations=tuple(
            audit_observation(observation, knowledge, cards)
            for observation in evidence.observations
        ),
        candidates=audit_candidates(evidence, proposals, knowledge),
    )
