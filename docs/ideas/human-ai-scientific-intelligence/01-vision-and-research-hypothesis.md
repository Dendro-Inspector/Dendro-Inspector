# 01 — Vision and research hypothesis

- **Status:** Draft — research proposal
- **Owner:** Dendro Inspector maintainers
- **Date:** 2026-11-22
- **Last-verified:** 2026-11-22 (CURRENT statements checked against `36a961c`)

## Agent brief

- **What this file fixes:** the hypotheses every other spec serves, and the claims nobody
  may make. An agent writing a PR description, README text or result summary from this
  directory checks it against "Claims that must not be made" below.
- **Implementation:** none. This file has no code consequence.

## The shift

**CURRENT.** Dendro Inspector answers one question per inspection: given these photographs,
which taxon, at which level, does the evidence support? It may answer "none" and ask for one
photograph ([`README.md`](../../../README.md), "What the graph may return";
[`PhotoRequest`](../../../src/dendro_inspector/schemas/decisions.py)).

**PROPOSED.** A second question is added, and it becomes the primary one in the field:
**which evidence should be obtained next, by whom, to distinguish the remaining hypotheses
most efficiently and safely?** The identification answer stays exactly as bounded as it is
today. What changes is that the system also plans and records the investigation that
produces the evidence.

The system optimises the **quality of the investigation**: right evidence, attributed to the
right organism, compared against the right look-alikes, at an honest resolution, with a
record another expert can contest.

## Hypotheses

Each is falsifiable and each has an owner spec. None is established.

| ID | HYPOTHESIS | Falsified when | Spec |
|---|---|---|---|
| H1 | A qualified expert using Dendro (Group D) reaches a defensible genus with a **lower wrong-genus rate** than the same expert alone (Group A), at no more than a pre-registered loss of coverage | The paired study in [13](13-evaluation-and-benchmark-design.md) shows D's wrong-genus rate not lower than A's, or D's coverage below the margin | 13 |
| H2 | Human + Dendro reaches a defensible identification with **fewer additional observations or less time** than the expert alone | Median observations or time per resolved tree is not lower for D | 05, 13 |
| H3 | Human + Dendro is **better calibrated** than the expert alone: stated certainty matches realised accuracy more closely | Calibration error for D is not lower than for A | 08, 13 |
| H4 | Under controlled conditions, one expert with Dendro matches or exceeds an **independent expert panel** (Group B) on wrong-genus rate at comparable coverage | Panel B's wrong-genus rate is lower than D's beyond the pre-registered margin | 13 |
| H5 | Explicit pairwise contrast between look-alikes names fewer wrong genera than single-card matching at equal coverage | The contrast arm does not reduce confident wrong names on look-alike units | 04 |
| H6 | A ranked Next Best Observation resolves more uncertain trees per extra photograph than today's single rule-based request | Resolution per additional observation is not higher than baseline | 05 |
| H7 | A blind-first protocol reduces automation bias relative to AI-first review | Experts change correct initial answers to AI-suggested wrong ones at the same rate in both protocols | 08 |

H4 is deliberately separate from H1 and stronger. H1 can hold while H4 fails, and that
outcome would still justify the system.

## Division of labour

| Participant | Contributes | Must not be asked to do |
|---|---|---|
| Human expert | Physical access, attachment checks by hand, measurements, seasonal return, local experience, final responsibility | Accept a name because a system displayed it |
| Dendro (models) | Readings from photographs, candidate proposals, challenges, look-alike recall | Decide admissibility, resolution, confidence or the answer (`AGENTS.md` §4.6) |
| Dendro (code) | Admission, ceilings, contrast tables, observation planning, provenance, records, replay | Assert botanical facts no card or source states |

## Claims that must not be made

- That Dendro identifies trees better than arborists. No such measurement exists. The only
  real-photograph figures are in-sample, on the maintainers' own 41 units
  ([`README.md`](../../../README.md#status)).
- That any capability in this directory exists, unless labelled CURRENT.
- That an expert's acceptance of an answer proves it correct.
- That a card match is a botanical identification. `card_confirmed` means the recorded
  observations match a feature the card calls strong
  ([`docs/evaluation.md`](../../evaluation.md#arm-v-the-model-checked-against-its-card)).
- Marketing terms: "revolutionary", "scientifically proven", "guaranteed accurate",
  "self-improving", "zero hallucinations".

## Scope limits

- Taxonomic identification only. Tree condition, risk and structural safety are out of
  scope ([`docs/dataset-policy.md`](../../dataset-policy.md#what-the-system-must-not-be-used-for)).
  [09](09-longitudinal-tree-intelligence.md) keeps condition records separate if they are
  ever added.
- Region: the knowledge base targets Eastern Europe
  ([`knowledge/regions/eastern-europe.yaml`](../../../knowledge/regions/eastern-europe.yaml)).
  Nothing here generalises beyond it without new knowledge and new evaluation.
- The user is assumed to be qualified (arborist, forester, dendrologist). A consumer mode is
  not designed here.
