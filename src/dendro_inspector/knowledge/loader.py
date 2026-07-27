"""Knowledge loading.

Taxon cards are data. The loader is lazy and per-taxon on purpose: pushing the whole
catalogue into every request is how a knowledge base turns into an expensive way to
confuse a model. Nodes ask for the handful of taxa actually in play.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from dendro_inspector.config import KnowledgeConfig
from dendro_inspector.schemas.taxon import ComparisonCard, RegionalPack, TaxonCard


class KnowledgeError(RuntimeError):
    """Raised when a knowledge file is missing or malformed."""


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        msg = f"knowledge file not found: {path}"
        raise KnowledgeError(msg)
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        msg = f"invalid YAML in {path}: {exc}"
        raise KnowledgeError(msg) from exc
    if not isinstance(loaded, dict):
        msg = f"{path} must contain a YAML mapping, got {type(loaded).__name__}"
        raise KnowledgeError(msg)
    return loaded


class KnowledgeBase:
    """Lazy, cached access to taxon cards, comparison cards and regional packs."""

    def __init__(self, config: KnowledgeConfig, *, root: Path | None = None) -> None:
        base = root or Path.cwd()
        self._root = config.root if config.root.is_absolute() else base / config.root
        self._region_id = config.region_pack
        self._taxa: dict[str, TaxonCard] = {}
        self._comparisons: dict[str, ComparisonCard] = {}
        self._region: RegionalPack | None = None

    @property
    def root(self) -> Path:
        return self._root

    def taxon(self, taxon_id: str) -> TaxonCard:
        """Load exactly one card."""
        if taxon_id not in self._taxa:
            card = TaxonCard.model_validate(_read_yaml(self._root / "taxa" / f"{taxon_id}.yaml"))
            if card.taxon_id != taxon_id:
                msg = f"taxon card {taxon_id}.yaml declares taxon_id={card.taxon_id!r}"
                raise KnowledgeError(msg)
            self._taxa[taxon_id] = card
        return self._taxa[taxon_id]

    def taxa(self, taxon_ids: tuple[str, ...]) -> tuple[TaxonCard, ...]:
        """Load only the requested cards, in the requested order."""
        return tuple(self.taxon(taxon_id) for taxon_id in taxon_ids)

    def try_taxon(self, taxon_id: str) -> TaxonCard | None:
        """Return the card, or ``None`` when the project has no knowledge of that taxon."""
        try:
            return self.taxon(taxon_id)
        except KnowledgeError:
            return None

    def comparison(self, comparison_id: str) -> ComparisonCard:
        if comparison_id not in self._comparisons:
            self._comparisons[comparison_id] = ComparisonCard.model_validate(
                _read_yaml(self._root / "comparisons" / f"{comparison_id}.yaml")
            )
        return self._comparisons[comparison_id]

    def comparisons_for(self, taxon_ids: frozenset[str]) -> tuple[ComparisonCard, ...]:
        """Return comparison cards that mention at least two of ``taxon_ids``."""
        found: list[ComparisonCard] = []
        for path in sorted(self.available_comparison_ids()):
            card = self.comparison(path)
            if len(set(card.taxa) & taxon_ids) >= 2:
                found.append(card)
        return tuple(found)

    def region(self) -> RegionalPack | None:
        if self._region_id is None:
            return None
        if self._region is None:
            self._region = RegionalPack.model_validate(
                _read_yaml(self._root / "regions" / f"{self._region_id}.yaml")
            )
        return self._region

    def available_taxon_ids(self) -> tuple[str, ...]:
        return tuple(sorted(path.stem for path in (self._root / "taxa").glob("*.yaml")))

    def available_comparison_ids(self) -> tuple[str, ...]:
        return tuple(sorted(path.stem for path in (self._root / "comparisons").glob("*.yaml")))

    def follow_up_for(self, taxon_ids: tuple[str, ...]) -> tuple[str, ...]:
        """Union of follow-up evidence suggestions across the given taxa, order-stable."""
        seen: list[str] = []
        for taxon_id in taxon_ids:
            card = self.try_taxon(taxon_id)
            if card is None:
                continue
            for item in card.follow_up_evidence:
                if item not in seen:
                    seen.append(item)
        return tuple(seen)


@lru_cache(maxsize=8)
def _cached_base(root: Path, region: str | None) -> KnowledgeBase:
    return KnowledgeBase(KnowledgeConfig(root=root, region_pack=region))


def load_knowledge(config: KnowledgeConfig, *, root: Path | None = None) -> KnowledgeBase:
    """Build a knowledge base. Cached per (root, region) for process-lifetime reuse."""
    base = root or Path.cwd()
    resolved = config.root if config.root.is_absolute() else base / config.root
    return _cached_base(resolved, config.region_pack)
