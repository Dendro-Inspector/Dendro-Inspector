# Evaluation

- **Status:** Current
- **Owner:** Evil Duck Dendro Inspector maintainers
- **Date:** 2026-07-25
- **Last-verified:** 2026-07-25

```bash
evil-duck eval --suite public            # summary
evil-duck eval --suite public --verbose  # every assertion, including passes
evil-duck eval --suite public --json --out report.json
```

The suite is deterministic: each case replays a recorded provider scenario from
`evals/fixtures/`. No network, no credentials, no cost, no flake — which is why it runs in CI
on every pull request, including pull requests from forks, which never have repository
secrets.

## What assertions check

Assertions run against the **graph's decisions**, never against prose. Rewriting the tone
layer must not turn the evaluation red; changing what the system concludes must.

## The nine cases

Cases 1–5 cover the core mechanics. Cases 6–8 are the named failure modes from section 13 of
the domain prompt. Case 9 is the counterweight — proof the system still commits when the
evidence is actually decisive.

### 1. `conifer-log-001` — probable Pinus

The happy path, and the definition of "good": a genus-level answer with honest confidence,
no species claim, reached without paying for an arbiter call.

Expects: `pinus`, genus, confidence ≤ medium, status `probable`, no escalation, no retries.

### 2. `insufficient-bark-001` — bark-only, nothing decisive

The most important case in the suite. A system that cannot say "I do not know, and here is
the photograph that would tell us" will invent an answer instead, and a plausible invented
genus is worse than no answer.

Expects: resolution `unknown`, status `insufficient_evidence`, a targeted photo request, no
retries.

### 3. `mixed-taxa-001` — two genera in one frame

Guards a subtle, common failure: averaging two subjects into one confident answer, or
supporting a claim about the far log with a feature only visible on the near one.

The fixture **deliberately** cites `obs-1` (which belongs to `foreground_log_1`) in a
candidate for `background_log_1`. The candidate generator must strip it. Do not "fix" that
fixture.

Expects: ≥ 2 subjects, per-subject conclusions, no evidence leakage, escalation.

### 4. `colour-overweighting-001` — colour dependence

**All three model reviewers return `pass`.** The deterministic colour check has to catch it
alone. If someone removes that check because "the reviewers handle it", this case goes red
immediately — which is the entire point of keeping it.

Expects: an accepted `colour_overweighting` finding, confidence ≤ low, escalation.

### 5. `arbiter-ranking-001` — the arbiter changes the ranking

Proves the escalation path does real work. The primary proposes Pinus at species level
against Picea-shaped evidence.

Expects: `picea` selected, genus resolution, escalation, a recorded candidate delta.

### 6. `light-trunk-birch-001` — FAILURE 1, "світлий стовбур = береза"

A pale trunk at distance. The model proposes Betula and names no alternative. White poplar
has pale bark too and the photograph cannot separate them, so the comparison-card check must
surface it unprompted.

Expects: Betula at genus, confidence ≤ low, evidence tier 3 (bark), an alternative named that
the model did not propose, escalation, a photo request.

### 7. `rough-bark-oak-claim-001` — FAILURE 2 and 3, the user says oak

Deep fissures on a massive weathered trunk, no foliage; the model leans ash. Oak, ash,
walnut, poplar and old apple all share this bark.

Expects: the user's version ruled `possible` and **never** `rejected`, confidence ≤ low,
evidence tier 3. This is the restraint rule — "я не маю права агресивно відкидати твою
версію по одній ділянці кори".

### 8. `edge-foliage-001` — FAILURE 6, foliage at the frame edge

Pinnate foliage enters the top corner with no branch traceable to the trunk. The model
treats it as decisive and proposes Fraxinus with strong support.

Expects: the claim falls back to what bark supports (tier 3, confidence ≤ low), an
`unsupported_claim` finding, and a request for the attachment photograph.

### 9. `apple-with-fruit-001` — section 17, "плід закриває дискусію"

The counterweight. A machine that hedges everything is as useless as one that hedges
nothing, so when a fruit is attached to the branch the system must commit.

Expects: Malus, status `identified`, evidence tier 7, the user's version accepted, no
escalation. This is the only tier that unlocks the 95–100 band.

## Metrics

Every rate is reported alongside the count it was computed from. A "100% top-1 accuracy"
over the two cases that happened to declare an expected taxon is not a fact about the
system, and a metric that hides its denominator invites exactly that misreading.

| Metric | Meaning |
| --- | --- |
| `top_1_accuracy` | Leading taxon matched, over cases declaring `expected_taxon` |
| `top_3_recall` | Expected taxon in the top 3 candidates |
| `correct_resolution_rate` | Resolution matched exactly |
| `overconfidence_rate` | Claims narrower or stronger than the case permits |
| `abstention_quality` | Abstained **and** asked for a specific photograph |
| `schema_validity` | Cases that completed without a contract violation |
| `escalation_precision` | Of arbiter calls made, how many were wanted |
| `escalation_recall` | Of arbiter calls wanted, how many were made |
| `unnecessary_arbiter_call_rate` | Arbiter called where the case expected none |

**`overconfidence_rate` is the number this project exists to keep at zero.** Accuracy that
comes with overconfidence is not an improvement — it is the failure mode wearing a better
score.

`abstention_quality` counts only abstentions that came with a targeted photo request. A
system that abstains without saying what would help has not solved the user's problem, it
has merely avoided being wrong.

### Current results

All nine cases pass; `overconfidence_rate` 0.0, `schema_validity` 1.0, escalation precision
and recall 1.0, unnecessary arbiter calls 0.0, abstention quality 1.0.

Read that honestly: nine hand-built cases over recorded fixtures. It demonstrates that the
machinery does what it claims on the situations it was built to handle — including every
failure mode the domain prompt names. It says nothing about accuracy on real photographs,
which has not been measured.

## Adding a case

1. **Record a scenario** at `evals/fixtures/<name>.json`:

```json
{
  "scenario": "my-scenario",
  "responses": {
    "primary:planner": { },
    "primary:evidence_extractor": { },
    "primary:candidate_generator": { },
    "primary:botanical_reviewer": { },
    "primary:confusion_reviewer": { },
    "primary:confidence_reviewer": { },
    "arbiter:arbiter": { }
  }
}
```

Keys are `role:node`. An unscripted key fails loudly rather than being improvised — a fake
that quietly invents data is worse than no fake. Only script `arbiter:arbiter` if the case
escalates; only script the reviewer keys if the case gets past the quality gate.

`"malformed_once": ["primary:evidence_extractor"]` makes a key fail validation on its first
call and succeed on the retry, exercising the structured-output repair path.

2. **Declare the case** at `evals/public/<name>.yaml` with a `case_id`, `title`,
   `description`, `scenario`, `input` and `expect` block.

3. **Run it**: `evil-duck eval --suite public --verbose`.

A case that asserts nothing cannot fail, which is worse than no case at all — a contract
test enforces that every case declares at least one expectation.

## Private evaluation material

`evals/golden/` is git-ignored except for its README. Photographs of real trees are usually
not redistributable and often carry location metadata. See
[`docs/dataset-policy.md`](dataset-policy.md).

## Implementation references

- [`src/evil_duck_dendro/evaluation/`](../src/evil_duck_dendro/evaluation)
- [`evals/public/`](../evals/public), [`evals/fixtures/`](../evals/fixtures)
- [`tests/evaluation/test_public_suite.py`](../tests/evaluation/test_public_suite.py)
