"""External reference verification for open-world candidates.

A source answers one question — *is this an accepted taxon, and what is it documented to
look like?* — and deterministic code turns the answer into a
:class:`~dendro_inspector.schemas.reference.ReferenceStatus`. Nothing here touches image
confidence, resolution or score: an accepted name with matching documented features says the
hypothesis is coherent with the literature, not that the photograph shows it.

Retrieved content is untrusted reference data. A source hands back
:class:`~dendro_inspector.schemas.reference.ReferenceRecord` objects, which carry tokens,
identifiers, timestamps and HTTPS URLs and no free text; a record that fails validation is
discarded whole. Records are never composed into a model prompt.

The only source shipped is :class:`RecordedReferenceSource`, which reads records an operator
has already retrieved and saved. It is offline by construction, and its trust is
:attr:`~dendro_inspector.schemas.reference.SourceTrust.OPERATOR_SUPPLIED`: nothing checks that
a saved record's content came from the URL it cites, so such a record can dispute a
hypothesis but never verify one. ``externally_verified`` is reserved for a source that
retrieves from an authoritative service itself. That live client is a separate integration
with its own authorization question (``AGENTS.md`` §2), and plugs in through
:class:`ReferenceSource` without changing the adjudication below.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

import yaml
from pydantic import ValidationError

from dendro_inspector.knowledge.evidence_hierarchy import (
    positive_observations_for,
    resolve_evidence_observations,
)
from dendro_inspector.observability.logging import get_logger
from dendro_inspector.schemas.candidates import OpenWorldCandidate
from dendro_inspector.schemas.evidence import EvidencePacket, is_positive_reading
from dendro_inspector.schemas.reference import (
    NameStatus,
    ReferenceCitation,
    ReferenceRecord,
    ReferenceStatus,
    SourceTrust,
)
from dendro_inspector.schemas.taxon import ValueVocabulary

_LOGGER = "references"


class ReferenceUnavailableError(RuntimeError):
    """A source could not be consulted. Recorded as ``unavailable``, never as "not found"."""


class ReferenceSource(Protocol):
    """Anything that can answer for a taxon id with zero or more reference records."""

    @property
    def source_id(self) -> str: ...

    @property
    def trust(self) -> SourceTrust: ...

    async def lookup(self, taxon: str) -> tuple[ReferenceRecord, ...]: ...


class RecordedReferenceSource:
    """Reference records previously retrieved and saved to a local YAML file.

    The file holds ``records:``, a list of mappings in the
    :class:`~dendro_inspector.schemas.reference.ReferenceRecord` shape. Each record keeps the
    ``source_id``, ``source_url`` and ``retrieved_at`` of wherever it was actually retrieved
    from, so one file can carry several sources and each still cites its own.

    Read on first lookup, not at construction: a missing or corrupt file is a source failure
    for this run's candidates, not a reason to refuse to start.
    """

    def __init__(self, path: Path, *, source_id: str = "recorded") -> None:
        self._path = path
        self._source_id = source_id
        self._records: tuple[ReferenceRecord, ...] | None = None
        self.rejected_records = 0

    @property
    def source_id(self) -> str:
        return self._source_id

    @property
    def trust(self) -> SourceTrust:
        """Fixed. A file cannot vouch for its own provenance, so none can raise this."""
        return SourceTrust.OPERATOR_SUPPLIED

    def _load(self) -> tuple[ReferenceRecord, ...]:
        if self._records is not None:
            return self._records
        try:
            loaded = yaml.safe_load(self._path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            msg = f"reference records could not be read from {self._path}: {exc}"
            raise ReferenceUnavailableError(msg) from exc
        entries = loaded.get("records") if isinstance(loaded, dict) else None
        if not isinstance(entries, list):
            msg = f"{self._path} must contain a mapping with a 'records' list"
            raise ReferenceUnavailableError(msg)

        records: list[ReferenceRecord] = []
        for entry in entries:
            try:
                records.append(ReferenceRecord.model_validate(entry))
            except ValidationError:
                # Discarded whole. A record that does not fit the contract may be truncated,
                # mis-mapped or hostile, and none of those is safe to use in part.
                self.rejected_records += 1
        if self.rejected_records:
            get_logger(_LOGGER).warning(
                "reference_records_rejected",
                extra={"path": str(self._path), "rejected": self.rejected_records},
            )
        self._records = tuple(records)
        return self._records

    async def lookup(self, taxon: str) -> tuple[ReferenceRecord, ...]:
        return tuple(record for record in self._load() if record.taxon == taxon)


def adjudicate_record(
    record: ReferenceRecord,
    candidate: OpenWorldCandidate,
    evidence: EvidencePacket,
    vocabulary: ValueVocabulary,
    trust: SourceTrust,
) -> ReferenceCitation:
    """Compare one record with the evidence. Deterministic; reads no model output.

    ``trust`` bounds the outcome in one direction only. Any source may dispute; only an
    authoritative retrieval may verify. A match from an operator-supplied record stays
    ``provisional``, with the matched features still recorded so the reader can see them.

    *Matched* features come from the candidate's own surviving support: the documented
    reading is what the hypothesis actually rests on. *Conflicting* features follow the same
    rule as a card's self-contradiction veto — a trusted same-subject reading of a
    documented path that no documented value is compatible with, on a path no other reading
    satisfied. Silence on a documented path is not a conflict.
    """
    if record.name_status is not NameStatus.ACCEPTED:
        return ReferenceCitation(
            source_id=record.source_id,
            source_trust=trust,
            source_url=record.source_url,
            retrieved_at=record.retrieved_at,
            outcome=ReferenceStatus.DISPUTED,
            reason=f"name_{record.name_status.value}",
            accepted_taxon=record.accepted_taxon,
        )

    documented = {item.feature: item.values for item in record.documented_features}

    def documented_reading(feature: str, value: str) -> bool:
        return any(
            vocabulary.compatible(feature, value, declared)
            for declared in documented.get(feature, ())
        )

    support = tuple(
        source
        for evidence_id in candidate.supporting_evidence_ids
        for source in resolve_evidence_observations(evidence, evidence_id, candidate.subject_id)
    )
    matched = sorted(
        {source.feature for source in support if documented_reading(source.feature, source.value)}
    )

    readings = tuple(
        observation
        for observation in positive_observations_for(evidence, candidate.subject_id)
        if observation.feature in documented and is_positive_reading(observation.value)
    )
    satisfied = {o.feature for o in readings if documented_reading(o.feature, o.value)}
    conflicting = sorted(
        {
            o.feature
            for o in readings
            if o.feature not in satisfied and not documented_reading(o.feature, o.value)
        }
    )

    if conflicting:
        outcome, reason = ReferenceStatus.DISPUTED, "documented_feature_conflict"
    elif matched and trust is SourceTrust.AUTHORITATIVE_RETRIEVAL:
        outcome, reason = ReferenceStatus.EXTERNALLY_VERIFIED, "documented_feature_match"
    elif matched:
        outcome, reason = ReferenceStatus.PROVISIONAL, "documented_feature_match_unauthenticated"
    else:
        outcome, reason = ReferenceStatus.PROVISIONAL, "no_documented_feature_observed"
    return ReferenceCitation(
        source_id=record.source_id,
        source_trust=trust,
        source_url=record.source_url,
        retrieved_at=record.retrieved_at,
        outcome=outcome,
        reason=reason,
        matched_features=tuple(matched),
        conflicting_features=tuple(conflicting),
    )


def combine_citations(citations: Sequence[ReferenceCitation]) -> ReferenceStatus:
    """One status from every source consulted. Disagreement wins over agreement.

    A single source that disputes the name or contradicts a documented feature outweighs
    any number that verify it, because the cost of the two errors is not symmetric: a
    wrongly disputed hypothesis is still reported, a wrongly verified one reads as settled.
    """
    outcomes = {citation.outcome for citation in citations}
    for status in (
        ReferenceStatus.DISPUTED,
        ReferenceStatus.EXTERNALLY_VERIFIED,
        ReferenceStatus.PROVISIONAL,
    ):
        if status in outcomes:
            return status
    return ReferenceStatus.UNAVAILABLE


async def verify_open_world(
    candidates: tuple[OpenWorldCandidate, ...],
    evidence: EvidencePacket,
    vocabulary: ValueVocabulary,
    sources: tuple[ReferenceSource, ...],
) -> tuple[OpenWorldCandidate, ...]:
    """Attach a reference status and its citations to every open-world candidate.

    With no source configured the status is ``unavailable``: nobody checked, which is a
    different fact from "checked and found nothing" (``provisional``). Only
    ``reference_status`` and ``references`` change; the evidence-bounded fields are copied
    through untouched. A covered name is passed through: local cards already describe it,
    and its status is theirs.
    """
    verified: list[OpenWorldCandidate] = []
    for candidate in candidates:
        if not candidate.uncovered:
            verified.append(candidate)
            continue
        citations: list[ReferenceCitation] = []
        for source in sources:
            try:
                records = await source.lookup(candidate.taxon)
            except ReferenceUnavailableError:
                citations.append(
                    ReferenceCitation(
                        source_id=source.source_id,
                        source_trust=source.trust,
                        outcome=ReferenceStatus.UNAVAILABLE,
                        reason="source_failed",
                    )
                )
                continue
            if not records:
                citations.append(
                    ReferenceCitation(
                        source_id=source.source_id,
                        source_trust=source.trust,
                        outcome=ReferenceStatus.PROVISIONAL,
                        reason="not_found",
                    )
                )
                continue
            citations.extend(
                adjudicate_record(record, candidate, evidence, vocabulary, source.trust)
                for record in records
            )
        # Re-validated rather than copied: `model_copy` skips validators, and the contract's
        # rule that a verified status needs a verifying citation is the one worth checking.
        verified.append(
            OpenWorldCandidate.model_validate(
                {
                    **candidate.model_dump(),
                    "reference_status": combine_citations(citations),
                    "references": tuple(citations),
                }
            )
        )
    return tuple(verified)
