# Evaluation

- **Status:** Current
- **Owner:** Dendro Inspector maintainers
- **Date:** 2026-11-20
- **Last-verified:** 2026-09-05

```bash
dendro eval --suite public            # summary
dendro eval --suite public --verbose  # every assertion, including passes
dendro eval --suite public --json --out report.json
```

The suite is deterministic: each case replays a recorded provider scenario from
`evals/fixtures/`. No network, no credentials, no cost, no flake — which is why it runs in CI
on every pull request, including pull requests from forks, which never have repository
secrets.

Fake replay uses the image IDs declared by each case as its synthetic image scope, even
when the example files are absent. Unknown image IDs still fail extraction validation.
Live providers are bound to images actually passed to the extraction call. Photo coverage
counts that scope as the photographs that reached extraction, so under fake replay a declared
photograph is never `unavailable`, only accounted for or `missing`. Regression
coverage for image provenance, review ownership, omitted subjects, retrieval and cancellation
is in [the architecture boundary tests](../tests/unit/test_architecture_boundaries.py).

## Compare identification policy offline

`coverage-audit` can replay saved case records under both discriminating-support settings:

```bash
dendro coverage-audit runs/saved/cases --replay --require-discriminating-support --json-out runs/strict.json
dendro coverage-audit runs/saved/cases --replay --no-require-discriminating-support --json-out runs/legacy.json
```

Both commands use the same recorded observations and candidate proposals with neutral fake
reviewers. They make no provider calls and change no deployment configuration. With neither
flag, replay inherits the configured policy; either flag explicitly overrides it for that
command. The default identification policy remains unchanged.

This comparison measures the deterministic layer, not the accuracy of new model readings
or the effect of live reviewers. Inspect the reported replay-fidelity mismatches before
interpreting a difference. New traces preserve the wood surface used to assign evidence
trust. Older traces that omit it replay with an unknown surface; they cannot establish
prepared end grain, and any changed trust or tier is flagged. Labels must use the records'
case IDs; repeated IDs do not become independent photographs. A family-level answer does
not establish a labelled genus.

## Grade stored and replayed answers against references

```bash
dendro coverage-audit runs/saved/cases --labels references.json --replay --json-out runs/scored.json
```

