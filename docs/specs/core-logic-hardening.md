# Core logic hardening — specification

- **Status:** Draft
- **Owner:** Dendro Inspector maintainers
- **Date:** 2026-09-02
- **Last-verified:** 2026-09-02

Scope is the deterministic decision chain of the one loop the project must do reliably:

```text
image + context -> evidence -> candidates -> review -> (arbitration) -> capped decision -> honest answer
```

Concretely: candidate admission, the escalation gate, the final decision engine, abstention
and the ruling on the user's own version. Providers, prompts, knowledge-card content and
response prose are out of scope, except where a change here obliges a doc or contract update.

Companion: [`latency-and-cost.md`](latency-and-cost.md) owns speed and cost. It depends on
C2 here (the provisional verdict) for its arbiter-value measurement and on C1 for the
`leading_candidates_close` trigger; it never changes what the deterministic layer decides.
[`core-modernisation.md`](core-modernisation.md) owns the shape of the data and the public
API underneath both.

Every finding below was reproduced against the working tree at `d6f8247` (dirty; the
uncommitted changes touch only providers, the agent bridge and their tests) by calling the
pure functions directly. The probe commands and their verbatim output are in the Evidence
appendix. Nothing in this document has been implemented yet.

## Why now

The architecture's rule is **a model proposes; code adjudicates** (`AGENTS.md` §4.6). The
v0.2.2 through v0.8.0 releases closed that boundary for evidence trust, candidate admission,
finding admissibility, reranks and reviewer scope. Three places still let a model-authored
label move the returned claim without deterministic adjudication, and two deterministic
rules have drifted into having more than one home. None of these is caught by the public
suite, which is the same pattern v0.5.0 recorded: the fixtures were written to exercise the
boundary as it stood, so a hole in the boundary is invisible to them.

## Findings

Each finding uses the §3 vocabulary. Domain source is the independent basis for the rule,
named so that no change here is a benchmark-driven tune (§16).

### F1 — A model-authored strength label seeds confidence and status

**Status:** `VERIFIED` (probe P5).

`final_decision.resolve_confidence` starts from `_SCORE_TO_CONFIDENCE[leader.score]`.
`Candidate.score` is the primary model's own `weak | moderate | strong` label, passed through
candidate validation untouched. The tier ceiling, the card's high-confidence requirements and
the reviewer bounds only ever lower it. At identical evidence (one attached, clear
`needles.fascicles = two` on a Pinus candidate) the label alone moves the result:

| Model `score` | Returned confidence | Returned status |
| --- | --- | --- |
| `strong` | `high` | `identified` |
| `moderate` | `medium` | `probable` |
| `weak` | `low` | `probable` |

`CandidateSet.leaders_are_close()` compares the same labels, so the
`leading_candidates_close` escalation trigger is also a comparison of two model opinions.

**Why it matters:** the confidence band and the `identified` status are the two most
believed fields in the answer, and within a tier they are currently decided by the field the
architecture says a model cannot justify (`docs/architecture.md`, "Ordinal scores, not
percentages").

**Domain source:** domain prompt §2 (evidence hierarchy) and §6 (confidence scale) tie
confidence to *which features are visible*, not to how sure the observer feels.

### F2 — The high-confidence trigger is blind to the claim it guards

**Status:** `VERIFIED` (probe P3a).

`escalation_gate._triggers` fires `high_confidence_proposed` only when
`synthesis.confidence_delta is HIGH`, and `confidence_delta` is the *lowest* confidence any
reviewer explicitly recommended. A `strong` leader with three reviewers that pass without a
recommendation produces `confidence_delta = None`, which the cost suppressor
`clean_review_and_modest_confidence` reads as "modest". The gate suppressed escalation while
the deterministic decision for the same state returned `high` / `identified`.

`docs/model-roles.md` describes this trigger as "confidence is the claim worth
double-checking". It currently double-checks a reviewer recommendation, never the claim.

**Domain source:** none needed; this is the gate failing its own stated contract.

### F3 — The species trigger fires on the proposal, not on the returned claim

**Status:** `VERIFIED` (probe P3b). Classified as a **cost question**, not a defect.

A species proposal on a genus-only card (`pinus`, `supported_resolution: [genus]`) fires the
hard trigger `species_level_proposed` although `cap_resolution` has already reduced the
returned claim to genus. The arbiter cannot lower resolution further; it can still change
the taxon or find a contradiction, so the call is not worthless. Whether it is worth its
cost is an operator decision and is listed under Open decisions rather than fixed here.

### F4 — A user's version can be rejected on evidence that could not support a claim

**Status:** `VERIFIED` (probe P2).

`rule_on_user_claim` computes `contradicted` from `match_card(...).has_contradiction`.
`match_card` matches contradictions against `contextual_observations_for`, which is every
visible observation regardless of trust — deliberately, so that weak evidence can still
raise a *finding*. But the ruling on the user's claim turns that finding into a verdict.
With one attached needle observation on the subject (so the subject is not bark-only) and a
second `needles.fascicles = two` whose attachment is `unknown`, the claim "ялина" is
`rejected`. The rejecting observation is one the extractor could not trace to this trunk;
under the evidence hierarchy it projects to context and could not have supported any
candidate.

`decide_status` and `_contradiction_summary` use the same unfiltered `has_contradiction`.
The botanical reviewer's deterministic layer is the only caller that filters (it exempts
bark-tier contradictions, FAILURE 3), so the rule "when does a contradiction count" now has
two homes. No shipped card declares a contradiction below tier 4, so the bark exemption is
unreachable today; the attachment gap is reachable on every foliage case.

