# Review pipeline

- **Status:** Current
- **Owner:** Dendro Inspector maintainers
- **Date:** 2026-09-05
- **Last-verified:** 2026-09-05

## The rule

**A finding is not accepted because a model produced it.**

Three reviewers plus an arbiter emit findings. Every one of them faces the same
deterministic admissibility test in
[`nodes/review_synthesizer.py:adjudicate`](../src/dendro_inspector/nodes/review_synthesizer.py).
Findings that pass are applied. Findings that fail are **kept**, marked rejected, with a
reason code. `ReviewFinding.origin` records `model` or `deterministic`; provider-returned
findings are forced to model origin at the reviewer boundary, while code-generated findings
are explicitly deterministic.

Deterministic findings are adjudicated first. This precedence is non-preemptible: a model
cannot suppress a deterministic colour, attachment or contract finding by emitting a similar
category earlier in its response.

Rejections are retained deliberately. "The reviewer said X, we did not act on it, because Y"
is the trail that makes a disputed answer defensible three months later. A pipeline that
silently drops findings it disagreed with cannot be audited.

## Reviewers

### Botanical

Leaf arrangement and shape, venation, buds, fruit, cones, needles, branch arrangement,
internal contradictions.

Deterministic layer: raises `botanical_contradiction` when the **leading** candidate's own
taxon card declares a feature disqualifying and that feature is visible.

Only the leader is checked. A visible contradiction against a rank-2 alternative is not a
defect in the answer — it is part of why that alternative lost. Raising a finding for it
would mark a sound result as conflicted, which is how a useful signal becomes noise
reviewers learn to ignore.

#### Which contradictions count

`CardMatch` reports two tuples and they are not interchangeable:

| Field | Meaning |
| --- | --- |
| `contradiction_hits` | every visible observation matching a `contradictions` entry of the card |
| `disqualifying_hits` | the subset that could itself have supported an identification: same subject, `supports_identification` under the shared trust projection, and a tier above bark |

Only a disqualifying hit may change a verdict. It alone can reject the user's version, set
`conflicting_evidence`, be printed as the strongest contradiction, or raise the major
`botanical_contradiction` finding. The remainder is still recorded — as a minor
`missing_decisive_feature` finding, and as a note in the report — and can still lower
confidence through the normal finding path.

The reason is the same in both directions. Foliage that cannot be traced to this trunk
cannot convict the tree any more than it can identify it (FAILURE 6), and one patch of bark
cannot disqualify a species that age, weathering, damage and site all reshape (FAILURE 3).
A contradiction with no authority to support a claim has none to destroy one.

### Confusion

Must answer four questions explicitly:

1. What evidence contradicts the leading candidate?
2. Which alternative explains the same observations?
3. What decisive feature is missing?
4. What is the highest defensible taxonomic level?

Deterministic layer, one check per named failure mode in the domain prompt:

| Check | Prompt reference |
| --- | --- |
| `colour_overweighting` when the quality gate flagged colour dependence | FAILURE 7, section 15 |
| `unsupported_claim` when detachable evidence is not confirmed attached | FAILURE 6 |
| `unsupported_claim` when nothing above bark level is resolvable | FAILURE 2, FAILURE 8 |
| `overlooked_alternative` naming a look-alike the model did not propose | FAILURE 1, 4, 5 |
| `region_assumption` when a pack is loaded but the case has no location | section 14 |

The look-alike check fires only when a comparison card actually declares the taxa confusable
**and** none of that card's decisive features is resolvable in the evidence. Absence of a
card means the project has no declared basis for the confusion, and inventing one from a name
in a list would be exactly the guesswork this reviewer exists to catch.

The attachment check is weighted by what else the subject has. When some foliage *was* traced
to this trunk, the loose material was demoted and the verdict never rested on it, so the
finding is `minor` / `no_material_change`. When nothing was traced, the same sentence is the
most important thing on the page and it stays `major`. Without that split, honestly marking a
peripheral observation `unknown` — which the extractor brief explicitly asks for — makes
"foliage could not be traced to this trunk" the headline contradiction of a subject whose
verdict was computed from foliage that *was* traced.

### Confidence

