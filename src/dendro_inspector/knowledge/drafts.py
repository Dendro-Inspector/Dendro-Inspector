"""Draft taxon cards from external reference records, into a review queue.

A verified open-world discovery is the cheapest moment to start a card: the accepted name,
the source and the documented morphology are already in hand. A draft is still only a
proposal, and three properties keep it one:

* it is written to ``knowledge/review-queue/``, which the loader never reads — only
  ``knowledge/taxa/`` is retrievable, so a draft cannot open a candidate or reach a verdict
  until a person moves it there;
* it is always ``review_state: unreviewed`` and ``placeholder_content: true``, whatever the
  source said. Nothing here can mark knowledge reviewed;
* every documented feature becomes a *supporting* rule, never a strong one. Which features
  are decisive is a dendrological judgement a reference record does not make, and a strong
  rule is what lets a card reach ``strong`` support and veto look-alikes.

Each rule carries the record's URL as its provenance, so a reviewer can check the claim at
its source. ``AGENTS.md`` §16 still applies after promotion: a card is never tuned against a
golden case, and the draft's origin in a run does not change that.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from dendro_inspector.schemas.reference import NameStatus, ReferenceRecord, scientific_display_name
from dendro_inspector.schemas.taxon import (
    Confidence,
    FeatureExpectation,
    Provenance,
    Resolution,
    ReviewState,
    SourceType,
    TaxonCard,
    TaxonIdentity,
)

#: Relative to the knowledge root. Deliberately not ``taxa/``.
REVIEW_QUEUE_DIR = "review-queue"

_HEADER = (
    "# DRAFT — generated from an external reference record, not reviewed by anyone.\n"
    "# The loader does not read this directory. Review every rule against its source, decide\n"
    "# which features are decisive, then move the file to knowledge/taxa/ in its own change.\n"
)


class DraftCardError(ValueError):
    """The record cannot honestly seed a card."""


def _provenance(record: ReferenceRecord) -> Provenance:
    return Provenance(
        source=record.source_url,
        source_type=SourceType.LITERATURE,
        confidence=Confidence.LOW,
        review_state=ReviewState.UNREVIEWED,
    )


def draft_card(record: ReferenceRecord, *, known_taxon_ids: frozenset[str]) -> TaxonCard:
    """Build an unreviewed placeholder card from one accepted reference record.

    ``known_taxon_ids`` are the cards that already exist: a draft for one of them is
    refused, and only look-alikes that have a card are kept as ``common_confusions``.
    """
    if record.name_status is not NameStatus.ACCEPTED:
        msg = (
            f"{record.taxon}: the source lists this name as {record.name_status.value} "
            f"(accepted: {record.accepted_taxon}); draft the accepted name instead"
        )
        raise DraftCardError(msg)
    if record.taxon in known_taxon_ids:
        msg = f"{record.taxon}: a card already exists; edit it rather than drafting a second"
        raise DraftCardError(msg)
    if not record.documented_features:
        msg = f"{record.taxon}: the record documents no features, so a draft would cite nothing"
        raise DraftCardError(msg)

    provenance = _provenance(record)
    genus, _, epithet = record.taxon.partition("_")
    native = Resolution.SPECIES if epithet else Resolution.GENUS
    broader = (
        (
            TaxonIdentity(
                resolution=Resolution.GENUS,
                taxon_id=genus,
                display_name=scientific_display_name(genus),
                provenance=provenance,
            ),
        )
        if epithet
        else ()
    )
    return TaxonCard(
        taxon_id=record.taxon,
        display_name=scientific_display_name(record.taxon),
        native_resolution=native,
        broader_identities=broader,
        supported_resolution=(native,),
        supporting_features=tuple(
            FeatureExpectation(feature=feature, values=values, provenance=provenance)
            for feature, values in record.documented_values().items()
        ),
        common_confusions=tuple(
            lookalike for lookalike in record.lookalikes if lookalike in known_taxon_ids
        ),
        provenance=provenance,
        placeholder_content=True,
    )


def render_draft(card: TaxonCard) -> str:
    """YAML in the shape of a hand-written card, with the draft header on top.

    ``value_vocabulary`` is omitted: it is composed by the loader from the shared
    vocabulary file, and a card that declared its own fails a contract test.
    """
    data = card.model_dump(mode="json", exclude={"value_vocabulary"}, exclude_none=True)
    return _HEADER + yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def write_draft(card: TaxonCard, knowledge_root: Path) -> Path:
    """Write the draft into the review queue. Never overwrites an existing draft."""
    if card.provenance.review_state is not ReviewState.UNREVIEWED or not card.placeholder_content:
        msg = f"{card.taxon_id}: only unreviewed placeholder drafts enter the review queue"
        raise DraftCardError(msg)
    queue = knowledge_root / REVIEW_QUEUE_DIR
    queue.mkdir(parents=True, exist_ok=True)
    target = queue / f"{card.taxon_id}.yaml"
    if target.exists():
        msg = f"{target} already exists; review or remove it first"
        raise DraftCardError(msg)
    target.write_text(render_draft(card), encoding="utf-8")
    return target
