# Analysis of the all-Astra Dendro Inspector bridge run

- Run: `2026-09-06-astra-all-055030-058`, photo `photo-058`.
- **Status:** Point-in-time analysis; VERIFIED for the retained execution records and local reproductions below. Botanical accuracy is UNKNOWN.
- **Owner:** Dendro Inspector maintainers
- **Date:** 2026-09-06
- Scope: read-only inspection of the supplied ZIP, adjacent output and trace, retained bridge state, and the current source tree. No source changes or new model calls.

The bridge completed cleanly. The foreground result became `unknown / low` during review, before arbitration. Two other subjects were excluded by the evidence-quality gate. The run exposes two reproducible reporting defects and a problem with subject-specific photo guidance. The logged arbitration-change flags must not be used to assess the arbiter's benefit for this run.

## Execution and artifact integrity

The run lasted **354.361 seconds**, from 02:51:57.962 to 02:57:52.323 UTC (05:51:57.962–05:57:52.323 Kyiv time). All 16 node events are `ok`; all seven provider attempts have zero validation failures; graph retries are zero. Each retained answer passes its corresponding current Pydantic contract. The worker, bridge and ledger process stderr files are empty. Dendro stderr contains two warnings and the completion record, with no execution error.

All seven provenance records name `gpt-6-astra`, and the retained launcher passes that model to the Codex worker. `anthropic:astra-all` is the local transport route. The worker's model provenance field records the requested model; the bundle does not provide an independent server-side attestation. It also does not retain Codex's raw event stream or private reasoning. The experiment records `xhigh` as inherited configuration, rather than a per-call command-line override.

ZIP CRC validation passed. The bundled stdout, stderr, trace and README are byte-identical to the adjacent files. The standalone trace equals the trace embedded in stdout. All seven transmitted images match their recorded SHA-256 and contain the same 965,285 bytes. The original photo has a different hash from the transmitted image; original and transmitted image identity must therefore be tracked separately.

Bundle SHA-256: `53a9f6ab9f02bc5aa3a47bfc410404e4463979eb4981e31f718fb83dca82cdff`.

## Why the three results are unknown

| Subject | Evidence and processing | Decisive cause |
|---|---|---|
| `foreground_tree` | Six observations; evidence tier 3 (bark). Picea and Fagus survive admission, both weak. | The confusion reviewer recommends `unknown / low`. The final derivation names `reviewer_recommendation` as the binding resolution bound. |
| `background_tree_right` | One partial bark observation, `obs-7`. | It falls below the configured minimum of two positive observations and never enters the admitted candidate sets. |
| `overhead_branch_1` | Needle presence and drooping branchlets, both with attachment `unknown`. Astra proposes Pinaceae in its raw answer. | Both observations are context-only under the existing attachment rules. This subject is excluded by quality before candidate validation or review. |

The foreground Picea candidate retains only `obs-3`, `bark.texture=fine_scales`. Fagus retains only `obs-1`, `trunk.form=straight_cylindrical`. The generator's colour support and unsupported Fagus contradiction references are removed. The warning's `dropped_evidence_ids` list is an aggregate across candidates: `obs-3` is removed as a contradiction for Fagus but still supports Picea. No foreground taxon is rejected and no score is demoted.

The botanical reviewer returns a bare pass with no recommendation. The confidence reviewer recommends `genus / low`. The confusion reviewer supplies the only model finding, requests connected diagnostic foliage, and recommends `unknown / low`. That explains the reviewer disagreement and the final cap. The arbiter then returns pass, no findings, and `unknown / low`.

The foreground provisional decision already has no selected taxon, resolution `unknown`, confidence `low`, and status `insufficient_evidence`. Arbitration leaves all four fields unchanged. The attachment authority gate is `not_applicable`; it is not the mechanism that downgrades this foreground verdict. The normal evidence rules had already prevented unconnected overhead foliage from supporting the trunk.

## Findings

**1. Arbitration benefit is falsely reported — VERIFIED, highest priority.**

The trace sets `arbiter_changed_status`, `arbiter_changed_taxon`, `arbiter_changed_resolution` and `arbiter_changed_confidence` to true. Its provisional list contains only the foreground subject, while the final list contains all three subjects. The comparison checks the union of subject IDs and treats an absent prior subject as a changed value.

Calling the existing comparison function on the retained decisions reproduces all four true flags. Restricting the comparison to the same foreground subject produces false for all four. The added background/branch terminal records are being counted as arbiter improvements or changes, despite neither having an admitted candidate set.

Source: [`trace.py`, line 40](../../src/dendro_inspector/observability/trace.py), [`escalation_gate.py`, line 168](../../src/dendro_inspector/nodes/escalation_gate.py), and final-result assembly in [`final_decision.py`, line 1056](../../src/dendro_inspector/nodes/final_decision.py). A suitable fix should create a complete provisional decision set for the same subjects, and distinguish subject-set changes from changes to an existing decision. A synthetic multi-subject regression can cover this without changing any taxon card.

**2. An empty-candidate result loses the background evidence tier — VERIFIED.**

