# 20 — Case study: mature rough-barked tree, *Alnus* versus *Ulmus*

- **Status:** Draft — evidence-policy research case; **not ground truth**
- **Owner:** Dendro Inspector maintainers
- **Date:** 2026-11-22
- **Last-verified:** 2026-11-22 (CURRENT statements checked against `36a961c`)

## Agent brief

- This case is **not independently verified**. Nobody has established the tree's genus.
  It must never be described as "correctly identified", used as a benchmark label, or used
  to justify a card change (`AGENTS.md` §16).
- No photograph, location or identifying detail of the real tree is recorded here. The
  observations below are **abstracted and partly hypothetical**.
- Expected outcome: a field investigation procedure, not an identification.

## Situation (abstracted)

| Item | Recorded as |
|---|---|
| Subject | One mature standing tree |
| Bark | Rough, deeply fissured |
| Leaves | Simple, serrated margin; some shoots appear attached in some photographs |
| Leaf base | Not clearly resolved in any photograph |
| Reproductive structures | None confirmed |
| Plausible genera (human hypothesis) | *Alnus* (alder) or *Ulmus* (elm) |

## Separation of claims

| Layer | Content | Label |
|---|---|---|
| Directly visible | Fissured bark; serrated simple leaves; shoots that look attached in some frames | Observation (to be extracted) |
| Model interpretation | Possible extractor readings: `bark.texture = deep_longitudinal_fissures`, `leaf.shape = …` (value depends on the model) | Model output; not yet trusted |
| Unresolved attribution | Whether every leaf belongs to this trunk | `attachment: unknown` for some readings |
| Reference-card compatibility | *Alnus* card: only strong feature `cones.type = small_woody_persistent`; leaf `rounded_obtuse_or_notched_apex` and bark `dark_platy` are supporting ([`alnus.yaml`](../../../knowledge/taxa/alnus.yaml)) | CURRENT |
| Card coverage | **No *Ulmus* card exists** | CURRENT |
| Diagnostic evidence | None that the cards call diagnostic | Derived |
| Working hypothesis | "*Alnus* or *Ulmus*", unranked | HYPOTHESIS |
| Validated identity | None | — |
| Expert-confirmed ground truth | None | UNKNOWN |

## What the CURRENT system does (expected, from code reading)

1. Admission can keep *Alnus* on a supporting leaf or bark reading (compatible).
2. The discriminating-support rule blocks naming: *Alnus* has no strong hit without cones
   (`final_decision._discrimination`).
3. *Ulmus* cannot be admitted (no card). With open-world discovery on, it could be retained
   as an uncovered hypothesis if a trusted observation supports it; it can never be named.
4. Bark is capped at genus, low confidence; bark alone cannot name either.
5. Next photograph: the comparison and follow-up lists for *Alnus* (`cone_photo`,
   `leaf_upper_macro`, `site_photo`). Nothing asks for the leaf base, because no card
   declares a leaf-base character. **This is the gap the case exposes.**

Expected outcome today: no genus named; *Alnus* possibly listed as "still consistent";
request for cones or a leaf macro. This was not run for this document.

## Research questions and proposed answers

**Can an expert separate *Alnus* from *Ulmus* by leaf characters without reproductive
organs?** Probably often, from general botanical knowledge: elm leaves are commonly
described with a markedly asymmetric (oblique) base and straight, parallel lateral veins;
alder leaves with a symmetric base and, in black alder, a rounded or notched apex.
**UNKNOWN until a qualified reviewer confirms these against a cited regional flora,
including exceptions (species, juvenile and epicormic leaves).** (Q-F2)

**Are current card rules too restrictive?** For *Alnus*, possibly: a reviewed vegetative
combination (for example symmetric base + notched apex + doubly serrate margin, if the
reviewer supports it) could become a `DiagnosticCombination` with a genus cap. This would
be a knowledge proposal through [07](07-scientific-knowledge-architecture.md), with an
independent source and non-golden tests — **not** a change motivated by this tree.

**What should happen when a plausible rival has no card?** The answer must disclose it:
"*Ulmus* (elm) is a plausible alternative that the reference cards do not cover; no genus is
named." In code terms this is a coverage gap (`no_discriminator_known` in HAI-05), not
insufficient photographs.

**When should the system request a leaf-base macro?** When a reviewed contrast table for
the pair declares leaf-base symmetry as discriminating, the pair is still `consistent`,
the leaf can be shown attached, and the season has leaves (HAI-05 R1).

**How to disclose uncertainty without asserting an unvalidated genus?** Keep the verdict
unnamed; list both hypotheses symmetrically; state the missing character and the
coverage gap; never promote either to `visual_hypothesis` unless a card-diagnostic reading
exists (CURRENT rule).

## Proposed field investigation procedure

| Step | Observation | Why | Stop if |
|---|---|---|---|
| 1 | Continuity: one attached shoot traced to this trunk (photograph or attestation) | Ownership first (CURRENT attachment-first rule) | Cannot establish → leaves stay context; go to step 4 |
| 2 | Leaf base and apex macro of an attached leaf, flat, with scale | Candidate discriminator (pending review) | Base clearly symmetric or oblique → record; contrast decides only if reviewed |
| 3 | Leaf venation and margin (single vs double serration), upper and underside | Secondary characters | — |
| 4 | Twig and buds (stalked buds, bud arrangement) | Available in winter too | — |
| 5 | Search under the crown for persistent woody cones or old catkins; record provenance as found-on-ground | *Alnus* strong feature; capped as ground material unless attested | Found and attested → capped support |
| 6 | Revisit: early spring (elm fruit), any season for persistent alder cones | Reproductive evidence | Not revisitable → final rank stays as reached |

## What would change the conclusion

- Attached persistent woody cones → *Alnus* named under the CURRENT card.
- A reviewed *Ulmus* card plus a trusted oblique leaf base → *Ulmus* could be admitted.
- Leaves proven to belong to another tree → leaf readings drop out; bark-only, unnamed.