**Domain source:** domain prompt §3 ("я не маю права агресивно відкидати твою версію") and
§2 / FAILURE 6 (unattached foliage must not move a verdict). `docs/architecture.md`,
"Attachment provenance": only `confirmed_attached` evidence may support identification.

### F5 — Claim matching is substring-based and iteration-order dependent

**Status:** `VERIFIED` (probe P1).

`_claim_matches` accepts `normalised in candidate or candidate in normalised` and
`rule_on_user_claim` takes the first card in `available_taxon_ids()` order:

| User claim | Resolved to | Cards that matched |
| --- | --- | --- |
| `дуб або ясен` | `fraxinus` | `fraxinus`, `quercus` |
| `ясен або дуб` | `fraxinus` | `fraxinus`, `quercus` |
| `не дуб` | `quercus` | `quercus` |
| `a` | `acer` | 22 cards |

A hedged claim is resolved alphabetically, a negated claim is resolved to the taxon it
denies, and a one-letter claim matches most of the pack. The verdict then depends on which
of those the user "said".

**Domain source:** domain prompt §3 rules on the version the user actually gave.

### F6 — Abstention is invisible in the verdict

**Status:** `VERIFIED` (probe P4).

`abstain.run` sets `state.abstained` and lowers `resolution_delta` one step below the
leader's *proposed* resolution. When the card cap has already broadened the proposal, that
step lands on the level the non-abstaining path returns anyway. For a species proposal on a
genus card, the abstained and non-abstained verdicts differ only in confidence
(`medium` → `low`); taxon, resolution and status are identical, and `FinalDecision` has no
field that says the run abstained. `state.abstained` is read by exactly two places: the
escalation gate and `resolve_confidence`. The response composer never sees it.

The abstain node's own docstring says the run must present "a deliberately weakened answer
rather than a confident one"; the structured result cannot tell the reader which it is.

### F7 — The derivation is not in the trace

**Status:** `VERIFIED` by reading `schemas/decisions.py` and `observability/trace.py`.

`final_decision.py` opens with "the derivation is inspectable in the trace". `FinalDecision`
records the composed result and the attachment counterfactual; nothing records which of the
five resolution bounds bound, which confidence steps applied and which were skipped as
already honoured, or whether the rerank came from the arbiter or an internal reviewer. A
disputed answer can be re-derived only by re-running the engine.

### F8 — Model-raised confidence downgrades stack without bound when no reviewer names a floor

**Status:** `VERIFIED` by `tests/unit/test_final_decision.py::test_downgrades_without_a_recommendation_still_compose`.
Classified as a **design decision to revisit**, not a defect: the current behaviour is
tested and deliberate.

v0.5.0 made a reviewer's `recommended_confidence` a floor for that reviewer's own findings
because "three reviewers writing up one overclaim cost three steps". The floor exists only
when a recommendation exists. Three model findings with no recommendation still cost three
steps, so the failure v0.5.0 fixed survives in the shape it was not tested in.

### F9 — The correction loop is case-global

**Status:** `VERIFIED` by reading `nodes/correction_worker.py`. Known limitation
(`AGENTS.md` §12).

One accepted `re_extract_evidence` finding on one subject clears every subject's candidate
sets, reviews and quality report and re-runs extraction for the whole case. Multi-subject
cases (log piles, mixed-taxa frames) pay the full retry for a defect in one subject. Deferred
to a later phase; recorded so it is not rediscovered.

## Changes

Ordered so that each phase is independently reviewable and revertable (§6). Every change
lands with its failing test first. A change that moves any frozen public-suite decision
bumps `policy_revision`, re-freezes the baseline, and lists each moved case in
`CHANGELOG.md` with the direction of the move. **A case whose resolution narrows or whose
confidence rises is a defect in the change, whatever else it does.**

### C1 — Adjudicate support strength deterministically (closes F1)

**Rule.** A candidate's effective `score` is the minimum of the model's label and a strength
derived from its *validated* support against its own card:

