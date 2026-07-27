"""The feature-family tables in two modules must agree about what counts as wood.

`_FAMILY_TIERS` (evidence_hierarchy) decides how strong a feature is. `_WOOD_SURFACE_FAMILIES`
(schemas.evidence) decides whether an observation must declare the physical surface it was
made on. They are edited independently, and a wood family added to the tier table but not to
the surface table would silently keep full wood-cut authority while skipping the surface
contract entirely — the exact failure the surface provenance work exists to prevent.

Nothing at runtime couples the two lists, so the coupling is asserted here.
"""

from __future__ import annotations

import pytest

from evil_duck_dendro.knowledge.evidence_hierarchy import (
    _FAMILY_TIERS,
    _PREPARED_END_GRAIN_PREFIXES,
    EvidenceTier,
    family_of,
    tier_of_feature,
)
from evil_duck_dendro.schemas.evidence import _WOOD_SURFACE_FAMILIES, requires_wood_surface

pytestmark = pytest.mark.contract


def _wood_cut_families() -> frozenset[str]:
    return frozenset(family for family, tier in _FAMILY_TIERS if tier is EvidenceTier.WOOD_CUT)


def test_every_wood_cut_family_carries_the_surface_contract():
    missing = _wood_cut_families() - _WOOD_SURFACE_FAMILIES
    assert not missing, (
        f"families tiered WOOD_CUT without a surface contract: {sorted(missing)}. "
        "Add them to _WOOD_SURFACE_FAMILIES in schemas.evidence, or tier them lower."
    )


def test_surface_families_are_wood_cut_or_deliberately_bark():
    # inner_bark is the one exception: it is exposed by splitting, so its surface matters,
    # but it is bark tissue and must not carry wood-cut authority.
    extra = _WOOD_SURFACE_FAMILIES - _wood_cut_families()
    assert extra == {"inner_bark"}
    assert tier_of_feature("inner_bark.colour") is EvidenceTier.BARK


def test_prepared_end_grain_prefixes_are_themselves_surface_bearing():
    for prefix in _PREPARED_END_GRAIN_PREFIXES:
        assert family_of(prefix) in _WOOD_SURFACE_FAMILIES
        assert requires_wood_surface(prefix)