Whether confidence matches the evidence, whether species level is earned, whether negative
evidence is valid, whether contradictions are resolved, whether abstention is better.

Deterministic layer: `resolution_too_specific` when the leading candidate claims a level its
card does not support, `missing_decisive_feature` when the card's high-confidence
requirements are not visible, and `invalid_negative_evidence` when a feature appears in both
`absent_features` and a `not_visible` observation.

The `missing_decisive_feature` finding normally costs one confidence step. It costs none
when the claim rests on a confidence exception its card declares: the exception states how
far that reading reaches without the requirement, and the decision engine's requirement cap
still keeps the claim below high. The finding is recorded either way, and the confidence
ledger shows its step as not applied
([`resolve_confidence`](../src/dendro_inspector/nodes/final_decision.py)).

## Admissibility

Synthesis first resolves one effective subject from the finding, review result and referenced
evidence. At the model boundary, code overwrites `ReviewResult.reviewed_evidence_ids` with the
ids in that reviewer's `ReviewProjection`.

Each cited id is then tested in a fixed order, and the order is load-bearing. **Resolution
first:** an id that names nothing in the packet, or names two things, fails with
`evidence_id_unknown`. **Scope second:** an id that resolves but was not in the reviewer's
projection fails with `out_of_scope`, as does evidence whose source observations belong to
another subject. Testing scope first would be simpler and wrong: the projection currently
carries the whole packet, so every invented id would report `out_of_scope` and
`evidence_id_unknown` would never fire for a model finding — merging "the model hallucinated
an id" and "the model reached into another subject" into one bucket the evaluations cannot
separate.

An inference is checked through every source observation, so it cannot hide a cross-subject
citation. Code-generated deterministic findings are adjudicated against canonical evidence
because they did not originate from the bounded model call; a result carrying no recorded
scope at all is treated as unscoped rather than as empty-scoped, at both the finding and the
rerank-recommendation check.

A deterministic finding also ignores the enclosing model result's `subject_id`. Its own
subject and canonical evidence establish scope; a model choosing to discuss another tree
cannot suppress the code's check. The model-call boundary binds reviewer identity in code.

A finding is **accepted** when one of these holds, in order:

| Test | Reason code |
| --- | --- |
| Cites evidence ids in the recorded projection that exist and belong to the effective subject | `references_visible_evidence` |
| Category is `contract_violation` | `identifies_contract_violation` |
| Category is a contradiction class | `identifies_contradiction` |
| Category is a calibration class | `improves_calibration` |
| `overlooked_alternative` names a known, actionable taxon | `plausible_omitted_alternative` |

**Rejected** otherwise:

| Cause | Reason code |
| --- | --- |
| Unknown or ambiguous evidence id | `evidence_id_unknown` |
| Evidence id was outside the review projection, or evidence/result/finding subjects conflict | `out_of_scope` |
| Materially duplicates an already-adjudicated finding | `restates_existing_finding` |
| An alternative or rerank with nothing admissible behind it | `not_actionable` |
| No evidence reference and no qualifying category | `no_evidence_reference` |

Duplicate detection uses a material signature: category, effective subject, required action,
impact, sorted evidence ids and proposed taxon. Finding id, prose and severity do not decide
whether two findings are the same. Combined with deterministic-first ordering, this prevents a
model restatement from occupying a category/subject slot before the deterministic finding.

## Finding-bound reranks

A recommendation is not a free-floating instruction. For a finding with
`required_action: rerank_candidates`, synthesis requires `recommended_candidates` in that same
`ReviewResult`, resolves the same subject, and passes the proposed ranking through the shared
candidate validator. Unknown taxa, unsupported support ids and candidates with no surviving
candidate-specific evidence are removed. If `proposed_taxon` is present, it must survive.

An accepted finding and its validated ranking are stored together as one `AdmittedRerank`.
Final decision reads only those artifacts, preferring one unambiguous arbiter rerank over
internal reranks. Multiple conflicting rankings at the same level leave the current order
unchanged and recommend escalation; a recommendation behind an absent or rejected finding is
inert.

## Derived actions