| Derived strength | Condition on validated support ids |
| --- | --- |
| `strong` | at least one full-trust hit on a `strong_positive_features` expectation **and** no missing `required_for_high_confidence` entry |
| `moderate` | at least one hit (any positive trust) on a `strong_positive_features` expectation, or at least two hits on `supporting_features` |
| `weak` | otherwise |

**Where.** `knowledge/candidate_validation.py`, in `validate_candidate_set_with_report`,
after support ids are validated. `CardMatch` already exposes `strong_hits`,
`supporting_hits`, `full_strong_hits` and `missing_for_high_confidence`; the derivation is a
few lines over those tuples restricted to the candidate's surviving ids. The validation
report gains `demoted_scores: tuple[tuple[str, SupportStrength, SupportStrength], ...]`
(taxon, proposed, effective) and the candidate generator logs it beside `rejected_taxa`.

**Consequences.** `resolve_confidence` needs no change: it reads `leader.score`, which is now
adjudicated. `leaders_are_close()` compares adjudicated strengths, so
`leading_candidates_close` becomes a statement about evidence. Reviewer and arbiter
recommendations pass through the same validator, so a rerank cannot smuggle a `strong` label
either. The model's label is never raised.

**Tests.** Unit: the three-row matrix above; a `strong` label on one supporting-only hit
returns `weak` and on two returns `moderate`; a `strong` label with a missing requirement
returns `moderate`; a `weak` label is never raised. Public case
`strong-label-thin-support-001`: primary labels a
candidate `strong` on one supporting-feature hit, reviewers pass silently, expected
confidence ≤ `medium`, no `identified`.

**Eval impact.** Expected to move any fixture whose scripted `score` outruns its scripted
support. Each move is reviewed individually; none may raise confidence.

### C2 — Compute the provisional verdict before the escalation gate (closes F2; enables F3)

**Implementation status:** implemented in policy revision `0.9.0`.

**Rule.** The gate decides on what the graph would return, not only on what was proposed.

**Where.** A new deterministic step at the head of `escalation_gate.run` computes
`decide_subject` for every candidate set and stores the result in a new
`GraphState.provisional_decisions` field. `graph/projections._proposed_assessments` reads
that field instead of recomputing, so the arbiter sees exactly what the gate saw. The
attachment authority gate already runs `decide_subject_base` internally for its
counterfactual; it is unchanged.

Trigger and suppressor inputs change as follows:

| Signal | Before C2 | Current C2 input |
| --- | --- | --- |
| `high_confidence_proposed` | `synthesis.confidence_delta is HIGH` | that **or** any provisional decision with `confidence is HIGH` |
| `clean_review_and_modest_confidence` | `confidence_delta` not `HIGH` | no provisional decision at `HIGH` |
| `broad_and_low_risk` | proposed leader resolution ≤ genus | provisional resolution ≤ genus for every subject and no provisional decision at `HIGH` |
| `species_level_proposed` | proposed leader resolution | unchanged (see Open decision 1) |
| `leading_candidates_close` | model labels | adjudicated labels, via C1 |

**Tests.** Unit: a `strong` leader with silent reviewers escalates with reason
`high_confidence_proposed`; the same state with a `medium` provisional decision does not;
the arbiter projection equals the stored provisional decisions. Public case
`silent-reviewers-high-confidence-001` expects escalation.

**Eval impact.** Escalation precision and recall are both frozen metrics with a direction;
an intended change in either is reported with the re-frozen baseline.

### C3 — One home for "does this contradiction count" (closes F4, absorbs the bark exemption)

**Rule.** A contradiction may change a verdict only if the observation carrying it could
itself have supported one: same subject, `supports_identification` under the shared trust
projection, and tier above bark. Every other contradiction hit remains available to
reviewers as a finding and to the report as a note, and can lower confidence through the
normal finding path, but cannot on its own reject a user's version, set
`conflicting_evidence`, or be printed as the strongest contradiction.

**Where.** `knowledge/taxon_cards.CardMatch` gains `disqualifying_hits`, computed in
`match_card` from the existing `contradiction_hits` by applying `project_observation` and
`tier_of_feature`. Callers move to it:

- `final_decision.rule_on_user_claim` — `contradicted` reads `disqualifying_hits`;
- `final_decision.decide_status` — `conflicting_evidence` reads `disqualifying_hits`;
- `final_decision._contradiction_summary` — prints the first disqualifying hit;
- `botanical_reviewer.card_contradiction_findings` — the major finding is raised on
  `disqualifying_hits`; the minor FAILURE 3 note is raised on the remainder, replacing the
  local bark-tier computation.

`contradiction_hits` stays as it is for callers that want everything, and
`docs/review-pipeline.md` documents both fields.

