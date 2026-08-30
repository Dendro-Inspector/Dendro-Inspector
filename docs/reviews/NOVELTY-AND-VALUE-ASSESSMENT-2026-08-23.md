# Dendro Inspector novelty and value assessment

- **Status:** Point-in-time product and technical assessment of `07906c1`
- **Owner:** Dendro Inspector maintainers
- **Date:** 2026-08-23
- **Scope:** Repository architecture, current evaluation evidence, external prior art, and plausible product positioning

This document records an assessment, not an active project rule or a source of botanical
truth. Current behavior and policy remain owned by [`AGENTS.md`](../../AGENTS.md),
[`README.md`](../../README.md), and the living documents under `docs/`.

## Executive verdict

**Dendro Inspector is not a fundamentally new scientific method. It is a differentiated
and potentially valuable engineering integration whose field and commercial value have not
yet been demonstrated.**

Plant identification from photographs, bark classifiers, multiple-organ evidence, confidence
or abstention, human review, structured evidence, multi-agent criticism, and deterministic
verification all have prior art. It would therefore be misleading to market the project as
the first evidence-based plant identifier, the first cautious identification agent, or a new
classification algorithm.

The more defensible claim is narrower and more interesting: Dendro Inspector combines typed
multimodal evidence, exact subject attachment, card-bounded candidate admission, adversarial
review, optional arbitration, deterministic taxonomic-resolution and confidence ceilings,
traceability, and anti-overfitting evaluation governance in one provider-independent reference
implementation. The integration is distinctive. This review did not establish that it is
unique.

The project's strongest prospective value is not competing with broad consumer plant
identification applications on raw species coverage. It is acting as an **audit and uncertainty
control layer around probabilistic vision models**, especially where an unsupported precise
answer is more harmful than an explicit genus-level answer, abstention, or request for another
photograph.

### Assessment summary

The numbers below are judgement scores, not measured project metrics.

| Dimension | Assessment | Reason |
|---|---:|---|
| Novelty of the individual ideas | 3/10 | Most components have clear prior art. |
| Distinctiveness of the complete integration | 7/10 | The deterministic evidence-to-claim boundary is unusually thorough for a small open-source reference implementation. |
| Current research and engineering value | 7/10 | It provides inspectable contracts, traces, provider substitution, conformance cases, and a concrete uncertainty-control architecture. |
| Current field-identification readiness | 2-3/10 | The knowledge pack is unreviewed and field accuracy has not been established at scale. |
| Current commercial defensibility | 2/10 | No validated accuracy advantage, workflow outcome, proprietary data advantage, or demonstrated demand exists yet. |
| Potential value after independent validation | 7-8/10 | High if it measurably reduces unsupported claims while preserving useful coverage at acceptable cost and latency. |

## Question 1: Is the approach new?

### The underlying ideas are not new

Relevant prior art includes:

