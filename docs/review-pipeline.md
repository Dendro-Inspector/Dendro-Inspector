# Review pipeline

- **Status:** Current
- **Owner:** Evil Duck Dendro Inspector maintainers
- **Date:** 2026-07-26
- **Last-verified:** 2026-07-26

## The rule

**A finding is not accepted because a model produced it.**

Three reviewers plus an arbiter emit findings. Every one of them faces the same
deterministic admissibility test in
[`nodes/review_synthesizer.py:adjudicate`](../src/evil_duck_dendro/nodes/review_synthesizer.py).
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

### Confidence

Whether confidence matches the evidence, whether species level is earned, whether negative
evidence is valid, whether contradictions are resolved, whether abstention is better.

Deterministic layer: `resolution_too_specific` when the leading candidate claims a level its
card does not support, `missing_decisive_feature` when the card's high-confidence
requirements are not visible, and `invalid_negative_evidence` when a feature appears in both
`absent_features` and a `not_visible` observation.

## Admissibility

Synthesis first resolves one effective subject from the finding, review result and referenced
evidence. Unknown/ambiguous ids fail with `evidence_id_unknown`; evidence whose source
observations belong to another subject fails with `out_of_scope`. An inference is checked
through every source observation, so it cannot hide a cross-subject citation.

A finding is **accepted** when one of these holds, in order:

| Test | Reason code |
| --- | --- |
| Cites evidence ids that exist and belong to the effective subject | `references_visible_evidence` |
| Category is `contract_violation` | `identifies_contract_violation` |
| Category is a contradiction class | `identifies_contradiction` |
| Category is a calibration class | `improves_calibration` |
| `overlooked_alternative` names a known, actionable taxon | `plausible_omitted_alternative` |

**Rejected** otherwise:

| Cause | Reason code |
| --- | --- |
| Unknown or ambiguous evidence id | `evidence_id_unknown` |
| Evidence/result/finding subjects conflict | `out_of_scope` |
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
| `confidence_delta` | the **lowest** confidence any reviewer recommended |
| `resolution_delta` | the **broadest** resolution any reviewer recommended |
| `candidate_delta` | exact accepted finding-bound `AdmittedRerank` artifacts |
| `escalation_recommended` | reviewer disagreement, or any accepted critical finding |

Deltas take the most conservative recommendation. Downgrades compose; nothing here raises a
claim.

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

## Status reflects the answer, not the history

`decide_status` recomputes contradictions against the **selected final identity** rather than
reading accepted findings against the original narrow candidate. After an arbiter rerank, a
finding raised against the candidate that lost says nothing about the one that won. Likewise,
a species-specific contradiction does not automatically contradict a genus/family identity
unless that broader card declares it. Status describes the answer returned, not its history.

## Implementation references

- [`src/evil_duck_dendro/nodes/review_synthesizer.py`](../src/evil_duck_dendro/nodes/review_synthesizer.py)
- [`src/evil_duck_dendro/schemas/reviews.py`](../src/evil_duck_dendro/schemas/reviews.py)
- [`tests/unit/test_review_synthesis.py`](../tests/unit/test_review_synthesis.py)