**Tests.** Unit: an `unknown`-attachment contradiction cannot reject a claim; the same
observation `confirmed_attached` can; a bark-tier contradiction (constructed card) is minor
in the reviewer and does not set `conflicting_evidence`. Note that
`card_contradiction_findings` is referenced by no test file today — the botanical
reviewer's deterministic layer is covered only indirectly through the public suite — so C3
also gives it direct unit tests for the major, minor and no-contradiction branches. Public
case `unattached-contradiction-claim-001`: user
claims a taxon, the only contradicting observation is unattached foliage, expected
`user_claim_verdict` in `{possible, doubtful}` and never `rejected`.

**Eval impact.** `rough-bark-oak-claim-001` and `edge-foliage-001` are the cases most likely
to move; both already expect the restrained outcome, so a move would be toward what they
assert.

### C4 — Recognise the claim the user made (closes F5)

**Rule.** Matching is by whole token against the card's aliases, display name and id, after
the existing normalisation, with the longest alias winning. An alias shorter than four
characters matches only as a whole token, never as a substring. When the claim resolves to
more than one taxon, it is a disjunction: the ruling is the most favourable verdict over the
members, because a user who hedged is not to be punished for it. A taxon token preceded by
a negation word (`не`, `not`, `no`, `ні`) is removed from the claim; if nothing remains the
claim is treated as unrecognised (`possible`) and the trace records `negated_claim`. Full
negation semantics ("it is *not* an oak" as a testable statement) are Open decision 4.

**Where.** `final_decision.normalise_claim` and `_claim_matches`, plus a small
`resolve_user_claim(claim, knowledge) -> tuple[str, ...]` that returns every matched taxon
in longest-alias-first order. `rule_on_user_claim` iterates the tuple.

**Tests.** The four rows of the F5 table become parametrised cases: `дуб або ясен` with
`quercus` selected is `accepted`; with `fraxinus` selected is `accepted`; `не дуб` does not
resolve to `quercus`; `a` resolves to nothing. Existing Ukrainian and Latin matching tests
keep passing. Public case `disjunctive-user-claim-001`.

### C5 — Make abstention visible and make it weaken the claim (closes F6)

**Rule.** An abstained run states so, and its verdict is broader than the verdict the same
evidence would otherwise have earned.

**Where.** `FinalDecision.abstained: bool = False` (additive; no consumer breaks).
`abstain.run` computes the resolution step from the *composed* provisional bound — it reuses
C2's `decide_subject` per subject — rather than from the proposed leader resolution, so the
step always lands one level below what would have been returned. `decide_subject` copies
`state.abstained` onto the decision. `response_composer` renders the flag in the structured
result's `limitations` and the composer's tone gating treats it as restraint.

This is the recommended option. The stronger alternative — abstention returns
`insufficient_evidence` with no selected taxon — is Open decision 2; it discards the
"lower the claim, do not erase the work" intent the node was built with.

**Tests.** Unit: species proposal on a genus card abstains to `family`, not `genus`; the
flag is set; the response mentions it. Public case `abstention-visible-001`.

### C6 — Record the derivation (closes F7)

**Rule.** For each subject the trace records how the verdict was composed, so a disputed
answer can be audited without re-running the engine.

**Where.** A `DecisionDerivation` contract in `schemas/decisions.py`, recorded through
`TraceRecorder` and attached to `RunTrace` beside `authority_checks`; **not** a field of
`FinalDecision`, which stays the consumer-facing verdict. Contents:

| Field | Meaning |
| --- | --- |
| `resolution_bounds` | proposed, card cap, tier ceiling, reviewer recommendation, abstention — each with its value |
| `resolution_binding_bound` | which of those produced the composed value |
| `resolution_action_applied` | whether a `lower_resolution` action was applied or skipped as already honoured |
| `confidence_steps` | ordered list: seed (with C1's proposed and effective strength), tier cap, requirement cap, recommendation, each model step applied or skipped, each deterministic step, abstention |
| `rerank_source` | `arbiter`, `internal` or `none`, with the admitted finding id |

**Tests.** Unit: the derivation for each existing `TestResolutionCap` and
`TestRecommendationIsAFloor` scenario in `tests/unit/test_final_decision.py` names the bound
or step the test already asserts. Contract: every `FinalDecision` in a trace has exactly one
derivation with the same subject id.

**Eval impact.** None; additive telemetry.

### C7 — Bound model-raised confidence steps without a floor (F8; Open decision 3)

Recommended rule: at most one model-raised `lower_confidence` step per reviewer per subject
when that reviewer named no floor. A reviewer that files three findings is describing one
opinion, and the deterministic steps remain unbounded. Not scheduled until the owner decides;
recorded here so the decision is made once.

## Phasing

| Phase | Contents | Verdict-moving? | Policy revision |
| --- | --- | --- | --- |
| 0 | One failing test per finding F1–F6, marked `xfail(strict=True)` with the finding id, no behaviour change | no | — |
| 1 | C3, C6, C5 | C3 possibly, toward what the cases assert | bump if any case moves |
| 2 | C1, C2 | yes, expected | bump; re-freeze baseline |
| 3 | C4; C7 if decided | C4 no | — |

Phase 0 lands first and alone. It turns this document's evidence into gates that fail, which
is the only form in which a finding here can be trusted after the next refactor (§"The Main
Rule").

## Gates and acceptance

All five §4.5 gates on every phase. In addition, before a phase merges:

- the new public cases named above pass, and each existing case either keeps its frozen
  decision or is listed in `CHANGELOG.md` with old value, new value and the finding that
  moved it;
- `overconfidence_rate` stays `0.0`;
- no case's resolution narrows and no case's confidence rises — checked by the baseline
  test's directional comparison, and re-checked by hand on the per-case diff;
- `prompts/versions.yaml` `policy_revision` and the value pinned in `prompts/library.py`
  move together when any frozen decision moves; `dendro prompt-seal` is **not** needed,
  because no prompt byte changes;
- documents updated in the same commit as the code they describe: `docs/architecture.md`
  (determinism table gains the provisional decision; "Ordinal scores" gains the
  adjudication rule), `docs/model-roles.md` (trigger table), `docs/review-pipeline.md`
  (`disqualifying_hits`), `docs/agent-graph.md` (new state field), `docs/evaluation.md`
  (case list and count), `AGENTS.md` §12 key-files table if `candidate_validation.py`'s
  "when to modify" line needs the strength rule named.

## Non-goals

- **Value-vocabulary synonyms.** Exact `(feature, value)` matching is the top risk in
  `docs/reviews/NOVELTY-AND-VALUE-ASSESSMENT-2026-08-23.md`, but any relaxation is a
  knowledge-layer change with a dendrology judgement inside it. Owner's call, separate spec.
- **Reviewer partial-failure degradation.** A `ProviderError` in one reviewer fails the run,
  and the deterministic findings that reviewer would have raised are lost with it. Changing
  that means choosing a policy for an incomplete review; it is a provider-boundary decision
  and belongs beside the agent-provider work, not here.
- **Per-subject correction scope** (F9). Real, deferred; the retry budget and the
  clearing rule are both case-global by design today and the termination argument depends
  on the budget.
- Model-backed response composition, conversation state, and anything in
  `CHANGELOG.md`'s "Intentionally deferred" list.

## Open decisions

1. **Species trigger source (F3).** Keep firing on the proposal (current, safest, costs an
   arbiter call the cap has already neutralised in part), or fire on the provisional verdict
   with the proposal-based variant as a separate switch. Recommendation: keep, and measure
   `unnecessary_arbiter_call_rate` after C2 before deciding.
2. **Abstention outcome (C5).** Flag plus one-step broadening from the composed bound
   (recommended), or `insufficient_evidence` with no taxon.
3. **Model downgrade stacking (C7).** Keep unbounded, or one step per reviewer per subject.
   Recommendation: one step per reviewer.
4. **Negated claims (C4).** Treat "not X" as unrecognised (recommended for now), or as a
   testable negative statement with its own verdict path.

## Evidence appendix

Working tree `d6f8247`, 2026-09-01. Probe script constructs `GraphState` values and calls
`decide_subject_base`, `escalation_gate.decide`, `rule_on_user_claim`,
`abstain.degraded_synthesis` and `_claim_matches` directly with the shipped knowledge pack
and default `AppConfig`. Subject `log_1`, image `img-1`.

**P1 — claim matching** (`_claim_matches` over `available_taxon_ids()` order):

```text
'дуб або ясен'           -> first='fraxinus'  all=['fraxinus', 'quercus']
'ясен або дуб'           -> first='fraxinus'  all=['fraxinus', 'quercus']
'не дуб'                 -> first='quercus'   all=['quercus']
'ash'                    -> first='fraxinus'  all=['fraxinus']
'a'                      -> first='acer'      all=[22 cards]
'дубок чи не дубок'      -> first='quercus'   all=['quercus']
```

**P2 — rejection on unattached contradiction** (`needles.persistence = evergreen`
attached; `needles.fascicles = two` attachment `unknown`; candidate `pinus`; claim `ялина`):

```text
claim 'ялина', contradiction o2 has attachment=unknown -> rejected
```

**P5 — model label seeds confidence** (`needles.fascicles = two` attached, clear;
candidate `pinus` at genus; empty synthesis):

```text
score=strong   -> confidence=high   status=identified tier=6
score=moderate -> confidence=medium status=probable   tier=6
score=weak     -> confidence=low    status=probable   tier=6
```

**P3a — silent reviewers, strong leader** (same evidence, `score=strong`, empty synthesis):

```text
gate: required=False reasons=() suppressed_by=('broad_and_low_risk', 'clean_review_and_modest_confidence')
provisional decision confidence=high status=identified
```

**P3b — species proposed on a genus-only card** (`pinus` supported_resolution `[genus]`):

```text
gate: required=True reasons=('species_level_proposed',)
provisional decision resolution=genus confidence=medium
```

**P4 — abstention on the P3b state** (`degraded_synthesis(synthesis, SPECIES)`,
`abstained=True`):

```text
not abstained: taxon=pinus res=genus conf=medium status=probable
abstained:     taxon=pinus res=genus conf=low    status=probable
FinalDecision has an 'abstained' field: False
```

Contradiction features declared by the shipped 25 cards, with their evidence tier — the
basis for calling the bark-tier exemption unreachable:

```text
leaf.arrangement     tier=5   leaf.type          tier=6   leaf.underside     tier=6
needles.attachment   tier=6   needles.fascicles  tier=6   needles.persistence tier=6
pores.arrangement    tier=4
```

## Appendix A — Contracts

Exact shapes, so that implementation is transcription. Every new field is additive with a
default; no existing field changes type or meaning. All contracts inherit `Contract`
(frozen, `extra="forbid"`).

### A1 — Support strength (C1)

```python
# knowledge/taxon_cards.py
def support_match(
    card: TaxonCard, evidence: EvidencePacket, subject_id: str, support_ids: tuple[str, ...]
) -> CardMatch:
    """`match_card` restricted to the source observations of `support_ids`.

    `strong_hits`, `supporting_hits` and `full_strong_hits` are computed over those
    observations only. `contradiction_hits` and `missing_for_high_confidence` are
    subject-level properties and are computed exactly as `match_card` computes them.
    """


# knowledge/candidate_validation.py
def derive_support_strength(match: CardMatch) -> SupportStrength:
    if match.full_strong_hits and not match.missing_for_high_confidence:
        return SupportStrength.STRONG
    if match.strong_hits or len(match.supporting_hits) >= 2:
        return SupportStrength.MODERATE
    return SupportStrength.WEAK


def adjudicated_strength(proposed: SupportStrength, derived: SupportStrength) -> SupportStrength:
    return min(proposed, derived, key=strength_rank)  # never raises the model's label
```

`validate_candidate_set_with_report` calls both after `_validated_support_ids` and writes
the result into the survivor's `score`. `CandidateValidationResult` gains:

```python
demoted_scores: tuple[ScoreDemotion, ...]  # ScoreDemotion(taxon, proposed, effective)
```

`candidate_generator` logs `demoted_scores` in the existing
`candidate_validation_filtered` warning. `Candidate` itself is unchanged: the model's label
is not kept on the contract, because a field the model can also fill is not provenance.
The proposed label survives in `GraphState.proposed_candidate_sets`, which already exists
for the attachment counterfactual, and in C6's derivation.

### A2 — Provisional decisions (C2)

```python
# graph/state.py
provisional_decisions: tuple[FinalDecision, ...] = Field(
    default=(),
    description=(
        "Deterministic per-subject verdicts computed at the escalation gate, before any "
        "arbiter call. The gate decides on these; the arbiter projection shows these."
    ),
)
```

`escalation_gate.run` computes them with `decide_subject` and stores them before calling
`decide`. `decide` gains a `provisional: tuple[FinalDecision, ...]` parameter and reads
resolution and confidence from it as in the C2 table. `graph/projections._proposed_assessments`
reads `state.provisional_decisions` and raises `ReviewProjectionError` if it is empty when
the arbiter is projected. `correction_worker` keeps its own `decide_subject` call: it runs
before the gate on the retry path and must not depend on a field the gate has not written.

### A3 — Disqualifying contradictions (C3)

```python
# knowledge/taxon_cards.py, on CardMatch
disqualifying_hits: tuple[str, ...]
```

Computed in `match_card` as the subset of `contradiction_hits` whose observation satisfies
`project_observation(o).supports_identification` **and** `tier_of_feature(o.feature) >
EvidenceTier.BARK`. `has_contradiction` keeps its current meaning (any hit); a new
`is_disqualified` property reads the new tuple. Call sites move as listed in C3.

### A4 — Claim resolution (C4)

```python
# nodes/final_decision.py
NEGATION_WORDS: frozenset[str] = frozenset({"не", "ні", "not", "no"})


def resolve_user_claim(claim: str, knowledge: KnowledgeBase) -> tuple[str, ...]:
    """Every card the claim names, longest matching alias first; empty when unrecognised.

    Tokenise on whitespace and punctuation after the existing normalisation per token.
    A token preceded by a negation word is dropped. An alias of fewer than four characters
    matches only a whole token; longer aliases match a whole token or a run of tokens.
    """
```

`rule_on_user_claim` becomes: no claim → `NOT_PROVIDED`; empty resolution → `POSSIBLE`;
otherwise evaluate the existing clause order for each resolved taxon and return the most
favourable verdict in the order `ACCEPTED > POSSIBLE > DOUBTFUL > REJECTED`.

### A5 — Abstention (C5)

```python
# schemas/decisions.py, on FinalDecision
abstained: bool = Field(
    default=False,
    description="The run abstained: this verdict is deliberately broader than the evidence earned.",
)
```

`abstain.run` computes `decide_subject` per subject and passes each decision's *composed*
`resolution` to `degraded_synthesis` instead of the leader's proposed resolution.
`decide_subject` copies `state.abstained`. `response_composer._limitations` prepends a
locale-rendered `abstained` line when the flag is set.

### A6 — Derivation (C6)

```python
# schemas/decisions.py
class ResolutionBound(Contract):
    source: Literal["proposed", "card_cap", "tier_ceiling", "reviewer_recommendation", "abstention"]
    value: Resolution


class ConfidenceStep(Contract):
    source: Literal[
        "seed",
        "tier_cap",
        "requirement_cap",
        "reviewer_recommendation",
        "model_finding",
        "deterministic_finding",
        "abstention",
    ]
    finding_id: Identifier | None = None
    before: Confidence
    after: Confidence
    applied: bool  # False when skipped as already honoured


class DecisionDerivation(Contract):
    subject_id: Identifier
    proposed_strength: SupportStrength
    effective_strength: SupportStrength
    resolution_bounds: tuple[ResolutionBound, ...]
    resolution_binding_source: str  # one of the ResolutionBound.source literals
    resolution_action_applied: bool
    confidence_steps: tuple[ConfidenceStep, ...]
    rerank_source: Literal["arbiter", "internal", "none"]
    rerank_finding_id: Identifier | None = None
```

`resolve_resolution` and `resolve_confidence` return `(value, record)` pairs; the node
assembles the derivation and calls a new `TraceRecorder.record_derivation(derivation)`.
`RunTrace` gains `decision_derivations: tuple[DecisionDerivation, ...] = ()`. A contract
test asserts one derivation per final decision, matched on `subject_id`.

### A7 — Evaluation expectation (C5, C6)

```python
# schemas/evaluation.py, on EvalExpectation
expected_abstained: bool | None = None
```

`evaluation/assertions.py` checks it against `FinalDecision.abstained` when stated.

## Appendix B — Phase 0 tests

One test per finding, `@pytest.mark.xfail(strict=True, reason="<finding id>: <one line>")`,
placed in the file that owns the function under test. Strict, so that the moment the
behaviour changes the marker must be removed in the same commit — the test then becomes the
regression guard. Each test is written against the pure function, with no provider.

| Finding | File | Test name | Given / when / then |
| --- | --- | --- | --- |
| F1 | `test_candidate_validation.py` | `test_strong_label_on_supporting_only_hit_is_demoted` | candidate `score=strong`, its only validated support matches a `supporting_features` entry → admitted `score` is `weak`, since the C1 table needs two supporting hits for `moderate` |
| F1 | `test_final_decision.py` | `test_the_same_evidence_yields_the_same_confidence_whatever_the_model_said` | P5 setup, `score` in `{strong, moderate, weak}` → identical confidence and status |
| F2 | `test_escalation_gate.py` | `test_strong_leader_with_silent_reviewers_escalates` | P3a state → `required` and `high_confidence_proposed` in `reasons` |
| F4 | `test_user_claim.py` | `test_an_unattached_contradiction_cannot_reject_a_version` | P2 setup → verdict is not `rejected` |
| F4 | `test_final_decision.py` | `test_conflicting_evidence_status_needs_a_disqualifying_hit` | selected taxon, only contradiction hit has attachment `unknown` → status is not `conflicting_evidence` |
| F5 | `test_user_claim.py` | `test_a_hedged_claim_is_accepted_when_either_member_is_selected` | claim `дуб або ясен`, selected `quercus` → `accepted` |
| F5 | `test_user_claim.py` | `test_a_negated_claim_does_not_name_the_taxon_it_denies` | claim `не дуб`, selected `quercus` → not `accepted` |
| F5 | `test_user_claim.py` | `test_a_one_letter_claim_matches_nothing` | claim `a` → `possible` because unrecognised, and `resolve_user_claim` is empty |
| F6 | `test_final_decision.py` | `test_an_abstained_verdict_says_so_and_is_broader` | P4 state → `abstained is True` and resolution is `family` |
| F7 | `test_trace.py` | `test_every_final_decision_has_a_derivation` | run `primary-pass` scenario → `len(trace.decision_derivations) == len(decisions)` |

F3, F8 and F9 get no Phase 0 test: they are decisions, not defects, and a strict xfail on a
behaviour the owner may keep would be a gate that fails for no reason.

## Appendix C — New public cases

Five cases, each a synthetic fixture under `evals/fixtures/` and a declaration under
`evals/public/`, following `docs/evaluation.md`'s "Adding a case". Fixture wording is new;
none copies a golden case (§16). Scripted keys: `primary:planner`,
`primary:evidence_extractor`, `primary:candidate_generator`, the three `reviewer:*` keys,
and `arbiter:arbiter` only where the case escalates.

### `strong-label-thin-support-001` (C1)

Extractor returns one attached, clear observation matching a `supporting_features` entry of
the candidate card and nothing matching `strong_positive_features`. Candidate generator
labels the candidate `strong`. All three reviewers return `pass` with no recommendation.

```yaml
expect:
  expected_taxon: <card>
  expected_resolution: genus
  max_confidence: medium
  expected_status: probable
  require_escalation: false
  forbid_admitted_reranks: true
  max_retries: 0
```

Before C1 this case returns `high` / `identified` on one supporting feature. The card is
chosen at implementation time from the shipped pack so that a single supporting-feature
observation exists in its vocabulary; the case must not add a card value.

### `silent-reviewers-high-confidence-001` (C2)

The P3a state as a fixture: full-trust `strong_positive_features` support satisfying the
card's high-confidence requirement, candidate labelled `strong`, three silent passes.
Arbiter scripted, returns `pass`.

```yaml
expect:
  expected_taxon: pinus
  expected_resolution: genus
  require_escalation: true
  max_retries: 0
```

Before C2 the gate suppresses; `escalation_recall` moves accordingly and is reported.

### `unattached-contradiction-claim-001` (C3)

Extractor returns one attached observation supporting Pinus and one `needles.fascicles`
observation contradicting Picea with attachment `unknown`. User claim `ялина`, no field
context. Reviewers pass.

```yaml
expect:
  expected_taxon: pinus
  forbid_user_claim_rejection: true
  expected_user_claim_verdict: doubtful
  require_next_photo: true
```

`doubtful` rather than `possible`: Picea is not in the candidates and no *disqualifying*
contradiction exists, so the existing clause order lands there. The photo request is
expected to be the attachment view through the existing `attachment_request` priority; that
expectation is a hypothesis until the fixture runs, and `require_next_photo` is dropped
from the case rather than tuned if it does not hold.

### `disjunctive-user-claim-001` (C4)

`rough-bark-oak-claim-001`'s evidence with the claim `дуб або ясен`. Everything else
unchanged from that case.

```yaml
expect:
  expected_taxon: fraxinus
  expected_user_claim_verdict: accepted
  forbid_user_claim_rejection: true
  max_confidence: low
  expected_evidence_tier: 3
```

Before C4 the verdict depends on which alias happens to match first.

### `abstention-visible-001` (C5)

A reviewer returns a `critical` finding with `required_action: abstain` citing visible
evidence, on a species proposal for a genus-only card. Routing takes `abstain`.

```yaml
expect:
  expected_taxon: <family identity of the card>
  expected_resolution: family
  max_confidence: low
  expected_abstained: true
  require_escalation: false
  require_next_photo: true
```

Before C5 the verdict is genus with `abstained` unrecorded.

### Suite bookkeeping

`docs/evaluation.md` gains the five descriptions and its count moves from nineteen to
twenty-four; `tests/evaluation/test_baseline.py` asserts the new count and the new baseline
file. `AGENTS.md` §4.5 deliberately records no count and needs no edit.

## Appendix D — `CHANGELOG.md` entries, by phase

Drafted so that the user-visible statement is agreed before the diff exists. Internal
details (helper names, test files) stay out, per §15.

**Phase 1**

```markdown
### Fixed
- A contradiction can no longer reject the user's own version, mark a verdict as
  conflicting or be printed as the strongest contradiction unless the contradicting
  observation could itself have supported an identification: same subject, attached where
  attachment matters, and above bark level. Weaker contradictions are still recorded as
  findings and can still lower confidence.
- An abstained run now says so in the structured result, and its resolution is one level
  broader than the same evidence would otherwise have earned. Previously abstention could
  return the identical taxon, resolution and status with only the confidence lowered.

### Added
- Run traces record how each verdict was composed: every resolution bound considered and
  the one that bound, every confidence step applied or skipped, and where a rerank came
  from.
```

**Phase 2**

```markdown
### Changed
- A candidate's support strength is now adjudicated against its own card from the evidence
  that survived admission, and the model's label can only lower it. Confidence and the
  `identified` status therefore follow visible features rather than the primary model's
  self-assessment. The deterministic policy revision moves to `0.9.0`; deployments pinning
  a manifest must re-seal.
- The escalation gate decides on the verdict the graph would return, not only on what the
  primary model proposed. A high-confidence verdict now escalates even when every reviewer
  passed without stating a confidence.

### Notes on this release
- <per-case list of moved decisions, old → new, with the finding that moved each>
```

**Phase 3**

```markdown
### Fixed
- The user's version is matched on whole words, longest alias first. A hedged claim naming
  two taxa is ruled on both and takes the more favourable verdict; a negated taxon no
  longer counts as the user having named it; a one-letter claim no longer matches most of
  the knowledge pack.
```