1. **Visual plant identification is established.** [Pl@ntNet](https://plantnet.org/en/about/)
   already provides large-scale AI-assisted plant identification with community-reviewed
   observations and continuing model improvement.
2. **Evidence-bounded taxonomic identification is established practice.**
   [iNaturalist's identification guidance](https://help.inaturalist.org/en/support/solutions/articles/151000170241-what-is-an-identification-)
   explicitly supports identifying only at the taxonomic rank justified by the available
   evidence and asks identifiers to independently support their conclusions rather than rely
   only on automated suggestions.
3. **Bark-based tree classification predates this project.** The BarkNet work,
   [“Tree Species Identification from Bark Images Using Convolutional Neural Networks”](https://www2.ift.ulaval.ca/~pgiguere/papers/tree-species-identification.pdf),
   demonstrates that tree classification from bark photographs is an established computer
   vision problem, not a new task introduced by Dendro Inspector.
4. **Multiple visual organs have already been combined.** Research such as
   [“Bark and leaf fusion systems to improve automatic tree species recognition”](https://www.sciencedirect.com/science/article/pii/S1574954117302078)
   explores combining distinct tree features rather than trusting a single view.
5. **Structured evidence and deterministic controls around model decisions are an active
   research direction.** [Omni-Decision](https://arxiv.org/abs/2607.11433) describes an
   explicit evidence state with deterministic updates, repair, and stopping behavior.
   [Safe LLM-based industrial process control](https://link.springer.com/article/10.1007/s43684-026-00136-1)
   similarly separates model proposals from deterministic verification in a different
   application domain.

These examples do not reproduce Dendro Inspector exactly. They do establish that its major
ideas cannot individually support a credible first-of-kind claim.

### The integration may be differentiated

Dendro Inspector's specific composition contains several mutually reinforcing controls:

- observations and inferences are separate frozen contracts;
- observations carry provenance, visibility, reliability, subject, and attachment state;
- only attached evidence from the relevant subject can support a claim;
- candidate tokens must match declared taxon-card vocabulary exactly;
- all generators, reviewers, and arbiters share the same deterministic admission boundary;
- evidence tier, card-supported resolution, reviewer bounds, and trusted support jointly cap
  the final identity;
- model reviewers can discover problems, but deterministic synthesis decides which findings
  and rerank recommendations are admissible;
- a provider failure is kept distinct from scientific insufficiency;
- the graph may lower resolution, abstain, retry once, request targeted evidence, or escalate;
- the final response is composed from deterministic state rather than delegated to another
  unconstrained model call;
- golden cases are governed as immutable benchmark assets and may not directly define card
  or threshold changes.

The canonical design statement is in [`docs/architecture.md`](../architecture.md): **a model
proposes; code adjudicates.** This is more than prompt wording; candidate admission, evidence
authority, routing, escalation, and the final claim cap are executable policy.

The novelty, if any, lies in this complete evidence-control system and its application to
dendrology—not in tree classification, multi-agent review, or confidence reporting alone.
This review was a targeted landscape check, not an exhaustive academic, commercial, or patent
search. Exact uniqueness therefore remains unknown.

## Question 2: Is it valuable?

### Present engineering value

As a reference implementation, the project already has meaningful engineering value:

- **It makes uncertainty executable.** “Be cautious” is replaced by gates that can reject
  unattached evidence, unsupported candidates, excessive taxonomic resolution, or inflated
  confidence.
- **It makes failures inspectable.** Typed intermediate artifacts and traces can show whether
  an error entered during planning, observation normalization, attachment, candidate
  generation, review, arbitration, or deterministic adjudication.
- **It separates model quality from policy quality.** Providers can be replaced while the
  same candidate and claim boundaries remain in force.
- **It treats abstention as an outcome.** An answer can state what is defensible and request
  the photograph most likely to improve the result.
- **It is evaluation-oriented.** Synthetic public cases test machinery independently from
  golden botanical accuracy cases.
- **It exposes integration defects.** Exact contracts can reveal mismatches that fluent model
  prose would otherwise conceal.

That makes Dendro Inspector useful as an architectural example for multimodal systems in which
models should contribute perception and judgement without owning the final authority.

### Present botanical value

The repository does not currently justify a claim of dependable field identification:

- [`AGENTS.md`](../../AGENTS.md) records 25 demonstration taxa with no dendrologist review.
- The same source states that the project has not been validated against real photographs at
  scale and that the conformance suite proves machinery rather than field accuracy.
- A live count for this assessment found 1,525 JPEG files under `evals/golden/`. That is a
  potentially useful evaluation asset, but a file count does not prove label correctness,
  specimen independence, representative sampling, blind execution, accuracy, or calibration.
- The current cards are deliberately marked as placeholder content. Their value is exercising
  the system boundary, not serving as an authoritative dendrology database.

Consequently, the system may produce a well-governed answer from incomplete or wrong botanical
rules. Deterministic adjudication prevents a model from exceeding the encoded policy; it cannot
make the encoded policy botanically correct.

### Evidence from the six-image birch pilot

The local provider comparison under
`evals/golden/birch/PROVIDER-COMPARISON-2026-08-22.md` is useful diagnostic evidence but not an
accuracy benchmark:

- the Ox-primary/Codex-arbiter run agreed with the collection label on 4 of 6 main subjects;
- the later Codex-only run agreed on 6 of 6;
- the six images represented only three capture events;
- verified botanical ground truth and specimen identity were absent;
- the Codex run was second and therefore not blind;
- Codex reviewed and arbitrated its own proposed artifacts;
- both cohorts ended at low confidence;
- the run exposed a `bark_pattern_or_leaf` versus dotted feature-name mismatch;
- the bridge trace did not independently attest which upstream agent authored each answer.

The valid conclusion is that both configurations could drive terminal, schema-valid Dendro
runs and that their label agreement differed in this tiny correlated pilot. It does **not**
establish 100% versus 67% accuracy, Codex superiority, provider independence, or field
reliability.

The pilot also illustrates why the architecture is useful: it preserved conservative final
claims and exposed a vocabulary defect. At the same time, the homogeneous Codex-only reviews
all passed despite that defect, demonstrating that multiple roles do not create independent
judgement when they use the same model and context.

### Present commercial value

Commercial value remains unproven. A general “photograph a plant and receive a name” product
would enter a mature category with strong incumbents, large observation corpora, community
review, broad taxonomic coverage, existing distribution, and network effects. Dendro Inspector
currently has none of those scale advantages.

The architecture also imposes costs that a user may not value in a casual identification
scenario:

- several model calls instead of one;
- higher latency and inference cost;
- more contracts and policy vocabulary to maintain;
- possible exact-token brittleness between observations and cards;
- conservative answers that may feel less satisfying than a confident species suggestion;
- an expert-review burden for every knowledge pack intended to carry authority.

Those costs are justified only where auditability, restraint, reproducibility, or the cost of
a false precise claim matters enough to pay for them.

## The strongest value proposition

The recommended positioning is:

> **A provider-independent evidence, review, and uncertainty-control engine for multimodal
> identification—not another consumer plant-identification application.**

Promising uses include:

1. **Professional triage and quality assurance.** Assist arborists, foresters, conservation
   teams, or collection managers while preserving evidence and uncertainty for human review.
2. **Audit layer around vision providers.** Apply one deterministic claim policy to outputs
   from different commercial or local multimodal models.
3. **Evaluation harness for multimodal agents.** Measure where a provider loses evidence,
   invents specificity, leaks evidence between subjects, or ignores contradictions.
4. **Education.** Show learners which visible features support a rank and what evidence would
   distinguish remaining candidates.
5. **Dataset review.** Flag weakly supported labels, ambiguous specimens, and missing views
   before records enter a curated collection.

The project becomes less valuable if it is reduced to a long sequence of model personas whose
outputs are accepted by another model. Its value depends on preserving the deterministic
boundary and demonstrating that the boundary improves measurable outcomes.

## What would establish real value

### 1. Establish trustworthy ground truth

- Define independent specimens or observation events rather than treating every photograph as
  independent.
- Have qualified reviewers verify labels and record the highest defensible taxonomic rank.
- Preserve disagreement rather than forcing consensus silently.
- Review a bounded taxon pack from independent dendrology sources before removing placeholder
  status.
- Keep benchmark photographs blind to prompts, card authoring, threshold selection, and model
  execution.

### 2. Pre-register a fair comparison

Compare at least:

- a direct single multimodal-model answer;
- the same model operating through Dendro Inspector;
- a different primary provider through the same Dendro policy;
- a heterogeneous primary/reviewer or primary/arbiter configuration;
- an established identification baseline where its terms and interfaces permit evaluation.

Group splits by specimen and capture event. Randomize run order. Prevent the expected label from
reaching the graph. Freeze prompts, cards, thresholds, code revision, model identifiers, and
provider settings before reading benchmark outcomes.

### 3. Measure the system's actual promise

Raw top-one species accuracy is insufficient. Report:

- correctness at the returned taxonomic rank;
- unsupported-specificity rate;
- selective accuracy as coverage changes through abstention;
- confidence calibration by declared confidence category;
- species, genus, family, and unknown outcomes separately;
- evidence attachment and subject-leakage errors;
- valid contradiction detection;
- whether the requested next photograph resolves the uncertainty;
- cross-provider outcome variance under the same policy;
- latency, model-call count, repair rate, and inference cost;
- denominators by specimen, capture event, taxon, organ, region, season, and image quality.

The owner should select quantitative success thresholds before the blind evaluation. Choosing a
threshold after seeing the results converts measurement into storytelling.

### 4. Demonstrate workflow value

A professional pilot should test whether the system:

- reduces review time without increasing unsupported claims;
- improves consistency between reviewers;
- produces traces that experts can actually audit;
- asks for feasible and informative follow-up photographs;
- helps users detect uncertainty rather than merely displaying more technical detail;
- creates enough benefit to justify its latency, cost, and maintenance burden.

### 5. Test the decisive product hypothesis

The central hypothesis should be stated narrowly:

> At an acceptable level of coverage, Dendro Inspector reduces unsupported taxonomic
> specificity and improves auditability relative to a direct model answer, at a cost and
> latency acceptable to the target workflow.

If a blind evaluation does not support that hypothesis, more agents or more elaborate prompts
will not create the intended value. The architecture would still be educational, but the
stronger product proposition would have failed.

## Principal risks

| Risk | Why it matters | Mitigation or evidence needed |
|---|---|---|
| Unreviewed botanical policy | Deterministic enforcement can consistently apply a wrong rule. | Independent card and label review with provenance. |
| Exact-vocabulary brittleness | Semantically correct observations may fail admission because tokens differ. | Vocabulary coverage diagnostics, contract tests, and domain-reviewed mappings. |
| Correlated model roles | Multiple passes by one model can repeat rather than discover the same error. | Heterogeneous blinded reviewers and deterministic negative controls. |
| Benchmark contamination | Golden-case-driven tuning destroys the meaning of the benchmark. | Continue enforcing `AGENTS.md` section 16 and require non-golden justification tests. |
| Weak market wedge | Consumer identification is already well served. | Validate a professional audit or QA workflow rather than generic identification demand. |
| Cost and latency | The graph makes more calls than a direct answer. | Measure benefit per added call and remove roles that add no independent value. |
| False impression of authority | Detailed traces may look scientific even when the knowledge pack is provisional. | Preserve placeholder warnings and communicate the validation boundary. |
| Provider attribution gaps | A bridge can obscure which upstream model produced an artifact. | Record upstream author/model identity and immutable run configuration in traces. |

## Recommended priority

Continuing the project is justified, but the next phase should emphasize evidence rather than
architectural expansion:

1. resolve known contract/specification mismatches through the conformance process;
2. obtain independent review for a bounded taxon and photograph cohort;
3. build a blind, specimen-grouped benchmark with frozen baselines;
4. measure unsupported specificity, calibration, abstention quality, and operating cost;
5. interview and observe a narrow professional user group;
6. add or remove model roles based on measured marginal value.

Do not add complexity merely to make the system look more agentic. A smaller graph that proves
better restraint is more valuable than a larger graph that only produces more opinions.

## Evidence-gated conclusions

Claim: The project's core scientific and product ideas are individually unprecedented.

Status: `UNKNOWN`, with the reviewed evidence weighing strongly against the claim.

Evidence: Established systems and research already cover photographic plant identification,
bark classification, evidence-bounded taxonomic identification, combined visual organs,
structured evidence state, and deterministic verification.

Not verified: An exhaustive literature, commercial-product, patent, and unpublished-work
search was not performed.

Next verification step: Conduct a systematic prior-art review with an explicit feature matrix
and documented search protocol before making any novelty claim.

---

Claim: The repository implements a differentiated evidence-to-claim control architecture.

Status: `VERIFIED`

Evidence: [`docs/architecture.md`](../architecture.md), the graph and schema files it links,
and the deterministic-boundary rule in [`AGENTS.md`](../../AGENTS.md) describe candidate
admission, evidence authority, subject attachment, review admissibility, routing, escalation,
and final claim caps as executable code-owned decisions.

Not verified: This review did not rerun the five mandatory gates or independently validate that
every documented property matches runtime behavior at `07906c1`.

Next verification step: Run the full gate set and negative-control probes, then attach their
outputs to a separate verification record.

---

Claim: The project currently provides dependable botanical identification at field scale.

Status: `UNKNOWN`

Evidence: The repository itself records that the 25-taxon knowledge pack is demonstration
content without dendrologist review and that real-photograph accuracy has not been validated at
scale. The 1,525 golden JPEGs counted during this review do not by themselves establish labels,
independence, representativeness, or performance.

Not verified: Field accuracy, rank correctness, calibration, regional and seasonal coverage,
and expert agreement.

Next verification step: Run a blinded, independently annotated, specimen-grouped benchmark and
report all denominators.

---

Claim: Dendro Inspector has established commercial value.

Status: `UNKNOWN`

Evidence: No measured professional workflow improvement, demand study, willingness-to-pay
evidence, field benchmark advantage, or defensible data advantage was found in scope.

Not verified: Target customer, frequency and cost of the problem, adoption barriers, acceptable
latency and price, and advantage over direct-model or established-application baselines.

Next verification step: Test the audit-layer proposition with a narrow professional cohort and
predefined outcome, cost, and adoption criteria.

## Bottom line

Dendro Inspector deserves continued work because it turns calibrated uncertainty from a prompt
request into inspectable software policy. That is a real engineering contribution. It does not
yet justify claims of scientific novelty, botanical reliability, or commercial success.

The shortest honest description is:

> **Not a new identification algorithm; a promising evidence-control architecture whose value
> now has to be proven with independent botanical and workflow evidence.**
