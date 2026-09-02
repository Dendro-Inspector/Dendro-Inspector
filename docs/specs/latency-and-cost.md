# Latency and cost — specification

- **Status:** Draft
- **Owner:** Dendro Inspector maintainers
- **Date:** 2026-09-01
- **Last-verified:** 2026-09-01

Make a live inspection materially faster and cheaper without moving a single verdict the
evidence does not justify moving. Quality is the invariant, not the trade: every lever below
is either quality-neutral by construction or ships behind a measurement that the owner
pre-registers before reading its result.

Companion to [`core-logic-hardening.md`](core-logic-hardening.md). Its change C2 (the
provisional verdict computed before the escalation gate) is a prerequisite for the
escalation measurement in this document, and its C1 turns one noisy trigger into an
evidence statement. [`core-modernisation.md`](core-modernisation.md) N2 (retrieval before
proposal) removes most of the candidate generator's input by proof rather than by
measurement and composes with L2 below. None of the three documents is implemented.

## Where the time goes

All numbers come from local run artifacts on this machine, none of which is in Git:
58 full run traces under `evals/100 top/runs/` (the top-100 photograph corpus, v0.6.0 to
v0.8.0 policy, mixed Opus/Sol/Ox routes) and 553 answered model calls with per-call timing
and token usage across 28 bridge run directories under `.bridge/` (2026-08-23 to
2026-09-01). The aggregation scripts are in this session's scratchpad and become
`scripts/bench/` in Phase 0. Every figure is a median unless marked; every rate carries its
denominator.

### A run

| Quantity | Median | p90 | Source |
| --- | ---: | ---: | --- |
| Wall time per run | 274 s | 664 s | 58 traces |
| Serial model calls (planner, extractor, candidate generator, arbiter) | 157 s | | 58 traces |
| Reviewer fan-out (slowest of three) | 100 s | | 58 traces |
| Everything deterministic | ≈ 0 s | | 58 traces |
| Model calls per run | 7 | 12 with one retry | 58 traces |
| Prompt characters per run | ≈ 298 k | | offline replay of the 19 public cases |

The deterministic half of the graph is free. Every second is a model call, and the split is
roughly 60 % serial chain, 40 % waiting for the slowest reviewer.

### A node

Wall time per node from the 58 traces; prompt size, output tokens and cost per call from
the 553 bridge calls (cost is the upstream-reported figure for Opus calls only):

| Node | Wall med | Wall p90 | Wall max | Prompt chars | Output tokens | Opus cost |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `confidence_reviewer` | 82.7 s | 150.8 s | 807 s | 37 k | 2 067 (5 811 on Opus) | $0.58 |
| `confusion_reviewer` | 76.7 s | 157.3 s | 625 s | 37 k | | $0.58 |
| `evidence_extractor` | 63.8 s | 90.7 s | 174 s | 40 k | 5 214 | $0.52 |
| `candidate_generator` | 40.3 s | 61.6 s | 85 s | **116 k** | 2 268 | **$0.75** |
| `planner` | 22.3 s | 30.8 s | **3 546 s** | 30 k | 1 282 | $0.26 |
| `botanical_reviewer` | 20.8 s | 118.4 s | 880 s | 36 k | | $0.58 |
| `arbiter` | 17.3 s | 63.9 s | 88 s | 37 k | 1 573 | |

An all-Opus run without a retry costs about $3.9 at these medians. The three reviewers are
45 % of that.

### The latency model

Across 146 Opus calls with token accounting, wall time against output tokens fits

```text
duration ≈ 11.5 s + 0.0101 s × output_tokens        (r = 0.99)
```

Input size does not appear in the fit: the domain prompt is served from cache and the
remainder is small next to generation time. Two consequences drive everything below:

1. **Output length is the latency lever.** The extractor's 5 200 tokens are 53 s of its
   64 s. A reviewer answering with 5 800 tokens spends 59 s generating.
2. **There is an 11.5 s floor per call** that no prompt change touches. A 137-character
   route probe took 11.8 s median (5 calls). Seven calls per run put ≈ 80 s, 29 % of the
   median run, into fixed overhead. The bridge spawns `claude --print` as a fresh process
   for every request; that floor is the process and the time to first token, not the answer.

