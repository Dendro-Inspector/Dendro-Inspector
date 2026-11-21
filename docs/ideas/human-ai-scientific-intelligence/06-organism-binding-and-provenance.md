# 06 — Organism binding and evidence provenance

- **Status:** Draft — research proposal, not implemented
- **Owner:** Dendro Inspector maintainers
- **Date:** 2026-11-22
- **Last-verified:** 2026-11-22 (CURRENT statements checked against `36a961c`)

## Agent brief

| | |
|---|---|
| Spec ID | HAI-06 |
| Priority | P1 research |
| Principle | **One observation must not silently become evidence for a different organism** |
| Goal | Grade how an observation's ownership is known, add independent verification routes, and keep organism identity stable across photographs and rounds |
| Implementation approved | **No** |
| Must not touch | Existing `AttachmentStatus` values and their trust semantics; `attachment_authority_gate` policy; part-attestation rules |
| New model calls | Optional, research only: an independent provenance reviewer (HAI-11). Never decides; only proposes |

## Current state

- **CURRENT.** `Observation.attachment` on detachable families: `confirmed_attached`,
  `confirmed_detached`, `unknown`, `user_attested`
  ([`schemas/evidence.py`](../../../src/dendro_inspector/schemas/evidence.py)). Only
  `confirmed_attached` counts at full tier; `user_attested` is capped support.
- **CURRENT.** `confirmed_attached` is a perception claim by the extractor. The attachment
  authority gate re-runs admission with critical detachable support demoted, and demotes
  only where sensitivity intersects risk (`attachment_risk_for`)
  ([`attachment_authority_gate.py`](../../../src/dendro_inspector/nodes/attachment_authority_gate.py)).
- **CURRENT.** The gate's own "Known limit": if the extractor says `confirmed_attached` about
  a crossing branch and flags nothing, nothing deterministic contradicts it.
- **CURRENT.** `parent_subject_id` components are re-scoped to the root; the projection is
  not a second witness.
- **CURRENT.** Arm V names only single-organism scenes; the experimental binder accepts a
  model's subject choice only under strict photograph-exclusivity rules
  ([evaluation](../../evaluation.md#experimental-explicit-subject-binding-opt-in)).
- **CURRENT.** Subjects are local to one inspection; nothing links a subject in one case to a
  subject in another.

## Proposal

### 1. Provenance grades (PROPOSED)

Keep `AttachmentStatus` as the decision input. Add a separate, richer **provenance basis**
recorded beside it, so the report can say *how* ownership is known:

| Basis | Who establishes it | Trust it may earn (never more than today's status allows) |
|---|---|---|
| `visual_continuity_confirmed` | Code-checked continuity photograph (see 3) plus extractor reading | Full tier (`confirmed_attached`) |
| `extractor_asserted` | Extractor alone | Today's `confirmed_attached` semantics, flagged `single_witness` |
| `user_attested` | Uploader statement (CURRENT channel) | Capped (CURRENT) |
| `field_verified_by_expert` | Qualified person in Blind-first protocol, recorded with identity ([08](08-human-ai-collaboration.md)) | Capped until a study shows expert attestation error is low; then owner decision |
| `independent_review_agrees` | A second model or reviewer, different family, agrees | Never raises trust alone; can only *lower* (disagreement → `unknown`) |
| `unresolved` | — | Context |
| `confirmed_non_attachment` | Any of the above | Context; report as a different organism's part |

Rule: **agreement of a second model never upgrades ownership; disagreement downgrades it.**
A model asserting attachment twice is one witness.

### 2. Organism identity independent of segmentation

A `subject_id` is the extractor's local label and changes between calls. Proposed
`OrganismBinding` records give each organism a stable ID **within an investigation** keyed by
evidence, not by label:

- anchor photographs where the organism is shown alone (the binder's exclusivity rule);
- the human's designation ("the trunk with the white paint mark");
- attestation links ("twig in photo 5 pulled from trunk in photo 1").

When re-extraction relabels subjects, binding is re-established by these anchors; if it
cannot be, the observations stay `unbound` and count for nothing. Identity across
investigations is [09](09-longitudinal-tree-intelligence.md) and is deferred.

### 3. Verification routes

| Route | What it checks | Cost | Status |
|---|---|---|---|
| Continuity photograph | One frame showing trunk → branch → leaf cluster without a gap | One photograph | PROPOSED: NBO requests it first (CURRENT planner already asks attachment-first) |
| Human manipulation attestation | "I pulled this twig; the branch moved with this trunk" | Seconds in the field | PROPOSED: extends `PartAttestation` with a `method` field |
| Segmentation-assisted check | Mask continuity between trunk and foliage | New dependency, compute | DEFERRED: needs evidence that it beats a continuity photograph ([10](10-multimodal-measurement.md)) |
| Independent provenance reviewer | A different model family judges ownership from the photographs | One call | Research only (HAI-11), can only downgrade |
| Counterfactual removal | Verdict with and without the observation | Zero (CURRENT gate) | Extend telemetry to all decision-critical evidence, not only detachable |

### 4. Contamination safeguards

1. Evidence from organism X never supports organism Y unless a binding record links them
   with an allowed basis.
2. A photograph that shows several organisms cannot be the sole anchor for any of them.
3. Cross-round evidence is unbound by default.
4. A detached part is its own organism (CURRENT rule, kept).
5. Every final verdict lists the provenance basis of each supporting observation.

### 5. Expert-facing evidence view (specification, no UI)

For each supporting or contradicting observation, the view must show:

| Field | Source |
|---|---|
| Feature and value, in words | `Observation` |
| Source photograph and region (if recorded) | `image_id`, extractor region notes |
| Organism it is bound to, in words | `OrganismBinding` |
| Attachment status and provenance basis | this spec |
| Trust and tier after projection | `project_evidence` |
| Which candidate used it, for or against | `observation_uses` (CURRENT trace field) |
| What changes if withdrawn | Counterfactual verdict (CURRENT gate telemetry, extended) |

The view is read-only. An expert may mark an observation "wrong reading" or "wrong owner";
that mark enters [12](12-learning-from-corrections.md) as raw feedback, never a direct
edit.

## Invariants

1. No path raises an observation's trust above today's `AttachmentStatus` semantics.
2. A model's agreement is never a second witness.
3. Unbound evidence counts for nothing.
4. Bindings are recorded with basis and author and are replayable.

## Failure modes

| Failure | Control |
|---|---|
| Continuity photograph that shows overlap, not continuity | Extractor must mark continuity `clear`; ambiguous → `unresolved`; expert view shows the frame |
| User attests wrongly (honest mistake) | Capped trust; measured in the study (attestation error rate) |
| Binding records multiply complexity for single-tree photos | Single organism: binding is implicit (CURRENT behaviour) |
| Stable ID collision across re-extractions | Anchors required; otherwise unbound |

## Evaluation

- Synthetic: crossing-branch, detached-leaf, two-trunk, felled-log-and-stump packets.
- Recorded traces: how many verdicts rest on `extractor_asserted` ownership alone
  (zero-cost count).
- Field: attestation error rate when experts later verify by hand (HAI-E3).

## Acceptance criteria for a future PR

- [ ] Provenance basis is additive telemetry first; no verdict changes.
- [ ] `PartAttestation.method` optional, defaulting to today's meaning.
- [ ] Contamination safeguards each have a failing-before test.

## Open questions

- Is a single continuity photograph from the ground reliable enough for tall crowns?
  **UNKNOWN** — needs a field measurement.
- Should `field_verified_by_expert` ever earn full tier? Owner decision after data.