Pool answers only from runs that share an identity. `dendro release-manifest --compare
ANSWER` says whether an answer was made with the same code, prompts, knowledge and settings
as the current installation, and names each setting that differs; traces before schema 5
record no knowledge or settings identity
([architecture](architecture.md#what-a-run-depended-on)).

Reference labels use the archive's case IDs. A flat mapping such as
`{"sample": "fagus"}` remains accepted. To declare the intended subject and whether a
written plant name is visible, use:

```json
{
  "labels": {
    "sample": {
      "genus": "fagus",
      "family": "fagaceae",
      "target_subject_ids": ["tree"],
      "label_visible": false
    }
  }
}
```

`family` is optional; when omitted, the report uses the genus card's family if available.
Use `target_subject_ids: "all"` only when every subject belongs to the labelled plant or
lot. Otherwise, list the target IDs from that record. Other subjects' genus names are
reported as context. A declared target missing from the decisions is `missing_target`,
including when only a background subject was returned or only some targets are present.

Without declared targets, a single subject is graded. Several subjects are graded only
when they all yield the same outcome; otherwise the result is `unscoped`, with its possible
outcomes. The report never selects whichever named subject happens to come first.

Detailed `outcomes` distinguish:

- `correct` and `wrong`: a single genus claim matches or differs from the reference.
- `ambiguous_with_label` and `ambiguous_without_label`: multiple offered names, kept out of
  single-genus accuracy even if one is correct. Latin binomials resolve to genus; family
  names do not match a genus by prefix.
- `broader_only` and `wrong_broader`: a broader claim, with a conflicting family identified
  only when the reference family is known. Broader claims are excluded from genus accuracy.
- `no_name`, `unscoped`, and `missing_target`: no genus answer or insufficient target scope.

With several declared targets, any wrong genus takes precedence over correct names; an
ambiguous answer takes precedence over a correct one. Missing targets are reported before
combining grades. `records` retains the deprecated coarse counters (`correct`, `wrong`,
`abstained`) for existing consumers; all detailed outcomes other than `correct` and `wrong`
fall into its legacy `abstained` bucket. New consumers should use `outcomes` and the
per-record `stored_grade` and `replayed_grade` fields.

The `grading` summary reports record count, distinct case count, genus coverage, conditional
accuracy and wrong-genus rate together. A Wilson 95% interval is reported for the wrong-genus
rate only when each case ID occurs once. Repeated runs retain their record-level rates but
receive no interval (`interval_unavailable_reason: "repeated_case_ids"`); split model arms
into separate reports to obtain one result per case. Distinct IDs alone do not establish an
independent held-out sample: photograph provenance and sampling remain the dataset owner's
responsibility. `labelled_movements` pairs stored and replayed outcomes by record, including
unchanged grades; unavailable replays are excluded, not counted as abstentions.

`label_visible` may be true, false or null. Omitted visibility is unknown. The `all` table
includes every labelled record, while `label_not_visible` includes only explicit false
values; `label_visibility_unknown` counts unannotated records. These are reference
annotations, not a claim that the scorer inspected or masked the photographs.

This command scores the recorded graph decisions. It does not extract hypotheses from
baseline prose, verify a label's botanical provenance, or establish accuracy on an unseen
dataset. The scorer's image-reference helper requires an archive adapter to supply both
cited and supplied IDs; coverage reports do not infer image validity from trace observations.
The implementation and synthetic regression cases are
[`label_scoring.py`](../src/dendro_inspector/evaluation/label_scoring.py) and
[`test_label_scoring.py`](../tests/unit/test_label_scoring.py).

## Replay with the recorded reviews

```bash
dendro coverage-audit runs/saved/cases --replay --reviews recorded --json-out runs/recorded.json
```

`--reviews recorded` gives each reviewer the review its trace recorded, in place of a
neutral pass. It measures what today's adjudication and decision rules make of the same
evidence, proposals and reviews, which is the comparison a change to how review binds a
verdict needs. It still calls no provider.

To compare the experimental review policy with the released one, replay the same records
under each and score both against the same reference labels:

```bash
dendro coverage-audit runs/saved/cases --replay --reviews recorded --labels references.json --json-out runs/released.json
dendro coverage-audit runs/saved/cases --replay --reviews recorded --labels references.json --review-authority evidence_bound --json-out runs/evidence-bound.json
```

Read the pair as names recovered against wrong names added, never as one number. With
neutral reviewers the two policies give the same result, because a review that finds
nothing lowers nothing.

For traces with schema-3 evidence history, recorded-review replay starts at the first
extraction. Each graph attempt consumes its own raw packet, original candidate proposal
and recorded reviewer responses. Artifact hashes, revision order and review-to-evidence
bindings are validated before use. Image references include earlier extractions, even
when the final packet no longer mentions those images. The audit continues to read the
final effective packet directly, preserving component provenance, notes and attachment
trust. Neutral-review replay continues to use only the final extraction.

Full-history replay refuses missing raw packets, unbound reviews and omitted findings.
A changed policy or retry budget may route differently: an extra call, a response assigned
to another retry or an unconsumed recorded response produces `replay_unavailable`, without
a substituted reviewer answer or partial verdict. Each such record carries
`replay_unavailable_reason`, the text report prints it, and the summary counts the reasons
in `replay_unavailable_reasons`. Use neutral mode to compare final
evidence admission independently of the original review route. Older traces without
evidence history retain the limited reconstruction behavior described below.

### Replay under the settings a run recorded

By default a replay runs under this environment's escalation and graph settings. Records
from a deployment configured differently then route differently: a run that called the
arbiter on every decision leaves an arbiter response that a replay under the default
escalation policy never reads, and the record is reported unavailable. The reason names
the settings that differ:

```text
replay unavailable because: replay_route: consumed 0 of 1 responses for arbiter:arbiter;
the run recorded other settings than this replay uses (escalation.review_every_decision,
graph.open_world_discovery): replay with the recorded settings to follow its route
```

`--settings recorded` replays each record under the escalation and graph settings its own
trace recorded (`runtime_identity`, trace schema 5), with this command's policy options
applied on top:

```bash
dendro coverage-audit runs/saved/cases --replay --reviews recorded --settings recorded --labels references.json --json-out runs/as-recorded.json
dendro coverage-audit runs/saved/cases --replay --reviews recorded --settings recorded --labels references.json --review-authority evidence_bound --json-out runs/evidence-bound.json
```

The first command asks whether today's code reproduces each stored answer; the second
changes one policy and nothing else. Two recorded settings are never taken over: the
shadow vision assessment, which is an extra model call that decides nothing, and the
wall-clock deadline. A trace that records no settings is reported unavailable; it is not
replayed under this environment's settings instead. Each record's `replay_settings` says
which of the two it ran under.

Original case context and image files are still absent and explicitly reported in
`replay_gaps`. Unsuccessful provider repair responses are not recorded; only the successful
response for each graph call replays. This is not an exact replay of the entire request.
The uploader's part attestations are the one case input a trace does carry: replay rebuilds
them from the recorded outcomes and applies them to the raw packet again. A run credited
under the earlier field-context rule, which needed no statement, replays without that credit
and lists `field_attestation` among its gaps. Visible-label handling remains separate policy
work.

What it does not do:

- A trace written before reviews were recorded (`trace_schema_version` 1, or absent) cannot
  be replayed this way. The report marks the record `replay_unavailable` and gives no
  replayed verdict; it does not fall back to neutral reviewers.
- Without evidence history, only the last internal round is replayed, so the original
  retry route cannot be reconstructed.
- Without evidence history, a reviewer the run never called, usually the arbiter, answers
  with a neutral pass if the replay reaches it. With history, that call is refused.
- The case's own inputs (declared object type, location, the user's claim) are not in the
  trace, so a record that used one replays without it.

Each record's `replay_gaps` lists which of these applied. The public suite's cases each
replay to the verdict their run reached
([`tests/unit/test_coverage_audit.py`](../tests/unit/test_coverage_audit.py)).
Retry sequence and route-divergence regressions live in
[`tests/unit/test_retry_replay.py`](../tests/unit/test_retry_replay.py).

### Replay under a review plan

[Adaptive routing](model-roles.md#adaptive-routing-experimental) is evaluated offline first.
Replay the same stored records twice, once as recorded and once following the plan, and
score both against the same reference labels:

```bash
dendro coverage-audit runs/saved/cases --replay --reviews recorded --labels references.json --json-out runs/full-review.json
dendro coverage-audit runs/saved/cases --replay --reviews recorded --labels references.json --adaptive-routing enabled --json-out runs/planned-review.json
```

The second replay gives each reviewer its plan calls the review the run recorded and leaves
the other recorded reviews unread. Each record reports its route and the reviewer models
not called; the summary adds `replay_routes` and `replay_reviewer_calls_skipped`.
`--adaptive-fast-path` adds the fast path, and `--adaptive-routing shadow` reports the
routes while replaying the full review.

Read the pair as with any policy comparison: names recovered against wrong names added, and
the calls saved beside both. What this replay cannot show:

- The case's own inputs are not in a trace, so the rule that keeps the full review for a
  case with a user claim never fails in a replay. A record that carried a claim is planned
  as though it had none.
- A plan is derived from the recorded proposals and evidence. It says nothing about a
  reviewer answering differently when told why it was called, because the recorded review
  was written without that question.
- A record whose run retried may not replay under a plan. If the plan leaves unread the
  review that asked for the retry, or the retry needs a review the plan skipped earlier,
  the record is reported `replay_unavailable`.
- A trace recorded with a plan applied has no response for a reviewer that plan skipped
  (`model_called: false`). Replaying it without the plan is refused, never filled with a
  neutral pass.

## What assertions check

Assertions run against the **graph's decisions**, never against prose. Rewriting the tone
layer must not turn the evaluation red; changing what the system concludes must.

## The twenty-four cases

Cases 1–5 cover the core mechanics. Cases 6–8 are named failure modes from section 13 of the
domain prompt. Case 9 is the counterweight — proof the system still commits when the evidence
is actually decisive. Cases 10–14 lock the v0.2.2 correctness boundary: candidate-specific
trusted evidence, fail-closed candidate admission, resolution-consistent identity,
deterministic-finding precedence and finding-bound reranks. Cases 15–16 hold the two
behaviours that six v0.2.2 fixture repairs would otherwise have quietly deleted. Cases 17–19
lock the v0.2.3 wood-surface boundary while preserving corroborated pile-level conclusions.
Case 20 proves that the escalation gate sees the verdict code would return even when every
reviewer is silent. Cases 21–22 lock the two halves of the v0.9.0 restraint boundary: what a
contradiction has the authority to do, and what abstention has to cost. Case 23 holds the
line the whole determinism boundary rests on: a model's own confidence in itself is not
evidence. Case 24 rules on the version the user actually gave, rather than on whichever
taxon their words happened to match first.

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

Expects: Malus, status `identified`, evidence tier 7, the user's version accepted, and
escalation because the deterministic provisional verdict is high. This is the only tier that
unlocks the 95–100 band.

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

### 20. `silent-reviewers-high-confidence-001` — the gate sees the verdict

Clear attached Pinus foliage and exact card-matched support produce a deterministic
high-confidence genus verdict. All three internal reviewers pass with no findings, so their
silence cannot be the gate's only view of confidence.

Expects: `pinus`, genus, confidence high, tier 6, escalation with
`high_confidence_proposed`, and no retries.

### 21. `unattached-contradiction-claim-001` — a contradiction that may not convict

Needle litter lying on a sawn log is fascicled in twos, which the Picea card calls
disqualifying, but no shoot is continuous with the log and the canopy overhead is mixed.
The user says spruce. Evidence with no authority to identify has none to reject: the
contradiction is still recorded and may still lower confidence, but the ruling on the user's
own version must stop short of `rejected`.

Expects: `pinus`, resolution ≤ genus, user claim `doubtful`, never rejected, a next photo,
and no retries.

### 22. `abstention-visible-001` — abstention has to cost something

A species is proposed on a card that supports genus only, and the confusion reviewer files a
critical finding no retry can fix. Broadening from the *proposed* species would land on the
genus the card cap had already produced — the confident answer returned under another name.
Abstention must broaden from the composed verdict instead, and must say that it did.

Expects: `pinaceae`, family, confidence low, `abstained`, no escalation, a next photo, and no
retries.

### 23. `strong-label-thin-support-001` — a label is not evidence

An attached leaf and dark platy bark: two of the Alnus card's supporting features, and no
match for the persistent cones that are its only strong-positive one. The model calls the
candidate `strong` and all three reviewers pass in silence. Nothing else in the pipeline
lowers this verdict — the card's high-confidence requirement is satisfied, so no
deterministic finding fires, and the evidence tier permits `high`. The adjective alone is
therefore load-bearing, which is why it must not be.

Expects: `alnus`, genus, confidence ≤ medium, status `probable`, tier 6, no escalation, no
admitted reranks, and no retries.

### 24. `disjunctive-user-claim-001` — a hedge is several claims

`conifer-log-001`'s photograph with a user who declines to commit between pine and spruce.
Pine is the answer, so their version is right. Before C4 the claim resolved to whichever card
the catalogue listed first — spruce — and the attached two-needle fascicle disqualifies
spruce, so the system returned `pinus` while telling a user who had named the right tree
that their version was **rejected**. A user who hedged has not made a weaker claim; they have
made two, and the ruling belongs to the most favourable of them.

Expects: `pinus`, genus, confidence ≤ medium, tier 6, user claim `accepted`, never rejected,
and no retries.

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

The v0.6.0 result remains **nineteen passing cases, zero failures and zero overconfidence**,
frozen in `evals/baselines/public-v0.6.0.json`. This revision changes deterministic
high-confidence requirement parsing and evidence-aware next-photo planning without changing
any frozen verdict. Every run trace now also records the source commit and dirty state when
executed from a Git checkout, so unreleased benchmark runs remain attributable.

The v0.7.0 result is frozen in `evals/baselines/public-v0.7.0.json`. It adds a deterministic
counterfactual around decision-critical detachable evidence: a single uncorroborated model
label can no longer silently switch a claim between a taxon and abstention. The same revision
records correction-loop outcome deltas and asks for attachment provenance before finer
morphology when evidence ownership is the unresolved hinge.

The v0.8.0 result is frozen in `evals/baselines/public-v0.8.0.json`. Reviewer model calls now
receive an explicit projection instead of the whole graph state, and each returned result is
bound by code to the evidence ids that projection carried. Every metric and every per-case
decision is identical to v0.7.0, which is the point: a boundary that changes what a reviewer
may cite should not change what the system concludes on cases where reviewers cited honestly.

The v0.9.0 result is **twenty-four passing cases, zero failures and zero overconfidence**,
frozen in `evals/baselines/public-v0.9.0.json`. The gate now computes and stores the
deterministic verdict before deciding on escalation. `apple-with-fruit-001.arbiter_used`
intentionally moves from `false` to `true`, and the new silent-reviewer case proves the same
path directly. All taxon, resolution, confidence and status fields from the previous baseline
remain unchanged. Cases 21 to 24 join the same baseline: all four are new, so none moves a
frozen decision, and each fails on the code that preceded it.

Case 23 is worth a note. Adjudicated support strength was expected to move existing frozen
verdicts, and it moved none: every scripted `score` in the suite already matched the support
scripted beside it. That is a statement about the fixtures, not about the change — no
recorded case had ever exercised a label that outran its evidence, which is precisely why
one had to be written.

The v0.11.0 baseline is frozen in `evals/baselines/public-v0.11.0.json`: twenty-four passing
cases, zero failures and zero overconfidence. One decision moves on purpose.
`log-pile-pinus-001` answers `pinus` at genus again, where the v0.10.0 baseline recorded
`unknown`: the Pinus card now declares scaly bark, resin and honey-coloured wood a diagnostic
combination, which permits the genus and leaves confidence at the bark-tier ceiling (`low`).
Every other frozen decision and every metric is unchanged.

The 1.0.0-alpha.1 result is frozen in `evals/baselines/public-v1.0.0a1.json`: twenty-four
passing cases, zero failures and zero overconfidence, with every decision and every metric
identical to v0.11.0. What the pre-release adds on the default path (leaving out a subject
that was named and never seen, and the Claude API image limit) moves no frozen verdict, and
its spend limit is unset by default. The file was cut from the report CI wrote for the code
it freezes.

Read the result honestly: twenty-four hand-built cases over recorded fixtures can show that
the machinery follows these contracts. It says nothing about identification accuracy on real
photographs. That has been measured only in-sample, on the maintainers' own photographs; the
figures and their limits are in the README's [Status](../README.md#status).

## Adding a case

1. **Record a scenario** at `evals/fixtures/<name>.json`:

```json
{
  "scenario": "my-scenario",
  "responses": {
    "primary:planner": { },
    "primary:evidence_extractor": { },
    "primary:candidate_generator": { },
    "reviewer:botanical_reviewer": { },
    "reviewer:confusion_reviewer": { },
    "reviewer:confidence_reviewer": { },
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

## The held-out set

The set that measures field accuracy is prepared outside the repository, beside its
photographs, as a YAML manifest that `dendro heldout-check MANIFEST` validates. Each unit is
one inspection (up to 12 photographs) with a `group_id` naming the physical tree or wood
lot, `categories`, the owner's preliminary `label` and the specialist `reviews` it has had.

```yaml
schema_version: 1
name: heldout-v1
development_photo_dirs: [../selected, ../untagged]   # photographs used in development
units:
  - unit_id: h001
    group_id: tree-017
    photos: [tree-017/01.jpg, tree-017/02.jpg]
    categories: [bark_only, confusable]   # also mixed_subjects, wood, unknown_taxon
    label: {genus: fagus, family: fagaceae, label_visible: false}
    reviews:
      - {reviewer: F.R., qualification: forester, verdict: agree, date: 2026-11-20}
```

What the check fails on: a repeated unit ID, a missing or non-image photograph, the same
photograph in two units, a photograph whose hash is in a development directory, one group
labelled with two genera (unless the unit is `mixed_subjects`), and `unknown_taxon` on a
genus a card covers. A genus no card covers counts as `unknown_taxon` whether declared or
not. It warns about a size outside the 100-200 planning target, a category with no unit,
disputed units and photographs with GPS coordinates in EXIF (with Pillow installed).

Review status is derived, never declared: `preliminary` with no review, `reviewed` when
every specialist agrees with the label, `disputed` after a disagreement, and `resolved` when
the two latest reviews agree. Only `reviewed` and `resolved` units are release-ready.
`--labels-out labels.json` writes their labels, keyed by unit ID, in the format
`coverage-audit --labels` reads; `--include-preliminary` writes every unit's. Submit each unit
under its `unit_id` so its records match.

Use an explicit `genus: null` when the genus cannot be established. Include `family` if
the specialist can establish it. A missing genus field is a manifest error. A reviewer
who disagrees with a genus identification can also explicitly record `genus: null`.
Reserve `unknown_taxon` for an established genus that has no reference card.
`heldout-check` warns when one tree or lot has units with `genus: null` and units with a
named genus: the protocol keeps `null` for a tree that no evidence names.

In the coverage audit, `grading.indeterminate_genus` counts how often an answer offers
a genus on these targets, including a list of alternatives. Giving no name or giving
only the reference family counts as an acceptable abstention. A missing target and a
broader name the reference cannot verify have their own outcomes. Conditional genus
accuracy uses names with a known reference genus.

Report wrong-genus rates per group as well as per unit when groups hold several units, and
always with the denominator ([`AGENTS.md` §16](../AGENTS.md#16-benchmark-governance)). The
wrong-genus target of under 5% among named genera is a planning target, not a guarantee.

How the run on this set is carried out for v1.0.0, what is counted, and which result leads
to which decision is fixed in advance in
[`docs/specs/v1-heldout-protocol.md`](specs/v1-heldout-protocol.md). It compares the graph
with the model alone on the same units and has to be signed before any result is read.

## Arm V: the model checked against its card

Arm V asks the model alone for a genus (arm M's request,
[`model_alone.py`](../src/dendro_inspector/evaluation/model_alone.py)) and, concurrently, runs
the graph's first stages exactly as an inspection does: input guard, planner, evidence
extractor, evidence quality. Nothing after them runs: no candidate generation, review,
arbitration, correction retry or graph fallback. Code then decides
([`validated.py`](../src/dendro_inspector/evaluation/validated.py)):

- The answer binds to the evidence only when the model names exactly one genus and the
  evidence shows exactly one organism: one root subject and the components attached to it.
  A root is any independent tree, log, detached part or material sample. The model's
  subjects are free text with no evidence subject ID, so with several genera
  (`unbound_genera`) or several organisms (`unbound_subjects`) nothing says which tree the
  answer was about, and nothing is named. The card checks are still recorded for several
  organisms, so a later subject binding can be measured against them.
- Every card of that genus is matched against each subject of the organism with
  `match_card`, the admission boundary the graph uses. A pairing is `confirmed` when a
  feature the card calls strong was observed and nothing contradicts or disqualifies it. A
  card contradicted on any part of the organism confirms nothing.
- The organism is named with the genus when a card is confirmed. The decision cites the
  observations that confirmed it: feature, value, image.
- Otherwise the organism abstains with a reason (`not_confirmed`, `contradicted`,
  `no_card_for_genus`, `unbound_genera`, `unbound_subjects`, `evidence_insufficient`,
  `model_named_no_genus`, `no_evidence`). Where the answer is bound and unconfirmed, the
  model's genus is kept as an unconfirmed `visual_hypothesis`, with the follow-up
  photographs its cards list. A hypothesis is never an answer and is never graded.

Scenes with several organisms are therefore never named by arm V. On the development units
that cost correct names (a tree with a loose leaf beside it, a standing tree and its felled
log); recovering them needs an explicit binding between the model's answer and one
organism, not a looser rule.

`card_confirmed` means the recorded observations match a feature the card calls strong for
that genus. It is not a botanical identification. The check catches a model that reads the
features correctly and names the wrong genus. It cannot catch a misreading the same model
makes both when naming and when describing: an oak's lobed leaves read as `palmate_lobed`
confirm *Acer* (`test_a_shared_misreading_is_confirmed_which_is_the_checks_known_limit`
states this). Checking one model's names with another model's evidence is the experimental
variant below.

```bash
# arm V on the configured primary model (records in runs/<name>/cases/)
python scripts/eval/validated.py --units units.json --out runs/<name>
# V x: an earlier arm M run (possibly another model) names; the primary model's evidence checks
python scripts/eval/validated.py --units units.json --answers runs/<arm-m> --out runs/<name>
# re-decide every stored record offline from its recorded answer and evidence
python scripts/eval/validated.py --replay runs/<name>    # 0 identical, 1 any differs, 2 none found
```

`--answers` uses an earlier answer only if it was given to the same question: the same
photographs in the same order (answers cite them by position), the same field-context
statement, request, answer schema and domain prompt. Otherwise that unit fails alone.

Every planned unit ends with a record: a decision, a failed run, or a unit refused before it
started (`failure.stage: setup`), so a score can count every unit, not only the answered
ones. A rerun skips decided units and tries failed ones again.

Each record keeps the stored-case layout and adds `result.validated`: the rules version, the
answer and its source, every card check, the evidence-quality verdict, wall time for the
namer, the extraction and the whole unit, and tokens for each. The decision is taken from
the evidence packet as the trace records it, which is why `--replay` reproduces it exactly.
`DENDRO_IMAGE_MAX_EDGE_PX` is recorded; a downscale applied outside the engine (a bridge
worker's own edge limit) is not visible to it and has to be recorded with the run.

Arm V is an evaluation arm. It changes no inspection default, and the held-out protocol
names its arms separately.

## Measuring cost and latency

The suite measures what the system concludes. It says nothing about what a run costs or how
long it takes, because it replays recorded fixtures with no network at all. Those questions
are answered from run traces instead.

```bash
dendro inspect --trace-out traces/ --image photo.jpg   # write one trace
python scripts/bench/trace_stats.py traces/            # summarise a directory of them
python scripts/bench/bridge_stats.py .bridge           # the same, from agent-bridge state
```

`trace_stats.py` reports per-node wall time with its percentiles, model calls and provider
attempts per run, escalation reasons, and per-node token accounting where the provider
reported any. It also groups the recorded arbiter verdict-change rate and per-field changes
by every trigger that fired, so a multi-trigger run contributes to each policy signal being
measured. `bridge_stats.py` joins the local bridge's pending requests to its answers, so it
can additionally report prompt size as sent and the upstream's own cost, and it fits elapsed
time against output length.

Two numbers deserve reading together. `duration_ms` is what the run took;
`critical_path_ms` is what it could not have avoided, being every serial node plus the
slowest member of each reviewer fan-out. The gap between them is what concurrency bought.

Neither script makes a model call or needs a credential, and neither estimates a price: a
cost appears only where the provider reported one. A provider that reports no tokens leaves
those fields empty rather than zero, and the tables say so rather than printing a confident
nought.

## Implementation references

- [`src/dendro_inspector/evaluation/`](../src/dendro_inspector/evaluation)
- [`evals/public/`](../evals/public), [`evals/fixtures/`](../evals/fixtures)
- [`tests/evaluation/test_public_suite.py`](../tests/evaluation/test_public_suite.py)
- [`scripts/bench/`](../scripts/bench)