### What the outputs are made of

Opus evidence packets (31): 7 516 characters of observations per packet, about 34
observations, and `notes` is 53 % of every observation's bytes (117 of 220 characters).
Opus review results (52): 5.1 findings per result, `summary` 310 characters each, 3 206
characters of findings per result. The contract allows `ShortText` up to 400 characters
and puts no bound on the number of findings or observations.

### What the inputs are made of

The candidate generator's 116 k-character prompt is 27 k of cacheable prefix (domain prompt
plus register note) and ≈ 88 k of knowledge context. That knowledge context is the whole
catalogue rendered as pretty-printed JSON:

| Block | Pretty JSON | Compact | Without provenance, compact |
| --- | ---: | ---: | ---: |
| 25 taxon cards | 62 577 | 43 277 | 25 224 |
| 8 comparison cards | 13 304 | | 10 160 (pretty) |
| regional pack | 1 005 | | |

`provenance` is audit metadata for maintainers. No node prompt asks a model to read it. The
same catalogue is byte-identical on every case and sits *after* the case context, so it is
never served from cache.

Every downstream node also receives the evidence packet as pretty-printed JSON, and the
reviewers receive it plus the candidate sets plus the cards in play.

### Escalation in the field

| Fact | Value | Denominator |
| --- | ---: | --- |
| Runs that called the arbiter | 57 | 58 traces |
| `possible_multiple_taxa` (hard trigger) fired | 47 | 58 |
| Reviewer disagreement or critical finding fired | 46 | 58 |
| `bark_only_input` fired | 34 | 58 |
| Arbiter returned `pass` with zero findings | 58 | 87 arbiter answers |
| Arbiter asked for a rerank | 11 | 87 |
| Arbiter asked to lower confidence, resolution or abstain | 9 | 87 |

The arbiter is a default, not an exception: it runs on 98 % of real inputs and two thirds
of the time says nothing. `docs/model-roles.md` prices it at "roughly double model cost on
escalated cases"; on this corpus that is every case. Whether the third that did speak
changed a verdict is **unknown** from the traces, because nothing records the verdict that
stood before the arbiter. That gap is the first thing Phase 0 closes.

### Tails

The planner's 3 546 s maximum and the reviewers' 807 s and 880 s maxima are hangs, not slow
answers. No per-call deadline exists in the graph; `ANTHROPIC_TIMEOUT_SECONDS` covers one
adapter. One hung call is a run lost.

## Quality invariant

Every phase must hold all of these, in the same commit as the change:

- the public suite returns byte-identical per-case decisions to the frozen baseline, or the
  change is in `core-logic-hardening.md` and follows its rules;
- `overconfidence_rate` stays `0.0`;
- a live A/B on the repeatability corpus (`.bridge/2026-08-24-v060-repeatability-sol-5x3-v1`
  shape: five photographs, three runs each, one route) shows agreement with the previous
  configuration's verdicts at or above the previous run-to-run agreement, with both
  denominators stated. Verdict agreement is taxon, resolution, confidence and status per
  subject;
- no lever moves a decision from a model into code's place or from code into a model's
  (§4.6); a speed-up that changes *who* decides is a rejected change.

## Levers

Each lever names its expected effect, computed from the figures above, its quality risk,
how the risk is measured, and whether it needs an owner decision.

### L1 — Telemetry first (Phase 0, quality-neutral)

Nothing else can be accepted without it.

- `ProviderCallRecord` gains `input_tokens`, `cached_input_tokens`, `output_tokens`,
  `reported_cost_usd`, all optional; each adapter fills what its provider reports, the
  bridge dialects included. The trace then carries the latency model's inputs.
- `RunTrace` gains `provisional_decisions` (from hardening C2) and
  `arbiter_changed_status / taxon / resolution / confidence`, the same shape as the existing
  `correction_changed_*` flags. This is the arbiter's marginal value, per run, per trigger.
- `RunTrace` gains `critical_path_ms`: serial nodes plus the slowest reviewer per fan-out.
- `scripts/bench/trace_stats.py` and `scripts/bench/bridge_stats.py` reproduce the tables in
  this document from a directory of traces and a bridge root, so the numbers can be re-cut
  after every phase. `docs/evaluation.md` gets a short section pointing at them.

