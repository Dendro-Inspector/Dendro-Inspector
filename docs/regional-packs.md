# Regional packs

- **Status:** Current
- **Owner:** Evil Duck Dendro Inspector maintainers
- **Date:** 2026-07-25
- **Last-verified:** 2026-07-25

## Region is a prior, never a verdict

A regional pack lists which taxa are plausible where. It may lower confidence in a candidate.
It may **never** delete one.

Two reasons, both practical:

1. **Planted trees exist.** Parks, estates, shelterbelts, arboreta, street plantings and
   gardens are full of species well outside their native range. Range maps describe wild
   populations, and the photographs people actually send are disproportionately of planted
   trees — because those are the trees people walk past.
2. **The interesting cases are the exceptions.** Nobody photographs the obvious local
   conifer they have known since childhood. They photograph the odd one, which is precisely
   the case a regional prior would eliminate.

An identification system that cannot see a planted tree is wrong exactly when it is being
asked a real question.

## The unstated-location trap

The dangerous combination is a loaded pack and a case with **no location**. The prior is
available and tempting, and using it silently assumes the photograph was taken where the
operator happens to live.

`region_assumption_risk()` detects this, and the confusion reviewer raises a
`region_assumption` finding. `likely_in_region()` and `unlikely_in_region()` both return
`False` when the location is empty — the prior is not merely de-weighted, it is unavailable.

## Card format

```yaml
region_id: eastern-europe
display_name: Eastern Europe
likely_taxa: [pinus, picea, larix]
unlikely_taxa: []
notes:
  - A regional pack is a prior, never a verdict.
placeholder_content: true
```

`unlikely_taxa` is for taxa genuinely improbable in the region — not merely uncommon. Listing
something as unlikely lowers confidence and raises a finding; it does not remove the
candidate.

Selected via `EVIL_DUCK_REGION_PACK`, or `KnowledgeConfig(region_pack=...)`. Set it to `None`
to disable regional reasoning entirely, which is the right default for a global deployment.

## Adding a pack

1. create `knowledge/regions/<region-id>.yaml`;
2. list only taxa that have cards under `knowledge/taxa/`;
3. keep `placeholder_content: true` until someone with regional knowledge reviews it;
4. write the notes as constraints on use, not as botanical trivia.

Region ids are kebab-case and must match the filename — a contract test enforces it.

## What packs are not for

- **Not a candidate filter.** Generation is driven by evidence; the pack is consulted after.
- **Not a substitute for evidence.** "Most likely given the region" cannot raise confidence
  past what the visible features support. Stated in `prompts/nodes/confidence-reviewer.md`.
- **Not a geography lesson.** The pack answers one question: is this taxon expected here?

## v0.1 status

One pack: `eastern-europe`, placeholder content, three conifer genera, no `unlikely_taxa`
entries. Enough to exercise the constraint machinery, not enough to be useful regional
knowledge.

## Implementation references

- [`src/evil_duck_dendro/knowledge/regional_packs.py`](../src/evil_duck_dendro/knowledge/regional_packs.py)
- [`knowledge/regions/eastern-europe.yaml`](../knowledge/regions/eastern-europe.yaml)
- [`tests/unit/test_knowledge.py`](../tests/unit/test_knowledge.py) — `TestRegionalPriors`
