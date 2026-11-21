# 16 — Risks and failure modes

- **Status:** Draft — research proposal
- **Owner:** Dendro Inspector maintainers
- **Date:** 2026-11-22
- **Last-verified:** 2026-11-22 (CURRENT statements checked against `36a961c`)

## Agent brief

- Before implementing any spec, find its risks here and write the listed test first.
- **Class:** `S` = scientific limitation (cannot be fixed by code; must be disclosed and
  measured), `E` = engineering defect (can be prevented by code and tests), `S+E` = both.

## Risk register

| # | Risk | Class | Detection | Prevention | Mitigation | Residual | Test / evaluation |
|---|---|---|---|---|---|---|---|
| R1 | Automation bias: expert defers to a wrong AI answer | S | `changed_without_new_evidence`; correct→wrong change rate ([08](08-human-ai-collaboration.md)) | Blind-first; symmetric presentation | Stop feature growth if high | Some deference is unavoidable | Blind-first vs AI-first, counterbalanced ([13](13-evaluation-and-benchmark-design.md)) |
| R2 | Correlated LLM errors (same misreading in naming and extraction) | S | Cross-family disagreement on labelled units | Downgrade-only second family ([11](11-adversarial-scientific-review.md)) | `single_source` flag | Shared misreadings across families remain possible | CURRENT test pins the oak→*Acer* limit; cross-model error-overlap rate |
| R3 | Hallucinated botanical facts entering knowledge | S+E | Source resolution check; reviewer | No rule without named reviewer and resolvable source ([07](07-scientific-knowledge-architecture.md)) | Revert one commit | Reviewers can err | Contract test: every `source_id` resolves |
| R4 | Wrong organism binding | S+E | Counterfactual removal; provenance basis | Unbound by default; anchors required ([06](06-organism-binding-and-provenance.md)) | Continuity photograph NBO | Extractor may miss an organism in a photo | Crossing-branch, two-trunk synthetic packets |
| R5 | Confusing nearby trees (background named) | S+E | Subject roles; target designation | No target among several unless designated (CURRENT) | Human designation in stage 1 | Experimental extractor target proposal can err | CURRENT mixed-subject cases + `missing_target` outcome |
| R6 | Overconfidence from many similar photographs | E | Count of photographs per observation | Repeated photographs are one witness; NBO "independent information" rule | — | — | Synthetic: 5 near-identical bark photos must not raise tier or confidence |
| R7 | False precision (measurements, percentages) | E | Contract validation | Ordinal scores (CURRENT); `Measurement.precision` | Reject over-precise values | — | Validator tests ([10](10-multimodal-measurement.md)) |
| R8 | Species claimed from insufficient evidence | E | Overconfidence rate | CURRENT card caps and tier ceilings | — | Card errors can still permit it | Public suite `overconfidence_rate` = 0 |
| R9 | Inaccurate local cards | S | Expert review; disputes | Reviewed provenance; no tuning to benchmark | `disputed` state | Until independent review, all cards are AI-reviewed only | Reviewer-written synthetic conformance |
| R10 | Synonym / taxonomic revision confusion | S+E | Claim-resolution tests | Backbone with version ([07](07-scientific-knowledge-architecture.md)) | Keep original names | Backbones disagree | Resolver tests per synonym |
| R11 | Regional and seasonal bias | S | Per-season, per-region metrics | Scope per source; season filter in NBO | Disclose scope | Training data of models is opaque | Stratified evaluation |
| R12 | Overfitting to development benchmarks | S | In-sample vs held-out gap | `AGENTS.md` §16; hash-checked held-out | New units after any change | Owner's photos dominate development | `heldout-check`; report both |
| R13 | Leakage into held-out evaluations | E | Hash and group checks | CURRENT `heldout-check`; corrections tiered ([12](12-learning-from-corrections.md)) | Discard leaked units | Same tree photographed by someone else | Extend checks to correction sources |
| R14 | Data contamination from expert corrections | S+E | Tier audit | T3 blind second reviewer | Revert | Shared expert misconceptions | Tier invariants tests |
| R15 | Artificial consensus between agents | S | Agreement rate vs accuracy | Agreement never upgrades | — | — | Ablation per reviewer |
| R16 | Excessively conservative abstention | S+E | Coverage metrics; CURRENT evidence: graph 18 vs model 38 correct in-sample | Evidence-bound review; NBO turns abstention into a plan | Owner policy decisions with replay | Some correct names will be withheld by design | Coverage margin in [13](13-evaluation-and-benchmark-design.md) |
| R17 | Excessive cost and latency | E | Tokens, calls, time per unit (CURRENT telemetry) | Code-first proposals; spend limit | Selective routing | Field connectivity | Cost table per arm |
| R18 | Poor field usability | S | Pilot time, declined requests, completion | Short stage 1; one request at a time with alternatives | Redesign | Varies by user | Pilot (stage 3) |
| R19 | Unsafe follow-up requests | E | Safety filter audit | FC-1–FC-3 as contract ([03](03-human-arborist-field-workflow.md)) | — | Unanticipated site hazards | Contract test over the acquisition table |
| R20 | Persistent identity collisions | E | Merge audits | No auto-merge; events ([09](09-longitudinal-tree-intelligence.md)) | Split + recompute | — | Merge/split replay tests (deferred) |
| R21 | Privacy exposure (photographs, locations) | E | EXIF check (CURRENT export refusal) | Region-level location; consent; no real data in repo | Deletion on request | Photographs can identify places visually | CURRENT GPS refusal in `review-export` |
| R22 | Expert feedback that is itself wrong | S | Inter-expert disagreement | Tiers; blind second reviewer | `disputed`, unresolved allowed | — | Kappa among labellers |
| R23 | Continuous learning weakens safeguards | S+E | Replay diff per KB release | T5 only; public suite unchanged | Revert | — | Replay of all recorded traces |
| R24 | Overengineering | S | Each component's measured marginal value | Option A first; [17](17-evil-duck-review.md) | Remove components without value | — | Ablation |
| R25 | Unsupported claims of human-level superiority | S | Review of public text | [01](01-vision-and-research-hypothesis.md) "claims that must not be made" | Retract | — | PR checklist |

## Scientific limitations that no code fixes

- A photograph does not show what it does not show; season and life stage remove organs.
- Botanical knowledge has genuine variability and regional exceptions.
- Ground truth can be unresolvable; "unresolved" must be an allowed label.
- Model training data is opaque; correlated errors cannot be ruled out, only measured.
