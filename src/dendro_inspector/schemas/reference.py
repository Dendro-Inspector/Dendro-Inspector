"""Taxonomic reference status and external reference records.

Two questions about a hypothesis are kept apart here because they have different
evidence:

* **Does this taxon exist, and what is it documented to look like?** That is a question
  about reference data — a local card, or an external botanical source. Its answer is a
  :class:`ReferenceStatus`.
* **Does this photograph show it?** That is a question about the image, answered by the
  evidence tier and the card, and recorded as :class:`~dendro_inspector.schemas.taxon.Confidence`.

An external source can settle the first and never the second. A record confirming that
*Aesculus hippocastanum* is an accepted name with palmately compound leaves says nothing
about whether the leaf in this frame grows on this trunk, so nothing in this module can
raise image-identification confidence.

External records are untrusted reference data. Every field is a typed token, identifier,
timestamp or URL — there is deliberately no free-text field, so a record cannot carry an
instruction anywhere, and records are never composed into a model prompt. A record that
fails validation is discarded whole, never partially used.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import AwareDatetime, Field, StringConstraints, model_validator

from dendro_inspector.schemas.base import Contract, FeaturePath, Identifier, ValueToken
from dendro_inspector.schemas.taxon import ReviewState

#: HTTPS only, no whitespace, no credentials-in-URL ``user@host`` form. A citation the
#: reader cannot open over a secure channel is not a citation.
HTTPS_URL_PATTERN = r"^https://[A-Za-z0-9.\-]+(:[0-9]{1,5})?(/[A-Za-z0-9._~%!$&'()*+,;=:/?#\-]*)?$"
HttpsUrl = Annotated[str, StringConstraints(pattern=HTTPS_URL_PATTERN, max_length=400)]


def scientific_display_name(taxon: str) -> str:
    """``aesculus_hippocastanum`` -> ``Aesculus hippocastanum``. Presentation only.

    For a taxon with no card, so no declared display name, the identifier is the only name
    there is; it is rendered rather than looked up so no untrusted text is ever displayed.
    """
    genus, *rest = taxon.split("_")
    return " ".join((genus.capitalize(), *rest))


class ReferenceStatus(StrEnum):
    """Where a taxon's reference knowledge comes from, and whether anyone has checked it.

    Independent of image-identification confidence by construction: no transition in this
    enum is a reason to change a confidence band.
    """

    LOCAL_REVIEWED = "local_reviewed"
    """A local card whose provenance records a named reviewer."""
    LOCAL_UNREVIEWED = "local_unreviewed"
    """A local card that nobody has reviewed — every shipped card today."""
    EXTERNALLY_VERIFIED = "externally_verified"
    """No local card; an external source accepts the name and documents a feature observed
    here, with no documented feature contradicted."""
    PROVISIONAL = "provisional"
    """No local card; the hypothesis rests on observed evidence only."""
    DISPUTED = "disputed"
    """A source does not accept the name, a documented feature is contradicted, or sources
    disagree. Also a local card whose review state is disputed."""
    UNAVAILABLE = "unavailable"
    """No local card, and no external source could be consulted."""


#: Statuses only local knowledge can carry: a card, or an identity or alias a card declares.
LOCAL_STATUSES: frozenset[ReferenceStatus] = frozenset(
    {ReferenceStatus.LOCAL_REVIEWED, ReferenceStatus.LOCAL_UNREVIEWED}
)

_REVIEW_TO_REFERENCE: dict[ReviewState, ReferenceStatus] = {
    ReviewState.REVIEWED: ReferenceStatus.LOCAL_REVIEWED,
    ReviewState.UNREVIEWED: ReferenceStatus.LOCAL_UNREVIEWED,
    ReviewState.DISPUTED: ReferenceStatus.DISPUTED,
}

#: Most conservative first, for combining several local sources of one name.
_LOCAL_PRECEDENCE: tuple[ReferenceStatus, ...] = (
    ReferenceStatus.DISPUTED,
    ReferenceStatus.LOCAL_UNREVIEWED,
    ReferenceStatus.LOCAL_REVIEWED,
)


def local_reference_status(review_states: tuple[ReviewState, ...]) -> ReferenceStatus:
    """Reference status of local knowledge, from the review state its provenance records.

    Several states — one name declared by several cards — combine to the most conservative:
    a name is only as reviewed as its least-reviewed declaration.
    """
    statuses = {_REVIEW_TO_REFERENCE[state] for state in review_states}
    return next(
        (status for status in _LOCAL_PRECEDENCE if status in statuses),
        ReferenceStatus.LOCAL_UNREVIEWED,
    )


class SourceTrust(StrEnum):
    """How a reference record reached this system, which bounds what it may establish.

    Only a record this system retrieved itself from an authoritative service can verify a
    hypothesis. A saved file asserts its URLs and timestamps; nothing checks that the content
    came from there. It may still *dispute* — a source can withdraw trust it cannot grant,
    because the cost of the two errors is not symmetric.
    """

    OPERATOR_SUPPLIED = "operator_supplied"
    """Records someone saved and handed over. Content as asserted by the file."""
    AUTHORITATIVE_RETRIEVAL = "authoritative_retrieval"
    """Retrieved by this system, at lookup time, from the authoritative service it cites."""


class NameStatus(StrEnum):
    """What a source says about the name it was asked for."""

    ACCEPTED = "accepted"
    SYNONYM = "synonym"
    UNRESOLVED = "unresolved"


class DocumentedFeature(Contract):
    """A feature path and the values a source documents for it. Tokens only."""

    feature: FeaturePath
    values: tuple[ValueToken, ...] = Field(min_length=1)


class ReferenceRecord(Contract):
    """One source's answer about one taxon id."""

    taxon: Identifier
    source_id: Identifier
    source_url: HttpsUrl
    retrieved_at: AwareDatetime
    name_status: NameStatus
    accepted_taxon: Identifier
    documented_features: tuple[DocumentedFeature, ...] = ()
    lookalikes: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def _accepted_names_point_at_themselves(self) -> ReferenceRecord:
        accepted = self.name_status is NameStatus.ACCEPTED
        if accepted != (self.accepted_taxon == self.taxon):
            msg = (
                "accepted_taxon must equal taxon exactly when name_status is accepted; "
                f"got {self.taxon!r} -> {self.accepted_taxon!r} ({self.name_status.value})"
            )
            raise ValueError(msg)
        return self


