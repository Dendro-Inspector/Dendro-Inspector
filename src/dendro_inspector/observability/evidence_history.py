"""Versioned evidence artifacts retained alongside the compact final trace ledger.

References are revision-local: ``evidence:2/obs-1`` is distinct from ``evidence:1/obs-1``.
The extractor's IDs never need to change, and earlier findings keep their original meaning.
"""

from __future__ import annotations

import hashlib
import json

from pydantic import Field, model_validator

from dendro_inspector.schemas.base import Contract
from dendro_inspector.schemas.candidates import CandidateProposal
from dendro_inspector.schemas.evidence import EvidencePacket, GeneratedEvidencePacket


def packet_digest(packet: EvidencePacket) -> str:
    """Hash the complete validated artifact, including notes and original attribution."""
    encoded = json.dumps(
        packet.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class EvidenceRevision(Contract):
    """One extraction before and after deterministic normalization and attestation.

    Raw observations retain regions, notes, component subjects and their original attachment.
    The effective packet is exactly what downstream decision code received. A missing raw
    packet denotes a controlled empty extraction or a legacy recorder caller, never an
    invented provider response. Image bytes, local file paths and case metadata are absent.
    """

    schema_version: int = Field(default=1, ge=1, le=1)
    revision: int = Field(ge=1)
    retry_index: int = Field(default=0, ge=0)
    raw: GeneratedEvidencePacket | None = None
    effective: EvidencePacket
    raw_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    effective_sha256: str = Field(min_length=64, max_length=64)
    candidate_proposal: CandidateProposal | None = None

    @model_validator(mode="after")
    def _content_matches_hashes(self) -> EvidenceRevision:
        if self.effective_sha256 != packet_digest(self.effective):
            raise ValueError("effective evidence artifact does not match its SHA-256")
        expected = packet_digest(self.raw) if self.raw is not None else None
        if self.raw_sha256 != expected:
            raise ValueError("raw evidence artifact does not match its SHA-256")
        return self

    def reference(self, observation_id: str) -> str:
        return f"evidence:{self.revision}/{observation_id}"
