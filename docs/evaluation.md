# Evaluation

- **Status:** Current
- **Owner:** Dendro Inspector maintainers
- **Date:** 2026-07-28
- **Last-verified:** 2026-07-28

```bash
dendro eval --suite public            # summary
dendro eval --suite public --verbose  # every assertion, including passes
dendro eval --suite public --json --out report.json
```

The suite is deterministic: each case replays a recorded provider scenario from
`evals/fixtures/`. No network, no credentials, no cost, no flake — which is why it runs in CI
on every pull request, including pull requests from forks, which never have repository
secrets.

## What assertions check

Assertions run against the **graph's decisions**, never against prose. Rewriting the tone
layer must not turn the evaluation red; changing what the system concludes must.

## The nineteen cases

Cases 1–5 cover the core mechanics. Cases 6–8 are named failure modes from section 13 of the
domain prompt. Case 9 is the counterweight — proof the system still commits when the evidence
is actually decisive. Cases 10–14 lock the v0.2.2 correctness boundary: candidate-specific
trusted evidence, fail-closed candidate admission, resolution-consistent identity,
deterministic-finding precedence and finding-bound reranks. Cases 15–16 hold the two
behaviours that six v0.2.2 fixture repairs would otherwise have quietly deleted. Cases 17–19
lock the v0.2.3 wood-surface boundary while preserving corroborated pile-level conclusions.

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

### 10. `unrelated-high-tier-001` — unrelated high-tier evidence cannot raise a candidate

A candidate supported only by bark must not inherit fruit/foliage authority from unrelated or
foreign-subject evidence elsewhere in the packet.

Expects: evidence tier and confidence derived only from the leader's validated support ids.

### 11. `candidate-sanitization-001` — unknown or unsupported candidates are removed

The model proposes unknown taxa and known taxa with empty, unrelated or contextual-only
support. The shared admission boundary removes them and rebuilds ranks densely.

Expects: only known supported candidates survive; if all are removed, resolution is `unknown`.

### 12. `broadened-species-identity-001` — broadened resolution renders broader identity

A species hypothesis is capped to genus or family by card/evidence/review bounds.

Expects: selected id and display name match that broader resolution; no species name survives.

### 13. `deterministic-preemption-001` — model findings cannot preempt deterministic findings

A model emits a same-category restatement before a deterministic colour or attachment check.

Expects: deterministic origin is adjudicated first and the material duplicate is retained as a
rejected restatement.

### 14. `unbound-rerank-001` — only finding-bound reranks can change ranking

A review contains a recommendation behind an absent or rejected `rerank_candidates` finding,
or multiple conflicting validated rankings at one level.

Expects: the current order is preserved unless one exact accepted finding/ranking artifact is
unambiguous.

### 15. `near-miss-vocabulary-001` — a plausible token no card declares admits nothing

Real models paraphrase. They emit `bark.flake_geometry` — a path the planner and the extractor
prompt both ask for, and the Pinus/Picea comparison card names — that no taxon card can match.
The candidate resting on it must not be admitted, and the run must not fall back to treating
"a feature was reported" as "a taxon was supported".

Exists because six fixtures were repaired in v0.2.2 by rewriting their model output into
verbatim card tokens. That left the near-miss path exercised only on input already conformed
to it. This case restores the near miss.

Expects: only `pinus` survives admission, tier 6, confidence ≤ medium, no escalation.

### 16. `partial-visibility-cap-001` — the happy path with one feature half-seen

`conifer-log-001` with a single edit: the fascicle count is `visibility: partial`. The
confidence reviewer still recommends medium; the evidence hierarchy caps it to low.

Also a v0.2.2 repair scar. The original fixture was flipped from `partial` to `clear` to keep
its `medium` expectation green, which deleted the only case where the cap fired on an
otherwise good photograph.

Expects: `pinus`, genus, confidence low, tier 3, a targeted photo request.

### 17. `rough-end-grain-anatomy-001` — rough cuts cannot prove prepared anatomy

A rough chainsaw face is described with exact pore and ray tokens. The tokens stay in the
packet for review, but surface provenance demotes both to context before quality or candidate
admission can treat them as anatomy.

Expects: no candidate, resolution `unknown`, tier 1, a prepared-end-grain request.