### L2 — Input diet (Phase 1, quality-neutral by construction)

| Change | Where | Effect |
| --- | --- | --- |
| Render knowledge cards for models without `provenance` | `nodes/_support.knowledge_context` via a `model_view()` on the card contracts | −24 k chars on the candidate generator, −3 k on each reviewer |
| Compact JSON (`indent=None`, `separators=(",", ":")`) for every context block | `_support.evidence_context`, `candidates_context`, `knowledge_context`, `case_context` | −25 to −30 % of every context block |
| Move the case-invariant catalogue into a second cacheable block, before the case context | `prompts/library.compose` gains an optional `static_context` placed after the node prompt; `cacheable_prefix_chars` reports the extended boundary; the contract test that binds the boundary to `compose` is extended | candidate generator: ≈ 115 k of 116 k cacheable across cases instead of 27 k |

Expected: candidate-generator prompt 116 k → ≈ 55 k, of which ≈ 50 k is cache-served after
the first case; reviewer prompts 37 k → ≈ 30 k. Cost on Opus: cache-creation tokens on the
candidate generator fall from ≈ 60 k to well under 20 k per case. Latency: small on caching
providers, proportional on the ones that do not cache (Ollama, NIM).

Quality: the model sees the same facts in a denser encoding. Provenance is not a fact about
the tree. Measured by the public suite (fake provider, byte-identical by construction) and
one live A/B on the repeatability corpus, because "models read compact JSON as well as
pretty JSON" is a belief until measured.

Not touched: the domain prompt, which is byte-for-byte by policy (`AGENTS.md` §12).

### L3 — Per-call deadlines (Phase 1, quality-neutral)

`ProviderConfig.call_deadline_seconds` per role, default 300 s, enforced in
`request_structured` with `asyncio.timeout`. A call that exceeds it raises `ProviderError`
after one retry, exactly as a transport failure does today; it is never reported as
uncertainty about a tree. Effect: caps the 3 546 s and 807 s tails at 600 s worst case per
node. No policy for a missing reviewer is introduced; the run still fails loud
(`core-logic-hardening.md`, Non-goals).

### L4 — Output diet (Phase 2, needs conformance review)

Latency lever number one, and the only lever that touches prompts.

| Change | Where | Expected effect |
| --- | --- | --- |
| `Observation.notes` becomes optional guidance: "only when the value token does not capture what you saw, one clause" | `prompts/nodes/evidence-extractor.md` | notes are 53 % of observation bytes; halving them is ≈ −1 500 output tokens, ≈ −15 s per extraction |
| One finding per defect; a finding's summary states the defect in one sentence | the three reviewer prompts and `prompts/nodes/arbiter.md` | 5.1 findings × 310 chars → ≈ 3 × 150; ≈ −1 000 to −3 000 tokens on Opus reviewers, ≈ −10 to −30 s off the fan-out |
| `ReviewFinding.summary` bounded at 240 characters, a new `FindingSummary` type | `schemas/reviews.py`, `schemas/base.py` | makes the prompt rule a contract; a longer summary is a validation failure the repair loop hands back |
| Ask for compact JSON on adapters that put the schema in the prompt | `providers/anthropic_adapter.py` and the bridge's Anthropic dialect | hypothesis: 5 214 output tokens for ≈ 10 k characters suggests whitespace on the wire; measured by tokens-per-character before and after |

Every prompt edit requires `dendro prompt-seal --write`, a `policy_revision` bump if any
frozen decision moves, and the §12 conformance review, because a shorter reviewer finding
must still carry the evidence ids and category that admissibility keys on. Quality is
measured on the repeatability corpus: the accepted-finding rate per reviewer, the rerank rate
and the verdict agreement must not fall.

### L5 — Escalation that pays for itself (Phase 3, owner decision, data first)

The arbiter costs one serial call and, on Opus, ≈ $0.6, on 98 % of real runs. The rule that
decides whether it is worth it has to be pre-registered, then measured, then applied:

