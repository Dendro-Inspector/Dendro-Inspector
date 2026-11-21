# 12 — Learning from corrections

- **Status:** Draft — research proposal, not implemented
- **Owner:** Dendro Inspector maintainers
- **Date:** 2026-11-22
- **Last-verified:** 2026-11-22 (CURRENT statements checked against `36a961c`)

## Agent brief

| | |
|---|---|
| Spec ID | HAI-12 |
| Priority | P2 research |
| Goal | Turn expert corrections into reviewed improvements (knowledge proposals, regression tests, evaluation labels) without poisoning the knowledge base or leaking into held-out data |
| Implementation approved | **No** |
| Prohibited | Automatic card edits; automatic fine-tuning; using a held-out unit's correction to change anything evaluated on that unit |

## Current state

- **CURRENT.** Specialist responses become append-only revisions with outcome codes; a
  revision changes nothing outside its package ([human review](../../human-review.md#what-a-revision-does-not-do)).
- **CURRENT.** `AGENTS.md` §16: a benchmark failure may reveal a defect, not define the fix;
  a `change_justification` block with an independent source and non-golden tests is
  required.
- **CURRENT.** Evidence coverage diagnostics split unusable evidence into intentionally weak
  and potential card gaps ([dataset policy](../../dataset-policy.md#finding-the-gaps-a-card-review-should-fill)).

## Proposal

### Error taxonomy

Every correction is classified by the **stage that failed**, never only by the wrong name:

| Class | Stage | Typical fix route |
|---|---|---|
| `wrong_reading` | Extraction (unsupported feature value) | Prompt/extractor research; morphology reviewer ([11](11-adversarial-scientific-review.md)) |
| `missed_reading` | Extraction (diagnostic feature not recorded) | Same; NBO guidance |
| `wrong_binding` | Organism binding / segmentation | [06](06-organism-binding-and-provenance.md) |
| `card_gap` | Knowledge: rule missing | [07](07-scientific-knowledge-architecture.md) proposal |
| `card_error` | Knowledge: rule wrong | Same, with source |
| `source_misread` | Knowledge: source misinterpreted | Same |
| `policy_too_strict` | Excessive abstention | Policy replay; owner decision |
| `policy_too_loose` | Excessive confidence | Policy replay; conformance case |
| `bad_follow_up` | Request useless or impossible | [05](05-active-field-investigator.md) tables |
| `model_disagreement` | Cross-model conflict | Recorded; no fix by itself |
| `coverage` | Taxon absent | Draft into review queue |

### Correction tiers

| Tier | Name | Requires | May be used for |
|---|---|---|---|
| T1 | Raw feedback | Anyone | Nothing but triage |
| T2 | Evidence-linked correction | Cites observations or photographs and an error class | Failure clustering |
| T3 | Independently reviewed | A second qualified reviewer, blind to the first, agrees | Regression *candidates*; development labels |
| T4 | Reference-backed | T3 plus a cited source ([07](07-scientific-knowledge-architecture.md)) or a lab/herbarium confirmation | Knowledge proposals; held-out labels (only for units not used in development) |
| T5 | Accepted update | Merged through the §16 / [07](07-scientific-knowledge-architecture.md) workflow | Changes the system |

A correction never skips a tier. An expert's correction is not a verified label until T3.

### Data separation

| Set | Source | Never used for |
|---|---|---|
| Development | Maintainers' photos, T2–T4 corrections on them | Reported accuracy |
| Validation | Separate trees, for choosing among policies | Final claims |
| Held-out | Trees never seen in development (`heldout-check` hash rules, CURRENT) | Any tuning, any card authoring |

A correction on a held-out unit is recorded but cannot inform a change evaluated on the same
held-out set; it waits for a new set (CURRENT protocol principle in
[`v1-heldout-protocol.md`](../../specs/v1-heldout-protocol.md)).

### Loop

1. Cluster T2+ corrections by error class and feature, not by case.
2. For a cluster, write the **generalised failure class** without naming a case.
3. Write synthetic, non-golden regression tests that fail on current code.
4. Propose the change through the matching route; owner and reviewer decide.
5. Replay all recorded traces under the change; report every moved decision, including
   ones that were right.
6. Monitor drift: recurring classes over time per model and KB version (runtime identity,
   CURRENT).

## Invariants

1. No correction changes the system without T5.
2. Labels are tiered; only T3+ labels are scored.
3. Held-out data stays out of every learning loop.
4. Fine-tuning is out of scope; any future proposal is a separate spec with its own data
   governance.

## Failure modes

| Failure | Control |
|---|---|
| Wrong expert correction | T3 blind second reviewer; disputed state |
| One vivid case drives a rule | Clustering + §16 block |
| Safeguard weakened to recover coverage | Replay must show no new wrong names on the full recorded set; overconfidence stays 0 on the public suite |
| Leakage into held-out | Hash checks (CURRENT `heldout-check`) extended to correction sources |

## Evaluation

Per KB release: moved decisions on recorded traces, wrong-name and coverage change on the
validation set, and unchanged public suite.

## Open questions

- Is a blind second reviewer affordable at pilot scale? **UNKNOWN**.