#: Statuses a single external lookup can produce.
EXTERNAL_OUTCOMES: frozenset[ReferenceStatus] = frozenset(ReferenceStatus) - LOCAL_STATUSES


class ReferenceCitation(Contract):
    """What one source contributed to an open-world candidate's reference status.

    Recorded for every source consulted, including the ones that failed or knew nothing,
    so "nobody checked" and "checked, not found" stay distinguishable in the trace.
    """

    source_id: Identifier
    source_trust: SourceTrust
    outcome: ReferenceStatus
    reason: ValueToken
    source_url: HttpsUrl | None = None
    retrieved_at: AwareDatetime | None = None
    accepted_taxon: Identifier | None = Field(
        default=None,
        description="The name the source accepts, when it differs from the one proposed.",
    )
    matched_features: tuple[FeaturePath, ...] = ()
    conflicting_features: tuple[FeaturePath, ...] = ()

    @model_validator(mode="after")
    def _outcomes_are_external_and_cited(self) -> ReferenceCitation:
        if self.outcome not in EXTERNAL_OUTCOMES:
            msg = f"a reference citation cannot carry the local status {self.outcome.value!r}"
            raise ValueError(msg)
        if self.outcome is ReferenceStatus.EXTERNALLY_VERIFIED and (
            self.source_url is None or self.retrieved_at is None or not self.matched_features
        ):
            msg = (
                "externally_verified requires a source URL, a retrieval time and a matched feature"
            )
            raise ValueError(msg)
        if (
            self.outcome is ReferenceStatus.EXTERNALLY_VERIFIED
            and self.source_trust is not SourceTrust.AUTHORITATIVE_RETRIEVAL
        ):
            msg = (
                "externally_verified requires a record this system retrieved from an "
                f"authoritative service, not {self.source_trust.value!r}"
            )
            raise ValueError(msg)
        return self
