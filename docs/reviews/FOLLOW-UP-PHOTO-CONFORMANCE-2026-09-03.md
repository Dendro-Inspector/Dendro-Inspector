# Follow-up photograph conformance review — 2026-09-03

- **Status:** Point-in-time review; primary behavioural correction verified in the working tree
- **Owner:** Dendro Inspector maintainers
- **Date:** 2026-09-03
- **Scope:** diagnosis at `3dcd8cf`; working-tree correction and verification over that revision

This review records a generalized policy defect exposed by a local run over an unlabelled
owner photograph. The photograph and its absolute path are intentionally omitted: they are
not public evaluation data. This file is evidence about the implementation at the stated
revision, not a second source of truth for follow-up policy. The active specification remains
section 16 of the domain prompt; the implementation lives in
[`photo_planner.py`](../../src/dendro_inspector/nodes/photo_planner.py) and
[`final_decision.py`](../../src/dendro_inspector/nodes/final_decision.py).

## Executive result

Claim: the unknown/low-confidence verdict was consistent with a bark-only, multi-tree view.

Status: **VERIFIED against the behavioural specification**

Evidence: the run retained one non-exclusive bark observation, returned no taxon, and kept
confidence below 50/100. The exact botanical identity remains unknown because no independent
reference label exists.

Claim: the next-photo request did not establish ownership before asking for leaf morphology.

Status: **VERIFIED**

Evidence: the structured result requested `leaf_upper_macro` while its admitted limitations
said that no leaf was demonstrably attached to the foreground trunk. A synthetic regression,
`test_bark_only_multi_tree_follow_up_proves_leaf_ownership_first`, reproduced the same result
before any implementation change.

Claim: the weak-photo response rendered an empty alternatives section while naming plausible
alternatives under uncertainty.

Status: **VERIFIED**

Evidence: a second synthetic regression,
`test_unknown_result_omits_an_empty_nearest_alternatives_section`, reproduced the literal
`Why not the nearest alternatives: none recorded` block for an unknown decision whose
unresolved questions named plausible alternatives.

## Generalized failure class

The defect requires all of these conditions:

1. the subject is a standing tree or bark view;
2. bark is the strongest admitted evidence;
3. the evidence packet says the frame may contain multiple taxa;
4. validation retains only one candidate, so no multi-taxon comparison card is selected;
5. that candidate's flat follow-up list puts a morphology macro before an attachment view.

The morphology request may produce a sharp photograph that the graph is still not allowed to
credit to the trunk. This is a provenance-ordering defect, not a request for looser candidate
admission. The candidate boundary in
[`candidate_validation.py`](../../src/dendro_inspector/knowledge/candidate_validation.py)
must remain unchanged.

## Competing causes and discriminating evidence

Three candidate causes were considered before editing:

1. **Wrong input image.** Rejected: the measured image digest matched its local manifest and
   visual inspection confirmed a bark-dominant multi-tree scene.
2. **Stochastic wording error.** Rejected: `FinalDecision.best_next_photo` contained the wrong
   structured target before response composition, and an existing deterministic test required
   the same macro-first fallback.
3. **Loss of provenance priority after candidate validation.** Confirmed: no current detachable
   observation existed for `attachment_request`; only one taxon remained, so
   `comparisons_for` returned no card; the selector then took the first entry from the surviving
   taxon's follow-up list.

The cheapest discriminating test constructed bark-only evidence with
`possible_multiple_taxa=true`, one validated broadleaf candidate, and no detachable
observation. It failed with `leaf_upper_macro` before the correction.

## Selected correction

The correction stays on the deterministic side of the architecture boundary:

- retain current candidate validation and claim caps;
- when the subject is a standing tree or bark view, bark is the strongest evidence, the frame
  may contain multiple taxa, and the selected knowledge list declares a canonical attachment
  photograph, request that attachment view before morphology;
- when the evidence-quality route has no candidate-specific list, request generic foliage or a
  reproductive structure visibly attached to the same subject;
- reuse the existing attachment target and reason rather than introduce an alias;
- render the nearest-alternatives block only when a structured ruled-out alternative exists.

This does not make `possible_multiple_taxa` evidence against a claim. It affects only the order
in which new evidence is requested.

## Benchmark-change justification

```yaml
change_justification:
  observed_failure: >-
    A local unlabelled bark-only run asked for a leaf upper-surface macro even though leaf
    ownership was unresolved.
  generalized_failure_class: >-
    Multi-tree bark views can request detachable-organ morphology before provenance, yielding
    evidence the graph cannot credit to the subject.
  independent_domain_source: >-
    Domain prompt sections 2 and 16: ambiguous foliage must not support the trunk, and follow-up
    photography includes the attachment point and a branch continuously connected to the trunk.
  new_non_golden_tests:
    - tests/unit/test_live_model_regressions.py::test_bark_only_multi_tree_follow_up_proves_leaf_ownership_first
    - tests/unit/test_live_model_regressions.py::test_unknown_result_omits_an_empty_nearest_alternatives_section
    - tests/unit/test_photo_planner.py::test_multi_tree_bark_view_proves_ownership_before_requesting_foliage_detail
  affected_rules:
    - src/dendro_inspector/nodes/photo_planner.py
    - src/dendro_inspector/nodes/final_decision.py
    - src/dendro_inspector/nodes/response_composer.py
  benchmark_cases_rechecked: []
```

## Additional findings outside this correction

These are separate defects and should remain separate changes:

- the loopback bridge reports token counts as zero while upstream worker metadata records
  non-zero usage, so the canonical trace cannot distinguish missing accounting from free work;
- a dead worker's claim and capacity leases are recovered only after the 1,800-second time-to-live
  even though the lease stores its process identifier;
- a run marked `code_dirty=true` records a commit SHA but not the working-tree patch, so that SHA
  cannot reproduce the exact execution.

## Verification record

Claim: the correction passes the repository gates.

Status: **VERIFIED**

Evidence: all three regressions were observed failing before their respective implementation
changes, then passed with the surrounding attachment and photo-planner suites (`18 passed`).
The repository gates then reported:

- `ruff format --check .`: 169 files already formatted;
- `ruff check .`: all checks passed;
- `mypy`: no issues in 120 source files;
- `pytest`: 779 passed, 1 expected failure;
- `dendro eval --suite public`: 24 passed, 0 failed, overconfidence rate 0.0.

The sandboxed pytest attempt could not manage its Windows temporary directory and produced no
valid summary. The exact `pytest` gate was rerun outside the filesystem sandbox and produced the
result above. No golden benchmark case was used to define or verify the correction.

Not verified: field accuracy, the omitted local photograph's taxon, or the three additional
infrastructure findings above.

Next verification step: review the diff, then address trace usage, lease recovery and dirty-run
reproducibility as separate changes.