| Synthesis field | Derived from |
| --- | --- |
| `retry_required` | any accepted finding with `re_extract_evidence` |
| `unresolvable` | any accepted **critical** finding with `abstain` that is code-raised or cites evidence |
| `unverified_abstention` | any accepted **critical** model finding with `abstain` and no evidence citation |
| `recommendations` | subject-scoped bounds, optionally bound to exact accepted model findings |
| `confidence_delta` | summary: the **lowest** admitted confidence recommendation |
| `resolution_delta` | summary: the **broadest** admitted resolution recommendation |
| `candidate_delta` | exact accepted finding-bound `AdmittedRerank` artifacts |
| `escalation_recommended` | reviewer disagreement, or any accepted critical finding |

### An abstention needs a basis or a second opinion

Calibration and contradiction categories are admitted without a citation, so a model can
file `critical` + `abstain` on its word alone. Routed straight to abstain, that finding would
also switch off the arbiter once every subject had abstained — one reviewer withdrawing a claim
with no independent look at it. So only a **grounded** finding — code-raised, or citing
admitted evidence — sets `unresolvable`. An uncited model finding sets `unverified_abstention`
instead: it stays accepted and critical, the graph proceeds to the escalation gate, where
`unresolved_contradiction` (a hard trigger) requests the arbiter, and
`abstain.settle_unverified_abstention` settles it, per subject, just before final decision:

| After the escalation gate | Outcome for each subject the finding names |
| --- | --- |
| Arbiter ran; its own accepted findings request `critical` + `abstain` for the subject | abstains |
| Arbiter ran and did not | claim stands, still bounded by the reviewer's admitted recommendation |
| Arbitration did not run (disabled, triggers switched off) | abstains — fails closed |

The arbiter is not shown the internal findings, so its agreement is independent. An
uncited finding cannot name another subject's evidence, which is why only uncited findings
can be case-wide; a grounded model finding is always scoped to the subject its evidence
belongs to.

Final decision consumes `recommendations` for the selected subject, not these aggregate
deltas. An explicit result subject must exist; otherwise scope is inferred from accepted
model findings or a single-subject packet. An ambiguous bare recommendation is not applied
to every tree. If all of a result's model findings were rejected, its recommendation is
discarded too. Deterministic findings merged into that result cannot authorize it.

## What a review may do to the level

`GraphConfig.review_authority` (`DENDRO_REVIEW_AUTHORITY`) selects it. There are two
policies, and the default is the released one.

**`recommendation` (default).** The broadest level among a subject's admitted
recommendations is a bound on the claim, and an accepted model finding that asks for
`lower_resolution` takes one step. A review's recommendation is admitted once any of its
model findings is accepted, whatever that finding is about. A reviewer who writes "no
needles are visible, so the genus is not defensible" and recommends `unknown` therefore
withdraws a genus that the card's own diagnostic reading supports.

**`evidence_bound` (experimental, for offline comparison).** A model review moves the level
only through an accepted finding that `final_decision.validated_ground` can check:

| Ground | What code verifies |
|---|---|
| `card_contradiction` | a cited observation is trusted evidence that the leader's card lists under `contradictions`, or that disagrees with the card on a strong-positive path |
| `supported_alternative` | the finding names another card taxon, and the cited observations support that card at least as well as the evidence supports the leader (strong hits first, then supporting hits) |
| `contaminated_support` | the finding reports subject contamination and cites evidence among the leader's own support |

The reviewer's category label is not what validates a contradiction: the cited reading is.
A reading the trust boundary holds as context only, such as a leaf of unknown attachment,
cannot validate one, for the reason it cannot support a claim. A recommendation bound to a
validated finding joins the `reviewer_recommendation` bound at the level the reviewer named,
and that finding's `lower_resolution` action is taken as today. Everything else is recorded
and not applied: the decision derivation lists such recommendations in
`advisory_recommendations` with `no_finding`, `no_cited_evidence` or `not_validated`, and
such actions in `advisory_action_findings`.

The policy changes one thing, how a model review reaches the taxonomic level. It leaves the
rest as it is: code-raised findings, the card cap and tier ceiling, the
discriminating-support rule, the confidence ledger (a recommended confidence still binds),
reranks, correction retries, and abstention on a grounded critical finding. A claim a
retry or an abstention withdrew is therefore withdrawn under both.

