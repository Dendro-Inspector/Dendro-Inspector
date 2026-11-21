# 13 — Evaluation: how to test human augmentation

- **Status:** Draft — research proposal; protocol not signed
- **Owner:** Dendro Inspector maintainers
- **Date:** 2026-11-22
- **Last-verified:** 2026-11-22 (CURRENT statements checked against `36a961c`)

## Agent brief

| | |
|---|---|
| Spec ID | HAI-13 |
| Priority | **P1 — gate.** No capability in this directory may be described as an improvement until it passes the stage of this plan that matches the claim |
| Goal | A controlled comparison of Human Solo, Expert Panel, AI Solo and Human + Dendro, with pre-registered metrics and decision rules |
| Builds on | [`v1-heldout-protocol.md`](../../specs/v1-heldout-protocol.md) (unsigned): its unit of independence, outcomes, Wilson intervals and sign-off discipline are reused unchanged |
| Implementation approved | **No**. No study is authorised. Nothing here is run until a protocol is signed |

## Current state

- **CURRENT.** Public suite: 24 synthetic conformance cases, deterministic, proves
  machinery only ([evaluation](../../evaluation.md#recorded-results)).
- **CURRENT.** Real-photo figures are in-sample on 41 maintainer units
  ([`README.md`](../../../README.md#status)).
- **CURRENT.** Held-out tooling (`heldout-check`, group IDs, review status) and an unsigned
  protocol comparing graph and model alone.
- **CURRENT.** Outcome grading exists (`evaluation/label_scoring.py`; outcomes `correct`,
  `wrong`, `broader_only`, `wrong_broader`, `ambiguous_*`, `no_name`, `missing_target`,
  `unscoped`, `failed`).
- **Absent.** Any human-performance measurement.

## Groups

| Group | Who | Tools | Initial evidence |
|---|---|---|---|
| A — Human Solo | One qualified expert | Their usual references, no Dendro | Same photographs + same field access as D |
| B — Expert Panel | 3 qualified experts, independent first, then documented reconciliation | No Dendro | Same |
| C — AI Solo | Dendro, no human field judgement | — | Same photographs; no further observations |
| D — Human + Dendro | One qualified expert | Blind-first protocol ([08](08-human-ai-collaboration.md)), NBO ([05](05-active-field-investigator.md)) | Same |

C uses the configuration signed in the protocol (for example the v1 G1 settings). Arm M
(model alone) is reported beside C for reference.

## Hypotheses (pre-registered)

- **Primary (H1).** D's wrong-genus rate among named answers is lower than A's, and D's
  naming coverage is at least A's minus a margin δ.
- **Secondary.** H2 (efficiency), H3 (calibration) from [01](01-vision-and-research-hypothesis.md).
- **Separate, stronger (H4).** D is non-inferior to B on wrong-genus rate at comparable
  coverage. Tested only if H1 holds; reported as exploratory otherwise.

## Experimental controls

| Control | Specification |
|---|---|
| Ground truth | Established independently of all four groups: reproductive organs examined in the field, herbarium or laboratory confirmation where possible, at least two qualified labellers blind to each other; disagreement → third labeller; unresolved → labelled `not_determinable` and scored as an abstention case |
| Expert qualification | Stated bar (profession, years, regional experience), recorded per participant by role, never by name in the repository |
| Specimens | Held-out physical trees; no photograph of a study tree in any development directory (CURRENT `heldout-check` hash rule) |
| Composition | Look-alike genera, bark-only, leafless/winter, mixed organisms, uncarded genera, easy controls; distribution fixed before collection |
| Season and growth stage | At least two seasons; juvenile and mature trees where possible |
| Equal initial evidence | Every group starts from the same photograph set per tree |
| Field access | A and D may take the same budget of extra observations (count and time); B members get the same budget each |
| Randomisation | Tree order randomised per participant |
| Counterbalancing | Each expert does half the trees as A and half as D, crossed (no expert sees a tree twice); washout between blocks |
| Label leakage | Labellers are not participants; participants never see labels; Dendro never receives labels (CURRENT blind-by-default rule, `AGENTS.md` §16) |
| Reference development | No card, prompt or rule changes between sign-off and the end of data collection |
| Adjudication | Disputed outcomes graded by a blinded adjudicator who does not know the group |

## Metrics (each with count, denominator and 95% Wilson interval; per tree and per unit)

| Metric | Definition |
|---|---|
| Correct genus rate | `correct` / units with an established genus |
| Correct species rate | Only on units where the label established species and the group claimed species |
| Wrong genus rate among named answers | `wrong` / (`correct` + `wrong`) |
| Wrong species rate | Analogous, on species claims |
| Coverage | Named (genus or finer) / all units |
| Abstention rate and appropriateness | `no_name`/`broader_only` on `not_determinable` units vs on determinable ones |
| Risk-weighted error | Wrong names weighted by a pre-registered severity table (for example wrong genus across families > within family) |
| Time to defensible identification | Wall-clock from first photograph to stage-5 decision |
| Human effort | Additional observations, declined requests, interface time |
| Information per observation | Hypotheses eliminated per additional observation (D only, from NBO records) |
| Expert correction rate | D: stage-5 decision differs from Dendro's verdict |
| Automation-bias indicators | `changed_without_new_evidence` rate; correct→wrong vs wrong→correct changes |
| Calibration | Ordinal certainty vs realised accuracy (per-band accuracy with intervals) |
| Cost per correctly resolved tree | Model tokens and price on the day + human minutes |
| Reproducibility | Agreement of C on a repeated run of a random 20% subset |

Accuracy is never reported alone. Every summary shows the accuracy × coverage × reliability
× cost × time table.

## Statistical analysis plan

- **Paired design.** A vs D is within-expert, across different trees; the comparison unit is
  the physical tree. Primary test: mixed-effects logistic regression of `wrong` (among named
  answers) on group, with random effects for expert and tree; McNemar on per-tree paired
  outcomes where the same tree is seen by A and D through different experts, as a check.
- **Clustering.** Units of one tree are not independent; intervals and tests are per tree
  (cluster bootstrap where models do not converge).
- **Non-inferiority (H4).** Margin fixed before data; one-sided test.
- **Multiplicity.** One primary hypothesis; secondary results reported with unadjusted and
  Holm-adjusted p-values.
- **Inter-rater agreement.** Cohen's / Fleiss' kappa among labellers and panel members;
  latent-truth modelling (Dawid–Skene, README references) as a sensitivity analysis only.

### Sample size (illustrative, assumptions stated)

Computed for this spec with the Wilson score interval and Connor's approximation for paired
proportions (α = 0.05 two-sided, power 0.8). These are planning numbers, not targets:

| Question | Assumption | Needed |
|---|---|---|
| Show a wrong-name rate is below ~4% | 0 wrong among *n* named | *n* = 100 gives upper bound 3.7%; *n* = 30 gives 11.4% |
| Precision at a 5% observed rate | 5 wrong of 100 named | interval 2.2%–11.2% |
| Detect a 10-point paired difference | 20% discordant pairs | about 155 paired trees |
| Same, 15% discordant | | about 116 |
| Same, 25% discordant | | about 194 |
| Detect a 5-point difference | 20% discordant | about 626 |

Conclusion: a 30–40 tree pilot can find gross problems (safety, protocol friction, large
automation bias) but **cannot** demonstrate H1 unless the effect is very large. Saying
otherwise would be a false claim.

## Evaluation stages — what each can and cannot show

| Stage | Can show | Cannot show |
|---|---|---|
| 1. Synthetic policy conformance | Contracts and rules behave as specified | Anything about botany or humans |
| 2. Replay of archived cases | A policy change's effect on recorded evidence, deterministically, at zero model cost | Effect on new photographs; in-sample bias remains |
| 3. Small expert-reviewed pilot (30–40 trees) | Protocol feasibility, gross error classes, time cost, friction | Statistical superiority |
| 4. Independent held-out botanical evaluation | AI Solo accuracy on unseen trees (v1 protocol) | Human augmentation |
| 5. Human–AI controlled study | H1–H4 under controlled conditions | Field effectiveness under real constraints |
| 6. Prospective field study | Effect in real work, with real time pressure | Generality beyond the region and participants |
| 7. External replication | Independence from the original team | — |

Existing Dendro benchmark results inform priorities only. They are never evidence that the
system outperforms professional arborists.

## Decision rules (proposed, to be signed)

| Result | Decision |
|---|---|
| H1 holds | Continue to stage 6; publish with full table |
| H1 fails on wrong-name rate | Question the evidence presentation and blind-first protocol first ([17](17-evil-duck-review.md), Q15) |
| H1 fails on coverage only | Examine NBO stopping and abstention policy |
| Automation bias indicator high in D | Stop expanding features; redesign stage 3 |
| Underpowered | Collect more trees under the same protocol, unchanged |

## Acceptance criteria for running anything

- [ ] Protocol signed (table like the v1 sign-off), including δ, severity table, budget.
- [ ] Ethics/consent for expert participants; no personal data in the repository.
- [ ] Ground-truth procedure piloted on 5 trees.
