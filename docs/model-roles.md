# Model roles and escalation

- **Status:** Current
- **Owner:** Dendro Inspector maintainers
- **Date:** 2026-07-27
- **Last-verified:** 2026-07-27

Two logical roles. Business logic names only these; which vendor and model satisfies each is
configuration.

## `primary`

Plans, inspects the image, extracts evidence, generates candidates, performs the first-pass
reviews, and (in a future revision) composes the answer. Needs strong multimodal grounding
and reliable structured output.

```bash
DENDRO_PRIMARY_PROVIDER=openai
DENDRO_PRIMARY_MODEL=gpt-5.6
```

## `arbiter`

Independently challenges a disputed or high-risk result: identifies unsupported claims,
finds overlooked alternatives, assesses overconfidence, and recommends the highest
defensible taxonomic level.

```bash
DENDRO_ARBITER_PROVIDER=anthropic
DENDRO_ARBITER_MODEL=claude-opus-5
```

**Bind the arbiter to a different model family than the primary.** Two instances of the same
model share failure modes, and a model that agrees with itself is not a second opinion — it
is the same opinion, billed twice.

## What the arbiter receives

Original images, original user context, the evidence packet, the candidate set, the proposed
resolution and confidence, and the relevant taxon and comparison cards.

**It never receives the primary model's private reasoning.** This is structural, not a
policy: the system stores no hidden chain-of-thought anywhere, so there is nothing to pass
on. What it cannot see, it cannot be anchored by.

## What the arbiter can and cannot do

It returns **structured findings only**. It cannot write the answer.

To change the ranking it must supply `recommended_candidates` **in the same `ReviewResult`**
as a finding with `required_action: rerank_candidates`, for the same unambiguous subject. The
ranking passes the shared candidate validator, and `proposed_taxon`, when present, must survive.
A recommendation without that exact admitted finding changes nothing; a finding without a
validated recommendation changes nothing.

Synthesis stores the accepted finding and validated ranking together as `AdmittedRerank`.
Final decision consumes only that artifact, never raw recommendations. One unambiguous arbiter
rerank takes precedence over internal reranks; conflicting arbiter rankings preserve the
current order rather than choosing arbitrarily.

Its findings then go through `adjudicate()` — the *same* function the internal reviewers
face, with deterministic findings adjudicated first. A second model does not get a lower bar
because it is expensive or because it disagreed confidently. See
[`docs/review-pipeline.md`](review-pipeline.md).

## Escalation policy

Fully configurable via `EscalationPolicy`. Every trigger is individually switchable, so an
operator can tune cost against risk without editing code, and so evaluation can measure
escalation precision and recall.

### Triggers

| Trigger | Hard? | Why |
| --- | --- | --- |
| `species_level_proposed` | yes | The claim most likely to be wrong and most likely to be believed |
| `possible_multiple_taxa` | yes | Averaging two subjects into one answer is a silent, plausible error |
| `user_challenged_result` | yes | The user has information the system does not |
| `instruction_like_content_detected` | yes | Untrusted content in play; a second look is cheap |
| `unresolved_contradiction` | yes | A critical finding survived adjudication |
| `high_confidence_proposed` | no | Confidence is the claim worth double-checking |
| `leading_candidates_close` | no | The ranking is doing work the evidence may not support |
| `reviewers_disagree_or_critical_finding` | no | Internal review did not converge |
| `bark_colour_dependence` | no | The single most common overweighted feature |
| `bark_only_input` | no | Structurally the weakest input class |
| `forced_by_configuration` | yes | Explicit operator override |

### Suppressors

**Blocking** — a second opinion could not help; these override everything:

* `evidence_insufficient` — arbitrating "I cannot tell" yields "I cannot tell", at twice
  the price.
* `already_abstaining`.

**Cost** — these trade risk for money and are overridden by any hard trigger:

* `broad_and_low_risk` — a clean genus-or-broader result across all subjects.
* `clean_review_and_modest_confidence` — no accepted findings, confidence not high.

### Precedence

```text
policy disabled        -> no
blocking suppressor    -> no
hard trigger           -> YES        (cost suppressors cannot override this)
cost suppressor        -> no
any remaining trigger  -> yes
```

The hard-trigger tier exists because of a specific failure: a two-log photograph produced a
clean, broad, cheap-looking result, and `broad_and_low_risk` suppressed the escalation that
the mixed-taxa flag had correctly requested. A cost suppressor must never talk the gate out
of a safety trigger. Regression-tested in
`tests/unit/test_escalation_gate.py::TestPrecedence`.

## Cost

The arbiter roughly doubles model cost on escalated cases. The public conformance suite is
deliberately weighted toward hard cases, so its escalation rate is not a production cost
forecast. v0.2.3 expands the suite from sixteen to nineteen cases. Tune with:

```python
EscalationPolicy(
    on_close_leading_candidates=False,  # the most frequent soft trigger
    on_bark_colour_dependence=False,  # if your inputs are mostly bark
)
```

Measure the effect with `dendro eval --suite public`: `escalation_precision`,
`escalation_recall` and `unnecessary_arbiter_call_rate` are all reported.

Do not disable the hard triggers to save money. They exist for the cases where a wrong
answer is both most likely and most costly.

## Adding a provider

1. implement the `ModelProvider` Protocol in `providers/` (lazy-import the SDK);
2. add a value to the `Adapter` enum;
3. add a branch to `providers/registry.py:build_provider`.

Nodes need no change. `assert_never` in the registry's match statement makes a forgotten
branch a type error rather than a runtime surprise.

## Implementation references

- [`src/dendro_inspector/config.py`](../src/dendro_inspector/config.py) — `Role`, `EscalationPolicy`
- [`src/dendro_inspector/nodes/escalation_gate.py`](../src/dendro_inspector/nodes/escalation_gate.py)
- [`src/dendro_inspector/providers/`](../src/dendro_inspector/providers)
- [`prompts/nodes/arbiter.md`](../prompts/nodes/arbiter.md)