It is not enabled anywhere. Compare the two on stored runs with
`coverage-audit --replay --reviews recorded --review-authority evidence_bound`
([evaluation](evaluation.md)); a run recorded before reviews were kept cannot be replayed
this way.

## The name a review held back

Under the released policy a recommended level can withdraw a name the leader's own card
diagnosed. The verdict is still lowered. The name is no longer lost with it:
`final_decision.held_back_hypothesis` records it on the decision as `visual_hypothesis`,
and the answer carries it beside the verdict.

It is recorded only when every row holds, and code checks each one:

| Condition | Checked against |
|---|---|
| The verdict names less than the leader's level | `hypothesis_identity`: the level the model proposed, capped by the card and by the evidence tier exactly as a verdict is |
| A reading the leader's card calls diagnostic of it was made, and reaches that level | the same test the discriminating-support rule uses |
| No trusted reading contradicts the leader's card, cited by a review or not | `match_card`, over the evidence the trust boundary counts |
| No evidence-side bound sits below that level | the composed bounds: `no_discriminating_support`, `diagnostic_combination`, `open_world_outranked` |
| No code-raised finding asked for a broader level, and no accepted model finding has a ground from the table above | `validated_ground` |

What remains is a verdict lowered by review that code could not check, and `held_back_by`
says which: a recommended level (`reviewer_recommendation`), an unverified finding's
`lower_resolution` action (`review_finding`), or an abstention (`abstention`).

A hypothesis is not a second verdict:

- It is never `selected_taxon` and never at or above the verdict's level; the
  `FinalDecision` contract rejects both. It carries no confidence.
- No model sees it. The arbiter is shown the provisional verdict's taxon, level, confidence
  and status, and nothing else of the decision.
- `dendro eval` and `coverage-audit` read `selected_taxon`, so a hypothesis is never
  counted as an answer, right or wrong.
- A structured result gives the two names as separate objects: `validated_identity` (the
  confirmed name with its status, or null) and `visual_hypothesis` (the name, its status,
  what held it back, and an explanation). The text adds one line after the verdict,
  "Unconfirmed hypothesis. Most likely ... — not confirmed.", and a verdict that names
  nothing then reads "no taxon is confirmed from this photograph".
- The consistency check reports a result that confirms a name its decision did not select
  (`validated_identity_mismatch`), that changes or drops the decision's hypothesis
  (`hypothesis_mismatch`), or whose text omits it (`text_omits_hypothesis`).

`HypothesisStatus` has one value, `evidence_supported`. Two weaker kinds of name are
deliberately not offered. A leader its card merely fits stays where it was, listed with its
rivals under "still consistent with this evidence": singling it out as "most likely" is the
answer the discriminating-support rule exists to stop. A proposal admission rejected is
not offered at all.

Under `evidence_bound` an unverifiable recommendation does not lower the verdict, so the
name is confirmed and no hypothesis is left to record; an abstention can still leave one.

## Corrections vs caps

Some findings describe a problem the decision engine already fixes. `resolution_too_specific`
is the example: `cap_resolution()` truncates a species claim to what the card supports, so
the finding is recorded with `required_action: none`.

Without that, the same mistake is punished twice — the cap takes species to genus, then the
finding's `lower_resolution` takes genus to family, and a straightforward conifer is reported
at family level for having been overclaimed once. The rule: **a finding requests an action
only when nothing else will apply it.**

Related: `resolve_resolution` skips a `lower_resolution` action when the card cap already
moved the claim.

### A recommendation is a floor as well as a ceiling

The card cap is not the only thing that can apply a correction before the finding does. A
reviewer that fills in `recommended_resolution` or `recommended_confidence` has stated where
its own findings stop, and the subject's recommendation already supplies that bound. The
finding raised alongside it is the *reason* for the recommendation, not a second, separate
correction — so applying both charges once for the cap and once for the reason.

Two symptoms, both observed on a live run:

- three reviewers write up one species overclaim and each files `lower_confidence`; every
  accepted finding costs a full step, so a claim all three recommended at `high` arrives at
  `low`;
- the same reviewers recommend `genus` as the highest defensible level and file
  `lower_resolution` to say so; the composed bound is already `genus`, and the action takes it
  to `family` — one step below the answer every reviewer asked for.