Reassessing the saved packet with the current quality rules yields tiers `{foreground_tree: 3, background_tree_right: 3, overhead_branch_1: 1}`. The final background decision reports tier 1. The no-leader branch constructs a `FinalDecision` without supplying its evidence tier, so the default replaces the actual bark tier. It also omits a subject-specific explanation of the minimum-observation failure.

This does not establish that the background result should be identified: one observation still fails the configured quality threshold. It does make the diagnostic record misleading. Source: [`final_decision.py`, line 879](../../src/dendro_inspector/nodes/final_decision.py). Preserve the quality tier and the specific quality failure when constructing a terminal result.

**3. Follow-up guidance assumes needles for every subject — VERIFIED behavior; suitability requires review.**

All three outputs request `needle_shoot_attachment_photo`. The background subject has only a bark observation, and the foreground alternatives include Fagus. The common unplaced overhead branch creates a competing-ownership signal for both trees; `attachment_request` then selects the needle family for each. This is reproducible directly with the saved evidence packet and photo-selection code.

The problem is the instruction's presumption that each tree has needles. A request for a connected leafy or needle-bearing shoot would preserve the unresolved identity. The global declared type `standing_tree` also overrides the independent branch's subject kind in photo planning. Source: [`evidence_authority.py`, line 78](../../src/dendro_inspector/knowledge/evidence_authority.py) and [`photo_planner.py`, line 128](../../src/dendro_inspector/nodes/photo_planner.py). Scope photo selection to evidence actually attributable to each subject, while retaining attachment requirements.

**4. Two potentially useful observations cannot match any card — VERIFIED limitation.**

The vocabulary warning identifies three unmatchable observations out of nine. One is intentionally weak bark colour; the other two are `bark.flake_geometry` and `bark.surface_marks`. These are recorded as possible knowledge-coverage gaps. They are not evidence that a different taxon was correct, and they are not the direct binding cause of the foreground unknown result. Any card change needs independent domain justification and non-golden tests, as required by [AGENTS.md section 16](../../AGENTS.md#16-benchmark-governance).

## Timing and token use

| Call | Dendro elapsed, seconds | Worker execution, seconds | Input tokens | Output tokens |
|---|---:|---:|---:|---:|
| Planner | 51.50 | 47.59 | 37,279 | 900 |
| Extractor | 103.25 | 102.37 | 47,987 | 3,052 |
| Candidate generator | 58.67 | 58.31 | 42,046 | 1,274 |
| Botanical reviewer | 23.34 | 22.75 | 45,474 | 359 |
| Confusion reviewer | 95.76 | 72.66 | 45,510 | 1,978 |
| Confidence reviewer | 119.29 | 23.37 | 47,128 | 413 |
| Arbiter | 21.32 | 20.30 | 45,604 | 340 |

The graph issues reviewer requests concurrently, but the launcher starts one worker. Its recorded execution intervals are sequential: botanical, confusion, then confidence. The confidence review's 119.29 seconds therefore includes about 96 seconds waiting, rather than representing 119 seconds of isolated model execution. Worker execution includes CLI overhead; it is not a measurement of pure inference latency.

Total reported usage is **311,028 input tokens, 8,316 output tokens, and zero cached input tokens**. The upstream additionally reports 5,705 reasoning-output tokens; these are not added to the output total. The three reviewers and arbiter account for 183,716 input tokens, about 59.1% of the input total. Arbitration adds 21.32 seconds and 45,604 input tokens without changing the common subject's decision fields.

The measured worker execution intervals sum to 347.35 seconds, compared with 354.36 seconds for the graph. Most elapsed time is in worker execution, with reviewer queueing redistributing time between the node measurements. Reviewing repeated prompt content and cache behavior is justified by the token records. Parallel worker capacity would need a separate authorized experiment; this run cannot predict its speedup or account limits.

## Interpretation and next verification

The clean contracts and absence of repairs establish an engineering success for this seven-call path. They do not establish botanical accuracy, completeness of the extracted evidence, or an advantage over another model. This is one photo, not three independent accuracy samples. `unknown` is an allowed result; `abstained=false` means the graph did not take its explicit abstention route.

The run identifies source commit `e1a16919264c648cb953cfd92012f1caf8598da9` with `code_dirty=true`. The ZIP preserves outputs and bridge configuration but not the dirty source diff. Current-source reproductions are useful evidence for the findings above, but the commit alone is insufficient to reconstruct the exact historical working tree.

Prioritize a synthetic reproduction for the arbitration comparison, then preservation of quality diagnostics in empty-candidate decisions, then subject-specific photo guidance. Keep identification quality UNKNOWN until separately evaluated with reference evidence. No source or configuration was changed during this analysis.

Evidence locations, relative to the repository root:

- Original run: `evals/100 top/runs/2026-09-06-astra-all-055030-058/`.
- Retained bridge state: `.bridge/astra-photo058-20260906-055030/`.
- These directories are local, Git-ignored evidence. The committed report contains the analysis; the photographs, prompts, raw answers and log bundle remain local.
- Governing rules: [AGENTS.md](../../AGENTS.md), especially sections 3, 4.6 and 16.
