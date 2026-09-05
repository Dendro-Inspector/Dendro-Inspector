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
| `unresolvable` | any accepted **critical** finding with `abstain` |
| `recommendations` | subject-scoped bounds, optionally bound to exact accepted model findings |
| `confidence_delta` | summary: the **lowest** admitted confidence recommendation |
| `resolution_delta` | summary: the **broadest** admitted resolution recommendation |
| `candidate_delta` | exact accepted finding-bound `AdmittedRerank` artifacts |
| `escalation_recommended` | reviewer disagreement, or any accepted critical finding |

Final decision consumes `recommendations` for the selected subject, not these aggregate
deltas. An explicit result subject must exist; otherwise scope is inferred from accepted
model findings or a single-subject packet. An ambiguous bare recommendation is not applied
to every tree. If all of a result's model findings were rejected, its recommendation is
discarded too. Deterministic findings merged into that result cannot authorize it.

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
