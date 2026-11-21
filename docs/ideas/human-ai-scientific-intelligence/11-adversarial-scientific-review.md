# 11 — Adversarial scientific review

- **Status:** Draft — research proposal, not implemented
- **Owner:** Dendro Inspector maintainers
- **Date:** 2026-11-22
- **Last-verified:** 2026-11-22 (CURRENT statements checked against `36a961c`)

## Agent brief

| | |
|---|---|
| Spec ID | HAI-11 |
| Priority | P2 research |
| Goal | Replace "several models guess and vote" with specialised review responsibilities, each producing checkable findings, routed only where its marginal value is shown |
| Implementation approved | **No** |
| Must not touch | Deterministic admissibility (`review_synthesizer.adjudicate`); final decision authority; arbiter binding |
| Hard rule | A reviewer's persuasive language never overrides evidence policy. A finding counts only if code can check its ground |

## Current state

- **CURRENT.** Three reviewers (botanical, confusion, confidence) run concurrently, plus an
  arbiter on escalation; each model reviewer has a deterministic layer
  ([review pipeline](../../review-pipeline.md)).
- **CURRENT.** Findings are admitted only by code; rejected findings are kept with reasons.
- **CURRENT.** `evidence_bound` (experimental) lets a review lower the level only through
  `card_contradiction`, `supported_alternative` or `contaminated_support`, which code checks
  (`final_decision.validated_ground`).
- **CURRENT.** The arbiter changed 20 of 1,450 live verdicts; selective arbitration keeps 17
  of 20 changes at 64% of arbiter tokens (cost measurement)
  ([model roles](../../model-roles.md#selective-arbitration-experimental)).
- **CURRENT.** Adaptive reviewer routing exists as an experiment
  ([`nodes/adaptive_gate.py`](../../../src/dendro_inspector/nodes/adaptive_gate.py)), off by
  default. Its effect on real runs is not recorded in the repository (**UNKNOWN**).
- **CURRENT (EVIDENCE, in-sample).** Review withdrew correct names: graph 18 correct vs model
  alone 38 on 41 units ([`README.md`](../../../README.md#status)).

## Proposed responsibilities

Each maps to an existing node, to deterministic code, or to a new optional call. "New call"
rows are research only.

| Reviewer | Question | Checkable output | Implementation route |
|---|---|---|---|
| Morphology | Is each extracted reading supported by the cited photograph? | Per-observation `supported / not_supported / unreadable`, citing image IDs | New optional call, **different model family** from the extractor; can only downgrade an observation's trust |
| Attachment | Does the evidence belong to the target organism? | Per-observation provenance basis ([06](06-organism-binding-and-provenance.md)) | Continuity photograph rule (code) first; model reviewer only downgrades |
| Taxonomic challenger | Which carded or uncovered taxon explains the same evidence? | A named rival + the contrast table result ([04](04-contrastive-taxonomy-engine.md)) | Mostly code (contrast engine); the CURRENT confusion reviewer proposes rivals |
| Knowledge coverage | Is the reference coverage adequate for this subject? | `knowledge_coverage_gap`, `no_discriminator_known` | Code (CURRENT coverage diagnostics + HAI-05 stop reason) |
| Evidence sufficiency | Does surviving evidence support the claimed rank? | Bound with source | Code (CURRENT final decision) — no model needed |
| Calibration | Are confidence and specificity justified by measured performance? | Per-pathway measured error rates from evaluation | Offline statistics (HAI-13), applied as configuration, never per-case model judgement |
| Human expert | Unresolved or high-impact cases | Blind-first record ([08](08-human-ai-collaboration.md)) | Human |

Observation: four of seven responsibilities are **code or statistics, not model calls**.
The proposal adds at most two optional model calls (morphology, attachment), both
downgrade-only.

## Selective routing

A reviewer runs only when a deterministic trigger says its question is open:

| Reviewer | Trigger |
|---|---|
| Morphology | The verdict's discriminating evidence is a single observation from a single call (`single_source`) |
| Attachment | Decision-critical detachable evidence with `extractor_asserted` basis |
| Taxonomic challenger | A carded rival is `consistent` and not separated |
| Human expert | Stop reason `no_discriminator_known`, conflicting history, or high-impact use declared |

Every trigger is logged with the reviewer's outcome, so marginal value is measurable:
**changed outcomes per call** and **correct changes per call** (the latter needs labels).

## Correlated errors

Two calls to the same model share failure modes (CURRENT guidance: bind the arbiter to a
different family, [model roles](../../model-roles.md#arbiter)). The oak-as-`palmate_lobed`
case shows a shared misreading confirmed by a same-model check
([evaluation](../../evaluation.md#arm-v-the-model-checked-against-its-card)). Therefore:

1. A downgrade-only reviewer from another family can catch some shared misreadings.
2. Agreement across families is **not** independent confirmation of botany; it is two
   readings. It never raises trust.
3. Independence is measured, not assumed: on labelled units, the rate at which two models
   make the *same* wrong reading.

## Stopping criteria

Review stops when no trigger is open, the spend limit is reached (CURRENT
`max_model_calls`/`max_tokens`), or a reviewer's measured marginal value on the last
evaluation was zero (configuration decision, not runtime).

## Invariants

1. No model output sets admission, rank, confidence or the answer.
2. New reviewers can only downgrade trust or add a rival.
3. Every reviewer call is attributable in the trace (CURRENT `review_rounds`).

## Failure modes

| Failure | Control |
|---|---|
| Review withdraws correct names (CURRENT evidence) | Downgrade must cite a checkable ground; measured per reviewer |
| Artificial consensus | Agreement never upgrades |
| Cost growth | Triggers + spend limit; marginal value report |
| Reviewer reads a different evidence world | CURRENT `ReviewProjection` binding, kept |

## Evaluation

Ablation per reviewer on labelled units: wrong-name rate, coverage, tokens, latency, with
and without that reviewer, paired. A reviewer that does not reduce wrong names or improve
calibration on held-out data is removed ([17](17-evil-duck-review.md), Q6).

## Acceptance criteria for a future PR

- [ ] One reviewer at a time, behind a flag, downgrade-only, with a trigger and trace record.
- [ ] Ablation result with denominators before enabling anywhere.
