# Human–AI Scientific Intelligence — research specifications

- **Status:** Draft — research proposals; nothing here is implemented or approved
- **Owner:** Dendro Inspector maintainers
- **Date:** 2026-11-22
- **Last-verified:** 2026-11-22 (statements marked CURRENT checked against commit `36a961c`)

This directory is a set of **research specifications**. They describe how Dendro Inspector
could change from a system that names trees from a fixed set of photographs into one that
helps a qualified person carry out a better, evidence-based field investigation. They are
written for **AI coding agents first** (Claude Code, Codex) and for human maintainers,
arborists and reviewers second.

> Better reasoning over existing photographs is useful. Obtaining the right new evidence
> may be far more valuable.

**Nothing in this directory is production functionality.** A statement describes the
running system only when it is labelled **CURRENT** and cites a file. Everything else is a
proposal or a hypothesis.

## Baseline

| Item | Value |
|---|---|
| Repository | `Dendro-Inspector/Dendro-Inspector` |
| Starting commit | `36a961c951f0f937f370fd53a3fc31b1e61b92e7` (`origin/main`, 2026-11-22, merge of PR #71) |
| Package version | `1.0.0a1` (research preview) |
| Branch | `research/human-ai-arborist-intelligence` (documentation only) |

## Agent entry point — read before acting on any spec

An AI agent that picks up a spec from this directory follows these rules. They restate
`AGENTS.md`; where they disagree, [`AGENTS.md`](../../../AGENTS.md) wins.

1. **These specs authorise nothing.** No spec here is approved for implementation. Before
   writing code, get the owner's explicit approval for that spec in the current session
   (`AGENTS.md` §2). Approval for one spec does not extend to another.
2. **Re-verify CURRENT claims.** Every CURRENT statement was true at `36a961c`. Re-read the
   cited file before relying on it (`AGENTS.md` §1.5). If it changed, update the spec first.
3. **Keep the determinism boundary.** A model proposes; code adjudicates (`AGENTS.md` §4.6).
   No spec here moves admission, resolution, confidence, escalation or the user-facing
   answer into a model call. A spec that seems to require that is wrong; stop and report.
4. **Flags off, defaults unchanged.** Any future implementation ships behind a setting that
   defaults to the released behaviour, with the public suite (`dendro eval --suite public`)
   unchanged.
5. **No benchmark tuning.** Cards, prompts, thresholds and routing are never changed to fix
   one benchmark case (`AGENTS.md` §16). The case studies in `20-` and `21-` are not ground
   truth and never justify a card edit.
6. **No paid runs without a budget.** No spec authorises live model calls. Any experiment
   that needs paid inference states its budget and waits for the owner.
7. **No real photographs, locations or personal data** in the repository
   ([dataset policy](../../dataset-policy.md)).
8. **Report with the `AGENTS.md` §3 vocabulary**: `VERIFIED`, `IMPLEMENTED-UNVERIFIED`,
   `UNKNOWN`, `BLOCKED`.

Each capability spec (`04-` to `12-`) has the same sections, so an agent can find what it
needs: **Agent brief**, **Current state**, **Proposal**, **Contract sketch**, **Invariants**,
**Abstain and stop conditions**, **Failure modes**, **Evaluation**, **Acceptance criteria for
a future PR**, **Open questions**.

## Claim labels

| Label | Meaning |
|---|---|
| **CURRENT** | Verified in repository code or documentation at `36a961c`, with a citation |
| **PROPOSED** | A future architectural concept; not implemented |
| **HYPOTHESIS** | An expected benefit nobody has measured |
| **EVIDENCE** | A measured result or an independently supported source, with its denominator |
| **UNKNOWN** | Unresolved or not verified; says what would resolve it |
| **REJECTED** | Considered and not recommended, with the reason |

Botanical statements in these specs (which characters separate which genera) are
**general botanical knowledge written by an AI agent**. They are not verified by a
dendrologist and are marked UNKNOWN until a qualified reviewer checks them against a cited
flora. They must not be copied into a taxon card on the strength of this directory.

## Current state versus proposed state

| Area | CURRENT (at `36a961c`) | PROPOSED here |
|---|---|---|
| Unit of work | One inspection of up to 16 photographs (`CaseInput`) | An investigation that can resume with new evidence ([05](05-active-field-investigator.md)) |
| Next photograph | One rule-chosen `PhotoRequest` per subject | A ranked, safety-checked Next Best Observation with a stopping rule ([05](05-active-field-investigator.md)) |
| Look-alikes | 8 placeholder comparison cards; single-card admission | Explicit pairwise contrast tables with abstention ([04](04-contrastive-taxonomy-engine.md)) |
| Attachment | Extractor-asserted, with counterfactual gate and uploader attestation | Graded provenance with independent verification routes ([06](06-organism-binding-and-provenance.md)) |
| Knowledge | 28 AI-reviewed taxon cards, rule-level provenance | Citation-level sources and a reviewed update workflow ([07](07-scientific-knowledge-architecture.md)) |
| Human review | Export after the AI answer, import as revisions | Blind first, AI second ([08](08-human-ai-collaboration.md)) |
| Memory | None across inspections | Persistent tree record ([09](09-longitudinal-tree-intelligence.md)), deferred |
| Evaluation | Conformance suite; in-sample real-photo runs; unsigned held-out protocol | Four-group human–AI study ([13](13-evaluation-and-benchmark-design.md)) |

The full inventory, with citations, is [02 — current architecture baseline](02-current-architecture-baseline.md).

## Reading order

| # | Spec | Purpose | Agent priority |
|---|---|---|---|
| 01 | [Vision and research hypothesis](01-vision-and-research-hypothesis.md) | What is claimed and what is not | read |
| 02 | [Current architecture baseline](02-current-architecture-baseline.md) | Capability inventory with citations | **read first** |
| 03 | [Human arborist field workflow](03-human-arborist-field-workflow.md) | What a field identification actually involves | read |
| 04 | [Contrastive taxonomy engine](04-contrastive-taxonomy-engine.md) | Pairwise discrimination between look-alikes | **P1** |
| 05 | [Active field investigator](05-active-field-investigator.md) | Next Best Observation and resumable investigations | **P1** |
| 06 | [Organism binding and provenance](06-organism-binding-and-provenance.md) | Whose evidence is this | **P1** |
| 07 | [Scientific knowledge architecture](07-scientific-knowledge-architecture.md) | Sources, citations, update workflow | P2 |
| 08 | [Human–AI collaboration](08-human-ai-collaboration.md) | Blind-first expert protocol | **P1** |
| 09 | [Longitudinal tree intelligence](09-longitudinal-tree-intelligence.md) | Persistent tree records | P3 (deferred) |
| 10 | [Multimodal measurement](10-multimodal-measurement.md) | Which extra modalities are worth it | P3 |
| 11 | [Adversarial scientific review](11-adversarial-scientific-review.md) | Specialised, selectively routed reviewers | P2 |
| 12 | [Learning from corrections](12-learning-from-corrections.md) | Tiered corrections without poisoning | P2 |
| 13 | [Evaluation and benchmark design](13-evaluation-and-benchmark-design.md) | How to prove human augmentation | **P1 (gate)** |
| 14 | [Architecture options](14-architecture-options.md) | Options A, B, C with diagrams | read |
| 15 | [Proposed data contracts](15-proposed-data-contracts.md) | Entity sketches — NOT IMPLEMENTED | reference |
| 16 | [Risks and failure modes](16-risks-and-failure-modes.md) | Detection, prevention, tests | read |
| 17 | [Evil Duck review](17-evil-duck-review.md) | Reasons this architecture might be wrong | **read before building anything** |
| 18 | [Research roadmap](18-research-roadmap.md) | Horizons and first experiments | **P1** |
| 19 | [Open research questions](19-open-research-questions.md) | What is not known | reference |
| 20 | [Case study: Alnus versus Ulmus](20-case-study-alnus-ulmus.md) | Evidence-policy case, not ground truth | reference |
| 21 | [Case study: Quercus foliage binding](21-case-study-quercus.md) | Evidence-binding case, not ground truth | reference |
| — | [Decision log](decision-log.md) | Existing, proposed, open, rejected, deferred | reference |
| — | [Future backlog](future-backlog.md) | Actionable research items for agents | **P1** |

P1 means "first candidates for a funded experiment", not "approved".

## Priority overview

1. **Measure before building.** The first experiments in [18](18-research-roadmap.md) cost
   no paid inference: an offline decision-value audit of the current next-photograph
   requests, and a look-alike coverage audit of the knowledge base.
2. **Contrastive taxonomy + Active Field Investigator + Blind-first expert review** are the
   three capabilities with the clearest link to the central hypothesis. Each must show its
   own incremental value ([17](17-evil-duck-review.md), question 6).
3. **Persistent tree identity, new sensors and continuous learning are deferred** until the
   first three show value under [13](13-evaluation-and-benchmark-design.md).

## External references and their verification status

The specs cite a small number of external works. They were recalled from general knowledge
and **not re-retrieved in this session (2026-11-22)**. Verify each before quoting it in a
publication or a card.

| Reference | Used in | Status |
|---|---|---|
| D. V. Lindley (1956), "On a measure of the information provided by an experiment", *Annals of Mathematical Statistics* 27(4) | 05 | recalled, unverified |
| B. Settles (2009), *Active Learning Literature Survey*, Univ. of Wisconsin–Madison CS Tech. Report 1648 | 05 | recalled, unverified |
| C. Guo et al. (2017), "On Calibration of Modern Neural Networks", ICML | 05, 11 | recalled, unverified |
| R. Parasuraman, D. H. Manzey (2010), "Complacency and bias in human use of automation", *Human Factors* 52(3) | 08, 16 | recalled, unverified |
| G. Bansal et al. (2021), "Does the Whole Exceed its Parts? The Effect of AI Explanations on Complementary Team Performance", CHI | 08, 13 | recalled, unverified |
| Z. Buçinca, M. B. Malaya, K. Z. Gajos (2021), "To Trust or to Think: Cognitive Forcing Functions Can Reduce Overreliance on AI", CSCW | 08 | recalled, unverified |
| A. P. Dawid, A. M. Skene (1979), "Maximum likelihood estimation of observer error-rates using the EM algorithm", *Applied Statistics* 28(1) | 13 | recalled, unverified |
| M. J. Dallwitz (1980), DELTA format for taxonomic descriptions, *Taxon* 29 | 04 | recalled, unverified |
| TDWG Structured Descriptive Data (SDD) standard | 04, 07 | existence recalled, content unverified |
| Pl@ntNet, iNaturalist identification guidance | 17 | linked and discussed in [`NOVELTY-AND-VALUE-ASSESSMENT-2026-08-23.md`](../../reviews/NOVELTY-AND-VALUE-ASSESSMENT-2026-08-23.md) |

No DOI, page number or quotation is given for any of them on purpose: none was checked.

## Implementation references

- [`AGENTS.md`](../../../AGENTS.md) — rules every spec inherits
- [`docs/architecture.md`](../../architecture.md), [`docs/agent-graph.md`](../../agent-graph.md),
  [`docs/review-pipeline.md`](../../review-pipeline.md), [`docs/evaluation.md`](../../evaluation.md),
  [`docs/human-review.md`](../../human-review.md), [`docs/dataset-policy.md`](../../dataset-policy.md)
- [`docs/specs/v1-heldout-protocol.md`](../../specs/v1-heldout-protocol.md) — the protocol any study here builds on
