# 18 — Research roadmap and first experiments

- **Status:** Draft — research stages, not delivery commitments
- **Owner:** Dendro Inspector maintainers
- **Date:** 2026-11-22
- **Last-verified:** 2026-11-22 (CURRENT statements checked against `36a961c`)

## Agent brief

- **Next action for an agent, if the owner approves:** HAI-E1, then HAI-E2. Both are
  offline, deterministic, use no paid inference, and change no production component.
- Each experiment below lists the components that must stay untouched. An experiment that
  needs to change one of them is a different experiment and needs a new approval.
- Experiment code, if approved, goes into a run folder or `scripts/eval/` as an evaluation
  tool — never into the inspection path.

## Prioritisation

Scored ordinally (H = high, M = medium, L = low). "Measurable" = can improvement be
measured with data the project can obtain.

| Item | Scientific value | Evidence today | Feasibility | Cost | Operational risk | Reversible | Measurable |
|---|---|---|---|---|---|---|---|
| E1 decision-value audit of follow-up requests | M | CURRENT code, recorded traces | H | ~0 | none | yes | H |
| E2 look-alike coverage audit | M | CURRENT cards | H | ~0 | none | yes | H |
| HAI-04 C0 contrast telemetry | M | none | H | L | L | yes | M |
| HAI-05 R1 NBO | H (HYPOTHESIS) | weak (E1 pending) | M | L | L | yes | M |
| HAI-08 blind-first | H (HYPOTHESIS) | literature only | M | L code / M people | L | yes | M |
| HAI-06 provenance basis | M | CURRENT known limit | H | L | L | yes | M |
| HAI-07 sources registry | M | CURRENT free-text sources | M | M (reviewer time) | L | yes | L |
| HAI-11 extra reviewers | L–M | CURRENT evidence against more review | M | M | M | yes | M |
| HAI-09 registry | L now | none | L | H | M (privacy) | partly | L |
| HAI-10 sensors | L now | none | L | H | L | yes | L |

## Horizons

### Horizon 0 — preserve the existing system

- Keep this directory documentation-only; no production change.
- Record the baseline commit (`36a961c`) and the in-sample figures as the reference point.
- Sign the v1 held-out protocol before any accuracy claim
  ([`v1-heldout-protocol.md`](../../specs/v1-heldout-protocol.md)).
- List which cards and comparison tables lack independent review ([02](02-current-architecture-baseline.md)).

### Horizon 1 — evidence quality

- E1, E2 (below).
- HAI-04 C0: contrast artifacts as telemetry, for pairs with reviewed data only.
- HAI-06: provenance basis telemetry; continuity-photograph request wording.
- Focused synthetic regression cases for each new failure class (public suite style).

### Horizon 2 — investigation intelligence

- HAI-05 R1 behind a flag; HAI-E3 field pilot with a checklist baseline.
- HAI-08 blind-first package format; HAI-E4 pilot.
- Acquisition/safety table reviewed by a practitioner.

### Horizon 3 — longitudinal intelligence (only if Horizon 2 shows value)

- Two-visit investigations (Option B), then decide on a registry.
- Sources registry and reviewed update workflow ([07](07-scientific-knowledge-architecture.md)).
- Correction tiers ([12](12-learning-from-corrections.md)).

### Horizon 4 — prove value

- Independent held-out botanical benchmark; human–AI controlled study; prospective field
  study; external replication ([13](13-evaluation-and-benchmark-design.md)).

## First experiments

### HAI-E1 — Decision value of today's follow-up requests (recommended first)

| Field | Content |
|---|---|
| Research question | When the current engine abstains or names only a family, does the photograph it requests have any chance of changing the verdict under the engine's own rules? |
| Hypothesis | In a substantial share of such cases the requested observation cannot change the deterministic verdict for any value the cards declare; a contrast-based rule (minimax elimination over carded rivals, HAI-05 R1) picks a decision-relevant observation more often |
| Baseline | Current `final_decision._next_photo` / `photo_planner.choose_request` output, as recorded in traces |
| Intervention | Offline R1 ranking computed from the same packets and cards |
| Inputs | Recorded traces from past runs (local, private; never committed); current knowledge files |
| Required labels | None for the primary measure (it is a policy-level property) |
| Procedure | For each subject without a genus: take the requested target's bound features; for each value any carded candidate declares, add a synthetic trusted observation and recompute the verdict with the engine's own decision functions; mark the request decision-relevant if any value changes the verdict. Same for R1's top request. Report counts with denominators |
| Success criterion | R1's decision-relevant share exceeds baseline's on the same subjects, with a paired comparison reported (McNemar) |
| Failure criterion | No difference, or baseline already decision-relevant in nearly all cases → do not build R1; improve capture guidance instead |
| Resources | Laptop; minutes; deterministic |
| Paid inference | **No** |
| Must stay untouched | All of `src/` (the experiment imports functions, never edits them), cards, prompts, traces |
| Limits | Measures rule-level value only. A decision-relevant request can still be botanically useless if the cards are wrong. Synthetic observations assume perfect reading |

### HAI-E2 — Look-alike coverage audit

| Field | Content |
|---|---|
| Research question | For each declared confusion (`common_confusions`, comparison cards), does the knowledge base contain any character that separates the pair? |
| Hypothesis | Many declared confusions have no separating character in code-readable form |
| Baseline | n/a (descriptive) |
| Procedure | Enumerate pairs; for each, list features where both cards declare values and the value sets are disjoint; list pairs with none; list declared look-alikes without a card (Ulmus, Platanus, Acer negundo) |
| Success criterion | A complete table delivered; it becomes the work list for a qualified reviewer |
| Paid inference | **No** |
| Must stay untouched | Cards, comparison cards (read only) |
| Limits | Says nothing about whether the card values are botanically right |

### HAI-E3 — Field pilot: checklist vs current request vs R1 (needs approval and people)

| Field | Content |
|---|---|
| Question | Which guidance leads to a defensible identification with fewer additional observations? |
| Arms | (1) static capture checklist; (2) current single request; (3) R1 ranked NBO |
| Inputs | 30–40 physical trees, mostly look-alikes and winter trees, plus easy controls |
| Labels | Independent ground truth per [13](13-evaluation-and-benchmark-design.md) |
| Success | R1 resolves more trees per extra observation than the checklist, without more wrong names |
| Failure | Checklist ≥ R1 → stop NBO work |
| Paid inference | **Yes** — needs a stated token budget and owner approval |
| Must stay untouched | Inspection defaults; R1 runs only behind an evaluation flag |
| Limits | Underpowered for small effects (see sample sizes in [13](13-evaluation-and-benchmark-design.md)) |

### HAI-E4 — Blind-first feasibility pilot (needs approval and people)

| Field | Content |
|---|---|
| Question | Will practitioners complete a sealed stage-1 assessment, and how often do they change answers without new evidence? |
| Inputs | 3–5 experts, 20–30 archived cases each (photographs they have not seen) |
| Success | Stage-1 completion ≥ a pre-agreed share; measured change classes |
| Paid inference | No (archived AI answers) |
| Limits | Archived photographs only; no field access |

## What this roadmap does not promise

No dates, no accuracy targets, no percentage improvements. Every number above is either a
count to be measured or a sample-size calculation with its assumptions.
