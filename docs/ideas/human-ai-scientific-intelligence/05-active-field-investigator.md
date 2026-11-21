# 05 — Active field investigator

- **Status:** Draft — research proposal, not implemented
- **Owner:** Dendro Inspector maintainers
- **Date:** 2026-11-22
- **Last-verified:** 2026-11-22 (CURRENT statements checked against `36a961c`)

## Agent brief

| | |
|---|---|
| Spec ID | HAI-05 |
| Priority | P1 research — the highest-value direction in this package (HYPOTHESIS) |
| Goal | Replace "which photograph would help" with a ranked, safe, season-aware **Next Best Observation** (NBO) and a resumable investigation that keeps its evidence history |
| Implementation approved | **No** |
| Prerequisites | HAI-E1 decision-value audit ([18](18-research-roadmap.md)) shows the current request is often not decision-relevant; HAI-04 C0 for contrast-driven ranking |
| Must not touch | Final decision composition; admission; existing `PhotoRequest` contract (extend, never replace) |
| New model calls | None for ranking. Ranking is deterministic. A model may only *read* the new photograph, as extraction does today |

## Current state

- **CURRENT.** One `PhotoRequest(target, reason, subject_id)` per subject
  ([`schemas/decisions.py`](../../../src/dendro_inspector/schemas/decisions.py)).
- **CURRENT.** Choice order in `final_decision._next_photo`: nothing at fruit tier with high
  confidence → split firewood fixed request → attachment-first (`attachment_request`) →
  owed diagnostic combination on cut material → comparison-card discriminators not yet
  resolved, with photographs that cannot raise the tier moved back
  (`deprioritise_saturated_photos`) → the leader card's `follow_up_evidence`, minus resolved
  ones. On insufficient evidence, `photo_planner.choose_request` uses the declared object
  type.
- **CURRENT.** The rationale for saturation is recorded in code: two live birch runs asked for
  a bark macro when the next informative photograph was a leaf
  (`comparison_cards.deprioritise_saturated_photos` docstring).
- **CURRENT.** No notion of cost, safety, season availability, reach, or expected count of
  further observations. `CaseInput.season` is not read by the planner.
- **CURRENT.** No investigation state: a follow-up photograph starts an unrelated inspection.
  `user_challenges_previous_result` asks for reconsideration without history.

## Proposal

### 1. The question changes

From "what species is this photograph?" to "what evidence should we obtain next to most
efficiently distinguish the remaining hypotheses?" The NBO planner answers the second
question after every round, and it is allowed to answer **"nothing more is worth
obtaining"**.

### 2. Hypothesis set

The planner works over a deterministic **hypothesis set** per subject: admitted candidates,
open-world candidates (if enabled), and carded look-alikes declared for them
(`common_confusions`, comparison cards). Each hypothesis carries its current state:
`named`, `consistent`, `contradicted`, `not_comparable`.

### 3. Ranking without fake probabilities