`AdmittedRecommendation` binds the exact accepted finding, its subject and reviewer. A
recommendation is a floor only for that finding; matching a reused finding ID is insufficient.
Another review's bare `high` recommendation cannot waive an accepted confidence downgrade.
Deterministic findings keep applying below every model recommendation.

Legacy syntheses with `recommendations=None` remain readable. Their aggregate deltas may
cap a single-subject decision but cannot waive findings or become bounds for multiple
subjects. New synthesis always writes a tuple, including an empty one.

The abstention path retains scope too: `GraphState.abstention_bounds` records one conservative
bound per subject named by a blocking finding. An unscoped blocking finding covers the case.
Bounds are computed from each subject's provisional result, so a weak or unknown subject
cannot erase a stronger neighbour. The run-level `abstained` flag is an aggregate; each final
decision records whether that subject abstained. Remaining subjects still pass escalation.

## What the trace keeps of a review

The graph state keeps one synthesis per pass, and a correction retry clears the reviews
behind it. The run trace keeps the rest, so a downgrade can be traced to the reviewer and
finding that asked for it:

- `review_rounds` holds every pass in the order it ran: each internal round, including one
  a retry replaced, then the arbiter's. Each review is recorded with its status, its
  recommended levels and ranking, and every finding it raised with the adjudicated status
  and reason code. Rejected findings are kept. `recommendation_admitted` says whether the
  review's levels became bounds. `model_called` is false for a reviewer an applied
  [review plan](model-roles.md#adaptive-routing-experimental) did not call: that record
  holds only what the reviewer's code raised, and its status is not a model's judgement.
- `routing_history` holds one review plan per internal round when adaptive routing is on:
  the route, every rule evaluated, the reviewer models called and skipped, the questions
  asked, and the verdict code alone had composed.
- Each finding has a `finding_ref` that is unique within the run. Decision derivations and
  abstention records cite it ([composition record](architecture.md#the-composition-record)).
- `abstentions` records, per subject, why a claim was withdrawn: `unresolvable_finding`,
  `retry_budget_spent`, `unverified_abstention_confirmed` or
  `unverified_abstention_unarbitrated`, with the findings that asked for it.
- `escalation_suppressed_by` and `escalation_hard_reasons` record why arbitration did or did
  not run.

The record is attribution only. No rule reads it, and a synthesis built without it decides
exactly as before. Summaries are stored as the reviewer wrote them; photographs and prompts
are not stored.

In trace schema 3, each round's `evidence_revision` identifies the corresponding raw and
effective packet in `evidence_history`. Resolve a finding's local evidence ID against that
revision, not against the compact final ledger: a correction retry may reuse the same ID for
a different reading. Candidate validations follow the same rule. See
[evidence revisions](architecture.md#evidence-revisions).

`dendro coverage-audit --replay --reviews recorded` replays a stored run with these reviews
([evaluation](evaluation.md#replay-with-the-recorded-reviews)).

## Status reflects the answer, not the history

`decide_status` recomputes contradictions against the **selected final identity** rather than
reading accepted findings against the original narrow candidate. After an arbiter rerank, a
finding raised against the candidate that lost says nothing about the one that won. Likewise,
a species-specific contradiction does not automatically contradict a genus/family identity
unless that broader card declares it. Status describes the answer returned, not its history.

## Implementation references

- [`src/dendro_inspector/nodes/review_synthesizer.py`](../src/dendro_inspector/nodes/review_synthesizer.py)
- [`src/dendro_inspector/schemas/reviews.py`](../src/dendro_inspector/schemas/reviews.py)
- [`tests/unit/test_review_synthesis.py`](../tests/unit/test_review_synthesis.py)
- [`src/dendro_inspector/observability/events.py`](../src/dendro_inspector/observability/events.py)
- [`tests/unit/test_review_attribution.py`](../tests/unit/test_review_attribution.py)
- [`src/dendro_inspector/nodes/final_decision.py`](../src/dendro_inspector/nodes/final_decision.py) — `held_back_hypothesis`
- [`tests/unit/test_visual_hypothesis.py`](../tests/unit/test_visual_hypothesis.py)