1. **Pre-register** (owner, before Phase 0's corpus run): the minimum arbiter-changed-verdict
   rate at which a trigger keeps its status, one threshold for hard triggers and one for
   soft. Recommendation: a hard trigger must change a verdict in ≥ 10 % of the runs it
   alone caused; a soft trigger in ≥ 5 %.
2. **Measure** with L1's `arbiter_changed_*` flags on a re-run of the top-100 corpus, per
   trigger, reporting runs where the trigger was the *only* reason separately from runs
   where it co-fired.
3. **Apply**, in `EscalationPolicy`, with the measurement cited in the PR under §16's
   justification block.

Two candidate changes that the data already motivates, held until the measurement:

- `possible_multiple_taxa` fires on 47 of 58 runs. Per-subject scoping already keeps two
  logs from being averaged; the trigger predates that scoping. Proposed: hard only while
  any subject's provisional verdict is at species or `high`; soft otherwise.
- `reviewer_disagreement` fires on any difference in recommended level. Proposed:
  *material* disagreement only — recommendations differ **and** at least one lies below
  the composed provisional bound. Two reviewers recommending `genus` and `species` on a
  genus-capped card do not disagree about the answer.

Expected effect if the corpus supports both: arbiter rate 98 % → the fraction of runs that
actually carry species, `high`, a real contradiction or a material disagreement; on this
corpus that is at most 20 of 58 by the trigger counts alone, saving ≈ 17–35 s and one
call's cost on the rest.

### L6 — Bridge floor (Phase 4, agent-provider surface)

The 11.5 s per-call floor is ≈ 80 s of the median run. It belongs to
`scripts/agent-provider/worker.py`, which runs `claude --print` cold per request. Options,
to be measured with a trivial-prompt probe before and after: a warm session per worker
(resume instead of spawn), or binding roles that do not need the Claude Code account to a
direct API adapter, which the registry already supports. Out of scope for the graph;
recorded here because it is the largest single fixed cost.

### L7 — Work that cannot change the answer (experiments, owner decision)

- **Reviewers on empty candidate sets.** One public case (`candidate-sanitization-001`)
  runs all three reviewers with no admitted candidate. Skipping the fan-out there saves
  three calls, but a reviewer may request re-extraction that recovers a candidate. Measure
  first: how often the correction loop on an empty set changed the outcome
  (`correction_changed_outcome` in existing traces), then decide.
- **Planner ablation.** 22 s and 8 % of the run for an `InspectionPlan` the extractor
  receives as guidance. A deterministic plan from `declared_object_type` and the card
  vocabulary would remove one serial call. Quality effect unknown; run it as a blind A/B on
  the repeatability corpus and decide on verdict agreement. Not scheduled.

## Targets

Pre-registered so that the result can fail. Median over the top-100 corpus, same routes as
the baseline run:

| Metric | Today | After Phase 2 | After Phase 3 |
| --- | ---: | ---: | ---: |
| Wall time per run | 274 s | ≤ 200 s | ≤ 160 s |
| Critical path, serial model calls | 157 s | ≤ 120 s | ≤ 100 s |
| Prompt characters per run | ≈ 298 k | ≤ 170 k | ≤ 150 k |
| Opus cost per run | ≈ $3.9 | ≤ $2.8 | ≤ $2.2 |
| Arbiter call rate | 98 % | 98 % | measured, threshold-driven |
| Verdict agreement vs previous configuration | — | ≥ run-to-run agreement | ≥ run-to-run agreement |

A phase that hits its speed target and misses the agreement line is reverted, not tuned.

## Phasing

| Phase | Contents | Prompt bytes change? | Decision moves? |
| --- | --- | --- | --- |
| 0 | L1 telemetry, `scripts/bench/`, corpus re-run to establish `arbiter_changed_*` rates | no | no |
| 1 | L2 input diet, L3 deadlines | no | no — byte-identical suite |
| 2 | L4 output diet | yes — re-seal, conformance review | possible, reviewed per case |
| 3 | L5 escalation policy from the Phase 0 measurement | no | escalation decisions only |
| 4 | L6 bridge floor; L7 experiments | no | no |

Phase 0 and Phase 1 can be reviewed as ordinary pull requests. Phase 2 and Phase 3 each
carry a §16 justification block naming the corpus measurement as the independent source.

## Non-goals

- Fewer reviewer roles. The novelty assessment recommends removing roles with no measured
  marginal value; this document supplies the measurement (L1, L5) and stops there.
- Streaming, partial results, per-run cost ceilings — still on `CHANGELOG.md`'s deferred
  list, and none of them shortens the critical path.
- Any change to what the deterministic layer decides. That is the other specification.

## Open decisions

1. **Escalation thresholds (L5).** The two rates, set before the corpus is re-run.
2. **`FindingSummary` bound (L4).** 240 characters is a proposal; the shortest the owner
   accepts as a defensible defect statement.
3. **Compact JSON on the wire (L4).** Only if the tokens-per-character measurement shows
   whitespace; otherwise dropped.
4. **Planner ablation (L7).** Whether to run the experiment at all.

## Evidence appendix

Aggregations run 2026-09-01 on the working tree at `d6f8247`.

**Per-node wall time, 58 traces** (`evals/100 top/runs/*/*.trace.json`):

```text
node                           n      med      p90      max   share_of_median_run
confidence_reviewer           67     82.7    150.8    807.1   30.2%
confusion_reviewer            67     76.7    157.3    625.0   28.0%
evidence_extractor            67     63.8     90.7    173.6   23.3%
candidate_generator           67     40.3     61.6     84.7   14.7%
planner                       58     22.3     30.8   3546.3    8.2%
botanical_reviewer            67     20.8    118.4    879.5    7.6%
arbiter                       57     17.3     63.9     88.4    6.3%
every deterministic node       -      0.0      0.0      0.0    0.0%
median run total: 273.5s
critical path medians: total=274s serial_model=157s reviewer_fanout=100s unexplained=0s
```

**Per response model, 553 answered bridge calls** (`.bridge/*/pending/req-*-meta.json`
joined to `answers/*.meta.json`):

```text
response_model               n  dur_med  dur_p90 prompt_med     in cache_rd cache_cr out_med cost_med
ReviewResult               298     35.7    109.1      49632  44727    44904    41022    2067    0.576
GeneratedEvidencePacket     91     68.0    116.6      48735      6    10353    35042    5214    0.522
CandidateProposal           80     40.8     61.5     121114     10    95007    59955    2268    0.750
InspectionPlan              78     21.1     30.9      30527      6     3659    20812    1282    0.259
DendroRouteProbe             5     11.8     17.2        137      4     1943     2408     391    0.036
```

**Latency fit, 146 Opus calls:** `slope=0.0101 s/token intercept=11.5 s corr=0.99`. Pooled
over all 274 calls that reported output tokens, across every route, it is
`slope=0.0097 intercept=17.4 s corr=0.93` — which is what `scripts/bench/bridge_stats.py`
prints, since it does not split by route. The Opus-only fit is quoted above because the
per-call floor is a property of one transport, and pooling routes with different floors
inflates the intercept while leaving the slope, the part the output diet acts on, intact.

**Output composition, Opus:** 31 evidence packets, per-observation mean bytes
`notes=117 value=19 subject_id=17 feature=15 visibility=10 ...` (220 total); 52 review
results, 5.1 findings each, per-finding `summary=310` of 474 bytes.

**Knowledge context:** 25 cards pretty 62 577 / compact 43 277 / compact without
provenance 25 224; 8 comparison cards 13 304; regional pack 1 005.

**Offline replay of the 19 public cases:** 111 model calls, 9 arbiter calls, 1 case with
reviewers on an empty candidate set; cacheable prefix 27 459 chars; per-node prompt medians
`candidate_generator 116 012, evidence_extractor 40 171, arbiter 37 313,
confidence_reviewer 37 042, confusion_reviewer 36 618, botanical_reviewer 36 298,
planner 29 656`.

**Escalation, 58 traces:** `possible_multiple_taxa 47, bark_only_input 34,
reviewers_disagree_or_critical_finding 34, reviewer_disagreement 12,
leading_candidates_close 8, species_level_proposed 4, bark_colour_dependence 3,
high_confidence_proposed 1`. **Arbiter answers, 87:** `pass 58 (all with zero findings),
fail_correctable 14, pass_with_findings 12, fail_unresolvable 3`; rerank requested 11,
lowering action 9.