LLM-stated confidence is not a calibrated posterior. The system has ordinal scores by
design ([architecture](../../architecture.md#ordinal-scores-not-percentages)). Two
rankers are specified; **R1 is the default; R2 is research only.**

**R1 — rule-based elimination ranking (PROPOSED, deterministic).** For each candidate
observation `o` (a contrast character on a reachable organ):

1. `separates(o)`: number of hypothesis pairs `o` would discriminate under HAI-04 tables,
   for every possible declared state, counting only pairs still `consistent`.
2. `worst_case_remaining(o)`: the largest hypothesis set left over any declared outcome of
   `o` (minimax elimination — a classic key-building heuristic, no probabilities needed).
3. `tier_gain(o)`: whether `o`'s tier is above the tier already reached at decisive trust
   (reuse `deprioritise_saturated_photos` logic).
4. `cost(o)`: ordinal `trivial | low | moderate | high` from a reviewed acquisition table
   (zoom photograph of an attached shoot = low; fallen fruit with provenance = low but
   capped; leaf-base macro of an out-of-reach crown = high).
5. Hard filters (never ranked): violates FC-1 to FC-3 ([03](03-human-arborist-field-workflow.md));
   organ not available this season (FC-5); already observed with trusted status;
   repetition of an observation that previously failed for the same reason twice.

Ranking: lexicographic — attachment-first (as today), then minimal
`worst_case_remaining`, then `tier_gain`, then lowest `cost`. Ties keep card order. Every
rank records which rule placed it.

**R2 — expected information gain (HYPOTHESIS, research only).** Expected entropy reduction
over the hypothesis set (Lindley 1956; Settles 2009 — see README references). It needs
`P(hypothesis | evidence)` and `P(state | hypothesis)`. Neither exists:

- `P(state | hypothesis)` could come from reviewed character-state frequencies in a flora;
  today cards hold no frequencies.
- `P(hypothesis | evidence)` would need a calibrated model or a regional base rate. Model
  self-reports are not admissible as probabilities.

R2 may be studied offline with **explicitly assumed** priors (uniform over consistent
hypotheses) and **reviewed** state frequencies, and compared against R1. It never ranks a
live request until a calibration study ([13](13-evaluation-and-benchmark-design.md)) shows
its probabilities are calibrated (for example, expected calibration error measured with
intervals on held-out units).

### 4. Stopping rule

The planner returns `stop` with a reason when any holds:

| Reason | Condition |
|---|---|
| `resolved` | The verdict is at the hypothesis set's finest defensible rank and no carded rival is `consistent` |
| `ceiling_reached` | No remaining observation's tier exceeds the reached tier at decisive trust |
| `nothing_obtainable` | Every discriminating observation is filtered (season, safety, reach, legality) |
| `budget_spent` | The human's declared observation or time budget is used |
| `no_discriminator_known` | Hypotheses remain but no contrast data separates them (knowledge gap — report, do not guess) |
| `repeated_failure` | The same observation failed to be readable twice |

`no_discriminator_known` is reported as a **knowledge-coverage gap**, the same class as
`knowledge_coverage_gap` today, never as insufficient photographs.

### 5. Resumable investigation

**PROPOSED.** An `Investigation` groups rounds for one target organism:

```mermaid
sequenceDiagram
    participant H as Human expert
    participant P as NBO planner (code)
    participant G as Graph (models + code)
    H->>G: Round 1 photographs + context
    G->>P: Evidence revision 1, hypothesis set, verdict
    P->>H: Ranked NBO list (or stop)
    H->>G: Round 2: new photographs, attestation "twig pulled from this trunk"
    G->>G: Extract round 2 only; previous revisions kept read-only
    G->>P: Evidence revision 2 merged under binding rules (06)
    P->>H: Updated NBO list or stop(resolved / ceiling / nothing_obtainable)
```

Rules:

- Earlier rounds' raw and effective packets are kept unchanged and hash-referenced, exactly
  as `evidence_history` keeps correction retries today
  ([architecture](../../architecture.md#evidence-revisions)).
- New photographs are extracted in their own call. Cross-round organism identity is **not**
  assumed: binding of a new photograph to the round-1 target follows
  [06](06-organism-binding-and-provenance.md) (user attestation, or a continuity
  photograph).
- The verdict is recomputed from all trusted evidence on the bound organism. A later round
  can lower a verdict as well as raise it.
- A request that was declined by the human ("cannot reach", "no fruit") is recorded and
  filters that observation for the rest of the investigation.

## Next Best Observation contract

```json
{
  "_comment": "PROPOSED CONTRACT — NOT IMPLEMENTED",
  "subject_id": "trunk_1",
  "target_description": "the large trunk in the centre of photo 1",
  "rank": 1,
  "observation": "leaf_base_macro",
  "characters": ["leaf.base_symmetry", "leaf.margin_serration"],
  "separates": [["alnus", "ulmus"]],
  "worst_case_remaining": 1,
  "tier_gain": false,
  "acquisition": {
    "cost": "low",
    "how": "Photograph the base of one leaf still attached to a low shoot of this trunk, leaf flat, base and petiole in focus, with a scale object.",
    "reachable_alternatives": ["zoom from the ground on an attached lower branch"],
    "alternative_limits": ["a zoomed photograph may not resolve base symmetry"]
  },
  "requires_attachment_proof": true,
  "season_available": ["spring", "summer", "autumn"],
  "safety_checked": ["no_climbing", "no_cutting", "public_access_assumed_unknown"],
  "ranked_by": ["attachment_first:n/a", "minimax:1", "tier_gain:no", "cost:low"],
  "would_not_change": "Bark photographs: bark tier already reached; cannot raise the claim."
}
```

`stop` is a first-class answer: `{"stop": "nothing_obtainable", "explanation": "...",
"revisit": {"season": "spring", "observation": "fruit_or_catkins"}}`.

## Worked examples (illustrative; botany UNVERIFIED)

| Situation | NBO rank 1 | Why | Stop condition |
|---|---|---|---|
| Alnus vs Ulmus, leaves attached, base unclear | Leaf base macro on an attached shoot | Separates the pair on a reviewed contrast (if one exists); attachment already confirmed | If no contrast data: `no_discriminator_known` — report gap |
| Quercus-like lobed leaves, attachment unresolved | **Continuity photograph**: the branch from trunk to the leaf cluster, or a user attestation | Attachment-first, as today; a leaf macro is worthless until ownership is settled | If continuity cannot be shown: verdict stays bounded; ask for fallen acorns *with stated provenance* as capped support |
| Leafless winter tree | Twig with buds and leaf scars (opposite/alternate, bud form) | Organs present in winter; leaves filtered by FC-5 | `revisit` spring/summer for leaves, autumn for fruit |
| Bark only | Attached foliage or reproductive structure; if out of reach, fallen material near the trunk with provenance stated | Bark tier saturated; second bark macro moved back (CURRENT logic) | `ceiling_reached` if nothing else is obtainable |
| Mixed organisms in frame | "Which trunk is the target?" — designation, then per-target NBO | No request is meaningful before the target is fixed | `stop: target_unresolved` |
| No reproductive structures this season | Vegetative discriminators first; fruit as `revisit` | FC-5 | `nothing_obtainable` now, with a dated revisit |
| Specimen cannot be revisited (felled, travelling) | Only observations obtainable now; no `revisit` entries | FC-6 | Verdict at current rank, gap recorded for the record |

## Invariants

1. Ranking is deterministic and replayable from the trace.
2. No request violates FC-1 to FC-3; the filter is a contract test over the acquisition
   table, not prompt wording.
3. The planner never changes the verdict. It reads it.
4. A declined or failed observation is never requested again in the same investigation
   without a changed reason.
5. Model confidence never enters ranking.

## Failure modes

| Failure | Control |
|---|---|
| Endless requests | Stopping rule; budget; `repeated_failure` |
| Unsafe or illegal request | Hard filter table, reviewed by a practitioner; contract test |
| Ranking dominated by wrong contrast data | Only reviewed contrast data ranks; unreviewed data can only add `candidate` requests flagged as such |
| Expert follows the NBO and ignores better field judgement | Requests are advisory; expert may decline with reason; declines are measured ([13](13-evaluation-and-benchmark-design.md)) |
| Round-2 photograph of a different tree | Binding rules ([06](06-organism-binding-and-provenance.md)); default is unbound |

## Evaluation

- **Offline, zero cost (HAI-E1):** on recorded traces, is today's request
  decision-relevant? (Would any declared outcome of the requested observation change the
  deterministic verdict?) Compare with R1 on the same packets. Synthetic outcomes only;
  measures policy value, not botanical value.
- **Field pilot (HAI-E3):** paired requests on the same trees; outcome = resolution per
  additional observation and time per resolved tree.
- **R2 research:** calibration study before any live use.

## Acceptance criteria for a future PR

- [ ] `PhotoRequest` extended compatibly (new optional fields) or a new `NextBestObservation`
      beside it; old consumers unaffected.
- [ ] Acquisition and safety table reviewed by a named practitioner.
- [ ] Behind a setting, default off; public suite decisions unchanged.
- [ ] Replay reproduces every ranking from a stored trace.

## Open questions

- Who maintains the acquisition-cost table, and per region?
- Should "no_discriminator_known" open a knowledge-review item automatically (as a queue
  entry, never a card edit)? Recommendation: yes, into a review queue like
  `knowledge/review-queue/`.
