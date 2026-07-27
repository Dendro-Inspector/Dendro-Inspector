---
name: evidence-contract
description: Extend or change the evidence contract — feature families, evidence tiers, trust projection, wood-surface provenance, attachment rules. Use when adding a new feature family, changing what a tier may claim, adjusting when evidence is demoted to context, or when a run demotes an observation you expected to count.
---

# Changing the evidence contract

**Status:** Draft — written from the implementation, not yet exercised on a real task.
Correct it in place when it misleads you.

This is the layer that decides **what a photograph is allowed to prove**. It is
deterministic on purpose: asking the model that produced the evidence to rule on its own
admissibility is asking a witness to rule on its own testimony.

Primary implementation:
[`src/dendro_inspector/knowledge/evidence_hierarchy.py`](../../src/dendro_inspector/knowledge/evidence_hierarchy.py)
and [`src/dendro_inspector/schemas/evidence.py`](../../src/dendro_inspector/schemas/evidence.py).
Rationale: [`docs/architecture.md`](../../docs/architecture.md#the-evidence-hierarchy).

## Two projections, not one

- **Tier** — how strong this *kind* of evidence is. Fruit/seed > clear foliage > leaf
  arrangement > wood cut > bark > silhouette > context.
- **Trust** — whether this *particular observation* may support a claim at all:
  `FULL_POSITIVE`, `CAPPED_POSITIVE` (bark-equivalent ceiling), `CONTEXT_ONLY`.

An observation's effective tier is its family tier projected through its trust. Changing one
without thinking about the other is the usual way this layer breaks.

## Adding a feature family means touching more than one table

Family tiers live in `_FAMILY_TIERS` (longest prefix wins, so `leaf.arrangement` is a
distinct, weaker tier from `leaf.shape`). But three other tables are maintained
independently and are not derived from it:

| Table | Location | Governs |
|---|---|---|
| `_FAMILY_TIERS` | `evidence_hierarchy.py` | how strong the family is |
| `_WOOD_SURFACE_FAMILIES` | `schemas/evidence.py` | must the observation declare its surface |
| `DETACHABLE_FAMILIES` | `evidence_hierarchy.py` | must attachment be confirmed |
| `_PREPARED_END_GRAIN_PREFIXES` | `evidence_hierarchy.py` | needs prepared end grain or it is context |

A wood family added to the tier table but not the surface table keeps full wood-cut
authority while skipping the surface contract entirely. That coupling is asserted by
[`tests/contract/test_evidence_family_contract.py`](../../tests/contract/test_evidence_family_contract.py)
— if you add a family and that test fails, the test is right.

## The demotion rules, in the order they apply

1. Non-image source → `CONTEXT_ONLY`. Text and user claims never support a taxon.
2. Obscured or not-visible → `CONTEXT_ONLY`.
3. Detachable family without confirmed attachment → `CONTEXT_ONLY`. Foliage at the frame
   edge may belong to the neighbouring tree.
4. Partial visibility or low reliability → capped to bark-equivalent.
5. Anatomy requiring prepared end grain, observed on anything else → `CONTEXT_ONLY`.
   A rough chainsaw cut cannot prove pore arrangement no matter how confident the label.
6. Any other wood feature not on prepared end grain → capped.
7. Colour or tone → capped, **unconditionally**.

Rule 7 is unconditional by design. An earlier draft made it conditional on recorded lighting
and white balance; that means trusting a model-authored lighting label to decide what a
model-authored colour claim is worth. Do not reintroduce it.

## Not visible is not absent

An absent feature and an unobservable one are different claims and are stored differently.
Collapsing them lets "I cannot see cones" become "there are no cones," which is how a
conifer becomes a broadleaf. Check `absent_features` versus `Visibility.NOT_VISIBLE` before
changing anything that reads either.

## What a change here costs

This layer sits under every verdict, so a change moves the frozen baseline. Expect
`tests/evaluation/test_baseline.py` to fail and treat each moved case as something to
explain, not to absorb — see [`eval-case`](../eval-case/SKILL.md).

If the change alters what the deterministic layer expects of the prompt — a new tier, a
changed ceiling — the policy revision moves with it; see
[`prompt-change`](../prompt-change/SKILL.md).

## Ordinal, never percentages

Confidence is `LOW | MEDIUM | HIGH` with display bands, not a number. The bands exist
because "87/100" claims a calibration nobody has. Do not add a numeric confidence field.
