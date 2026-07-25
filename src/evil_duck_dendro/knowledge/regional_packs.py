"""Pure helpers over regional packs.

Region is a prior, never a verdict. A taxon being "unlikely here" lowers confidence and
raises a finding; it does not delete the candidate. Plenty of trees are planted well
outside their native range, and an identification system that cannot see that is wrong in
exactly the cases people ask about.
"""

from __future__ import annotations

from evil_duck_dendro.schemas.taxon import RegionalPack


def is_region_known(pack: RegionalPack | None, location: str | None) -> bool:
    """Whether a regional prior may be applied at all."""
    return pack is not None and bool(location and location.strip())


def unlikely_in_region(pack: RegionalPack | None, taxon_id: str, location: str | None) -> bool:
    """Whether the pack marks this taxon as unexpected — only when location is known."""
    if not is_region_known(pack, location) or pack is None:
        return False
    return taxon_id in pack.unlikely_taxa


def likely_in_region(pack: RegionalPack | None, taxon_id: str, location: str | None) -> bool:
    if not is_region_known(pack, location) or pack is None:
        return False
    return taxon_id in pack.likely_taxa


def region_assumption_risk(pack: RegionalPack | None, location: str | None) -> bool:
    """True when a pack exists but no location was supplied.

    That combination is the trap: the regional prior is available and tempting, and using
    it would silently assume the photograph was taken where the operator happens to live.
    """
    return pack is not None and not (location and location.strip())
