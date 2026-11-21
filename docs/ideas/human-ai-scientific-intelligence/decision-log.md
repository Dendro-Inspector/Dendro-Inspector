# Decision log — Human–AI Scientific Intelligence research

- **Status:** Draft — register of decisions relevant to this research; no ADR here is accepted
- **Owner:** Dendro Inspector maintainers
- **Date:** 2026-11-22
- **Last-verified:** 2026-11-22 (existing decisions checked against `36a961c`)

## Agent brief

- **Existing** decisions are facts of the current system, with a citation. Do not
  contradict them in an implementation without an explicit owner decision.
- **Proposed** decisions are recommendations from this package. None is accepted.
- To record an owner decision: move the row to "Existing" (or "Rejected"/"Deferred"), add
  the date and where the decision was recorded. Do not invent acceptance.

## Existing engineering decisions (CURRENT)

| ID | Decision | Source |
|---|---|---|
| X1 | A model proposes; code adjudicates admission, resolution, confidence, escalation and the answer | `AGENTS.md` §4.6; [architecture](../../architecture.md#determinism-boundary) |
| X2 | Ordinal scores, no percentages | [architecture](../../architecture.md#ordinal-scores-not-percentages) |
| X3 | Only `confirmed_attached` detachable evidence counts at full tier; `user_attested` is capped; field-context flag credits nothing | `AttachmentStatus`; `field_attestation.py` |
| X4 | Exact card match is the admission boundary; never widened to admit a case's answer | `AGENTS.md` §12 key files |
| X5 | A leader matched only on supporting features is not named | `final_decision.discriminating_support` |
| X6 | Benchmark failures never define the fix; `change_justification` block required | `AGENTS.md` §16 |
| X7 | Golden material never informs card authoring | `AGENTS.md` §16 |
| X8 | External records can dispute, not verify; only an authoritative live source could verify | `knowledge/references.py` |
| X9 | Drafts go to `knowledge/review-queue/`, never read by the loader | `knowledge/drafts.py` |
| X10 | A specialist revision changes nothing outside its package | [human review](../../human-review.md#what-a-revision-does-not-do) |
| X11 | Held-out unit of independence is the physical tree; protocol fixed before results | [`v1-heldout-protocol.md`](../../specs/v1-heldout-protocol.md) |
| X12 | No agent framework; custom executor | `AGENTS.md` §12 |
| X13 | Experiments ship off by default | every flag in `config.py` |
| X14 | No photographs or personal data in the repository | [dataset policy](../../dataset-policy.md) |

## Proposed architectural decisions (not accepted)

| ID | Proposal | Spec | Depends on |
|---|---|---|---|
| P1 | Adopt Option A (minimal extension) first; Option B only after pilot value | [14](14-architecture-options.md) | HAI-E1, HAI-E3 |
| P2 | Contrast engine is telemetry first (C0); may later only narrow, never widen | [04](04-contrastive-taxonomy-engine.md) | Reviewed contrast data |
| P3 | NBO default ranker is rule-based minimax elimination (R1); EIG (R2) research only | [05](05-active-field-investigator.md) | HAI-E1 |
| P4 | Safety, legality, season filters are contracts, not prompt text | [05](05-active-field-investigator.md) | Practitioner-reviewed table |
| P5 | Model agreement never upgrades provenance or trust; disagreement downgrades | [06](06-organism-binding-and-provenance.md), [11](11-adversarial-scientific-review.md) | — |
| P6 | Blind first, AI second in every expert study | [08](08-human-ai-collaboration.md) | — |
| P7 | Corrections pass five tiers; only T5 changes the system | [12](12-learning-from-corrections.md) | — |
| P8 | Structured `KnowledgeSource` citations; URL/DOI never model-filled | [07](07-scientific-knowledge-architecture.md) | Q-K1 |
| P9 | New reviewers only downgrade-only and trigger-routed, with ablation before enabling | [11](11-adversarial-scientific-review.md) | Labelled data |
| P10 | Every human–AI claim is gated by the stage of [13](13-evaluation-and-benchmark-design.md) that supports it | [13](13-evaluation-and-benchmark-design.md) | — |

## Open decisions (owner)

| ID | Question | Recommendation |
|---|---|---|
| O1 | Approve HAI-E1 and HAI-E2 (zero-cost audits)? | Yes |
| O2 | Who qualifies as reviewer for contrast tables and cards? | Name the bar before recruiting (Q-K3) |
| O3 | Which taxonomic backbone is authoritative? | Decide with a reviewer (Q-K1) |
| O4 | Include a static capture checklist as the baseline arm in field pilots? | Yes ([17](17-evil-duck-review.md), Q4) |
| O5 | Include a key-only arm if a regional key is available? | Yes, if licensing allows |
| O6 | Should `no_discriminator_known` open review-queue items automatically? | Yes, as queue entries only |
| O7 | Should expert field attestation ever earn full tier? | Only after Q-F6 data |

## Rejected ideas

| Idea | Reason |
|---|---|
| Voting ensembles of same-family models | Correlated errors; adds tokens, not evidence ([17](17-evil-duck-review.md), Q5) |
| Vector-index RAG feeding literature into prompts | Re-opens unconstrained botany; bypasses review ([07](07-scientific-knowledge-architecture.md)) |
| Automatic card edits from corrections or benchmark failures | `AGENTS.md` §16; poisoning risk |
| Fine-tuning on expert corrections | Out of scope; data governance absent |
| Using LLM-stated confidence as a probability | Not calibrated; violates X2 |
| Multi-view reconstruction or hyperspectral imaging for identification | No evidence of value over a good organ photograph; high cost ([10](10-multimodal-measurement.md)) |
| Any tree-risk or safety assessment | Out of scope ([dataset policy](../../dataset-policy.md#what-the-system-must-not-be-used-for)) |
| A second decision engine in the investigation coordinator | Two sources of truth (`AGENTS.md` Main Rule) |

## Deferred ideas

| Idea | Until |
|---|---|
| Persistent tree registry (Option C) | Two-visit investigations show value and a user group needs it |
| Expected information gain ranking (R2) | A calibration study passes |
| Segmentation-assisted attachment | A continuity-photograph study shows it is insufficient |
| Additional model reviewers (morphology, attachment) | Ablation shows net benefit |
| Panel-level superiority claims (H4) | H1 holds in a controlled study |
| Measurement contracts with numeric card ranges | A reviewed flora supplies ranges (Q-F2/F3 style review) |
