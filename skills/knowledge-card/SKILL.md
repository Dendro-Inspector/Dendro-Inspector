---
name: knowledge-card
description: Add or edit a taxon card, comparison card or regional pack under knowledge/. Use when asked to add a species or genus, describe how two taxa are told apart, add a follow-up photo recommendation, or when a candidate is being rejected because no card declares its features.
---

# Editing the knowledge pack

**Status:** Draft — written from the implementation, not yet exercised on a real task.
Correct it in place when it misleads you.

Knowledge here is **data, not agents**. Cards are declarative YAML validated against
`TaxonCard` / `ComparisonCard` contracts; no model decides what a card says, and no card
decides what a verdict is. A card makes a claim *admissible*, it does not make it true.

Every shipped card is validated by
[`tests/contract/test_data_contract.py`](../../tests/contract/test_data_contract.py) —
malformed YAML fails there, fast and offline, rather than three nodes into a run.

## The pack is unreviewed demonstration content

Stated in the README, in `docs/dataset-policy.md`, and in every card's own `provenance`
block. Do not describe it as verified, and do not quietly promote a card by editing its
`review_state`.

## Provenance is mandatory and scoped

Every card carries a `provenance` block scoped to its **feature rules** —
`strong_positive_features`, `supporting_features`, `contradictions` and their thresholds.
Any individual feature rule may override it.

The contract enforces exactly one rule: `review_state: reviewed` requires both
`reviewed_by` and `last_reviewed`. **A card cannot claim review without saying who and
when.** Everything else about provenance is enforced by review, not by code.

The failure this guards against is quiet and expensive: someone adds a plausible,
well-written, wrong feature; every test still passes, because the tests check the code and
not the botany. Provenance does not prevent that. It makes the resulting list of
never-verified rules *producible*.

Fields and taxonomic-placement attribution:
[`docs/dataset-policy.md`](../../docs/dataset-policy.md#provenance-is-mandatory).

## Feature paths must match the evidence vocabulary

A card that declares a feature the extractor never emits is inert — it will never match, and
nothing will tell you. Feature families and their tiers are in
[`src/dendro_inspector/knowledge/evidence_hierarchy.py`](../../src/dendro_inspector/knowledge/evidence_hierarchy.py);
see [`evidence-contract`](../evidence-contract/SKILL.md) before inventing a family.

Two rules that bite:

- **Colour is never enough.** Any feature ending `.colour`, `.color` or `.tone` is capped
  to bark-equivalent authority and cannot admit a candidate on its own. A card whose only
  distinguishing features are colours will never produce a verdict.
- **Vocabulary is exact, not fuzzy.** `.color` and `.colour` are both classified as colour,
  but they are never rewritten into one another for card matching. A card declaring
  `heartwood.color` will not match an observation of `heartwood.colour`.

## Do not tune a card against a failing case

This is the rule the repository is most serious about, and it is
[`AGENTS.md` §16](../../AGENTS.md#16-benchmark-governance).

> A benchmark failure may reveal a defect.
> It may not, by itself, define the fix.

"Case 47 says Picea and we say Pinus" is an observation. It is not a botanical source and it
is not permission to edit `picea.yaml` until case 47 goes green. That edit costs nothing
today and permanently destroys what the benchmark measures — afterwards the suite measures
how well the cards were fitted to the suite.

Any card change motivated by a benchmark failure carries a `change_justification` block in
the pull request naming an **independent domain source** and new **non-golden** tests.

## Adding a comparison card

Comparison cards answer "what would actually separate these two?" from declared data.
They drive `decisive_features_between()` and the follow-up photo recommendations. A
comparison card listing only features already known to be insufficient (colour, coarse bark
texture) will correctly refuse to resolve anything — check `INSUFFICIENT_ALONE` in
[`src/dendro_inspector/knowledge/comparison_cards.py`](../../src/dendro_inspector/knowledge/comparison_cards.py)
before adding one.