### 18. `split-face-colour-only-001` — split firewood needs more than colour

The model returns two exact colour/tone observations and incorrectly clears the mixed-taxa
flag. Deterministic planner/extractor reconciliation restores the flag; colour remains
bark-capped and insufficient without a non-colour feature above context.

Expects: no candidate, resolution `unknown`, tier 3, and matching end-grain/bark views of one
labelled piece.

### 19. `log-pile-pinus-001` — the pile counterexample

A `material_group` carries repeated exact Pinus-card evidence: scaly bark, light honey wood and
visible resin. This is the conformance counterexample to an overbroad aggregate ban: the pile
may receive a conservative genus conclusion, without proving every separated piece identical.

Expects: `pinus`, genus, confidence low, tier 3, no escalation.

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

The report also carries three raw counters. `escalations_expected` and `escalations_observed`
are the recall and precision denominators. `escalation_decisions_correct` is **not** a
numerator over either of them — it counts every case whose arbiter decision matched its
expectation, correct non-escalations included, so its denominator is `cases`. It was renamed
from `escalations_correct` for that reason: sitting between the other two, the old name read
as "16 correct out of 9".

### Recorded results

The frozen v0.2.1 baseline records all nine cases of that release passing, with
`overconfidence_rate` 0.0 and `schema_validity` 1.0. It is preserved as a historical record;
v0.2.2 intentionally changed admission and identity behaviour, so it is not the current result.

The v0.2.2 release result is preserved in `evals/baselines/public-v0.2.2.json`: sixteen
passing cases, zero failures and zero overconfidence.

The v0.3.0 release result is **nineteen passing cases, zero failures, zero overconfidence**,
frozen in `evals/baselines/public-v0.3.0.json`. The original sixteen decisions are byte-for-byte
equivalent in the baseline comparison; the three additions cover rough-end-grain anatomy,
split-face colour-only abstention and the corroborated log-pile counterexample.

The v0.4.0 release result is also **nineteen passing cases, zero failures and zero
overconfidence**, frozen in `evals/baselines/public-v0.4.0.json`. Every decision and metric
is identical to v0.3.0; this release changes provider reachability, image transport,
observability and evidence-vocabulary diagnostics without moving the conformance boundary.

The v0.5.0 result is unchanged again — **nineteen passing cases, zero failures, zero
overconfidence**, frozen in `evals/baselines/public-v0.5.0.json`, every decision and metric
identical to v0.4.0. That is worth stating plainly rather than quietly: v0.5.0 fixes four
defects in how reviewer findings are composed into a verdict, and the suite did not notice
any of them. All four were found on a live photograph, and all four are guarded by unit tests
rather than by a conformance case. A suite that stays green across a real fix is telling you
where its coverage ends.

Read the result honestly: nineteen hand-built cases over recorded fixtures can show that the
machinery follows these contracts. It says nothing about identification accuracy on real
photographs, which has not been measured.

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

3. **Run it**: `dendro eval --suite public --verbose`.

A case that asserts nothing cannot fail, which is worse than no case at all — a contract
test enforces that every case declares at least one expectation.

## Private evaluation material

`evals/golden/` is git-ignored except for its README. Photographs of real trees are usually
not redistributable and often carry location metadata. See
[`docs/dataset-policy.md`](dataset-policy.md).

**Golden cases are immutable evaluation assets.** Cards, prompts, thresholds and routing
rules must not be tuned against an individual case — a benchmark failure may reveal a
defect, but it may not by itself define the fix. Any change motivated by one carries a
justification block naming an independent domain source and new non-golden tests. The rule,
and the block, are in [`AGENTS.md` §16](../AGENTS.md#16-benchmark-governance).

This suite (`evals/public/`) is a **conformance and regression** suite, not an accuracy
benchmark. Adding a public case for a newly-understood failure class is expected and is not
overfitting: the fixtures are synthetic and the case documents a rule rather than an answer.

## Implementation references

- [`src/dendro_inspector/evaluation/`](../src/dendro_inspector/evaluation)
- [`evals/public/`](../evals/public), [`evals/fixtures/`](../evals/fixtures)
- [`tests/evaluation/test_public_suite.py`](../tests/evaluation/test_public_suite.py)
