# Review — the v0.2.2 correctness boundary

- **Status:** Point-in-time review of `9ac56f0`. All eight concerns resolved; see Resolution.
- **Owner:** Dendro Inspector maintainers
- **Date:** 2026-07-26
- **Scope:** the working tree that became `9ac56f0`, reviewed before it was committed

An independent review of work produced across several agent sessions on 2026-07-25/26 that
was never verified end-to-end by the session that commissioned it — that session ended
immediately after the last agent reported, before the promised "independently review the diff
and rerun all mandatory gates" step ran.

This is a dated assessment, not a source of truth for any rule in `AGENTS.md`.

## Resolution

| # | Resolved by |
|---|---|
| C1 | `dendro prompt-seal` — dry run by default, `--write` applies, never touches the policy revision. `prompts/seal.py`, documented in `README.md`, `prompts/README.md`, `CONTRIBUTING.md`, `docs/architecture.md`, `AGENTS.md` §12. |
| C2 | "Tune them freely" replaced; `prompts/README.md` and `CONTRIBUTING.md` now say tuning a node prompt requires a re-seal, committed with the change. |
| C3 | `near-miss-vocabulary-001` and `partial-visibility-cap-001` added, restoring both deleted behaviours; proven non-vacuous by negative-control probes. The rewritten six stay as conformance cases. |
| C4 | `TaxonIdentity.provenance` added and migrated across 25 cards: family placements say `inferred`, the five genus identities the prompt names keep `domain_prompt`. Contract tests enforce it. `docs/dataset-policy.md` scopes the card-level block to feature rules. |
| C5 | `CHANGELOG.md` now discloses the six rewritten fixtures and the `arbiter-ranking-001` `medium` → `low` move, and states the apples-to-oranges caveat. `docs/evaluation.md` records the result instead of gating it. |
| C6 | `eval` calls `configure_logging` like `inspect`; the filter warning keeps its fields and its redaction. |
| C7 | URLs point at `Dendro-Inspector/Dendro-Inspector` in `pyproject.toml`, `CHANGELOG.md` and `.github/ISSUE_TEMPLATE/config.yml`; `AGENTS.md` §17.5 settled, with the tags-and-Discussions caveats recorded. |
| C8 | `escalations_correct` → `escalation_decisions_correct`, with its denominator stated in the schema and in `docs/evaluation.md`. |

Two findings surfaced while resolving these and are **not** fixed:

- **`bark.flake_geometry` is asked for by the prompts and matched by no taxon card.** The
  planner and extractor prompts request it and `knowledge/comparisons/pinus-picea-larix.yaml`
  names it, but no card declares it, so a candidate resting on it is never admitted.
  `near-miss-vocabulary-001` documents that rejection as correct; it does not decide whether
  the prompts or the cards are wrong. That is a §12 conformance call for the owner.
- **`larix` is in the knowledge pack but not in the domain prompt.** Its provenance no longer
  claims otherwise, and `test_a_card_claiming_the_domain_prompt_is_named_in_it` now fails on
  any card that repeats the error. Whether the taxon belongs in a pack derived from section 14
  is still the owner's call.

---

## What was verified

All five §4.5 gates were re-run from a clean shell against the current working tree.

| Gate | Result | Status |
|---|---|---|
| `ruff format --check .` | 122 files already formatted | `VERIFIED` |
| `ruff check .` | All checks passed | `VERIFIED` |
| `mypy` | no issues in 91 source files | `VERIFIED` |
| `pytest` | 458 passed in 20.67s | `VERIFIED` |
| `dendro eval --suite public` | 14 cases, 14 passed, 0 failed, overconfidence 0.0 | `VERIFIED` |

Other checks that came back clean:

- **Domain prompt is byte-identical to `HEAD`.** SHA-256 `b4c38c00…cf7d7`, matching both
  `prompts/versions.yaml` and the hash every agent reported. Working tree CRLF is intentional
  (`.gitattributes` `-text`) and equals the committed blob.
- **Node prompt hashes are line-ending portable.** `prompts/nodes/*.md` are LF in the worktree
  and their bytes equal the committed blobs, so the manifest will not break on a Linux checkout.
- **Taxon cards are additive only.** 25 files, 152 insertions, 0 deletions — structural
  `native_resolution` / `broader_identities` metadata. No botanical feature, threshold or
  contradiction rule was tuned, so §16's core prohibition holds.
- **Version is consistent at 0.2.2** across `pyproject.toml`, `__init__.py` and
  `prompts/versions.yaml`.
- **Baseline drift is what the agent reported.** Diffing `public-v0.2.1.json` against
  `public-v0.2.2.json` shows exactly one behavioural change on the nine pre-existing cases —
  `arbiter-ranking-001` confidence `medium` → `low`. Everything else is new fields
  (`evidence_tier`, `selected_taxon_display_name`, `admitted_rerank_finding_ids`).

---

## Concerns

Ordered by severity. Each carries the evidence it rests on.

### C1 — Editing the shipped domain prompt in place now hard-fails, with no escape hatch

`README.md` calls the domain prompt "an opaque, user-managed artifact … edited only by its
owner", and `cli.py:172` tells the user "Put your own prompt in
`prompts/domain/system-prompt.md`". Doing exactly that now aborts the run.

The custom-manifest escape hatch in `prompts/library.py:324-335` only triggers when
`DENDRO_DOMAIN_PROMPT_PATH` differs from the default. Replacing the file *at* the default
path skips that branch and dies one check later on the hash comparison
(`prompts/library.py:353-359`).

Evidence — temp copy of `prompts/`, one comment line appended to the domain prompt:

```text
PromptPolicyError -> Domain prompt hash mismatch for …\prompts\domain\system-prompt.md:
manifest expects b4c38c00…cf7d7, found 7ec0cff3…8c26a.
```

There is no command to regenerate the manifest: the CLI exposes `inspect`, `eval`, `graph`
and `prompt_info`, and `prompt_info` reports hashes rather than writing them. The owner must
hand-edit `prompts/versions.yaml` and compute a SHA-256 out of band, and nothing in
`README.md`, `prompts/README.md` or `docs/architecture.md` says so.

Fail-closed is the right default; the missing half is the supported path for the artifact the
project exists to carry. Options: an `dendro prompt-seal` style command, or treating the
default path as self-attesting and reserving hash enforcement for non-default deployments.

### C2 — "Tune node prompts freely" is now false, in the same file that pins their hashes

`prompts/README.md:26` still reads "One prompt per model-backed node. These are engineering
surface: tune them freely." The manifest section further down the same file pins every node
prompt's SHA-256, and validation is fail-closed.

Evidence — temp copy, one line appended to `nodes/botanical-reviewer.md`:

```text
PromptPolicyError -> Node prompt hash mismatch for …\prompts\nodes\botanical-reviewer.md:
manifest expects 5f96171e…d1458, found 36eb61a0…49ccc.
```

Two statements about the same contract, in one file, that cannot both be true — the exact
condition `AGENTS.md`'s Main Rule exists to prevent. Either restate the sentence to say
tuning requires a manifest update, or exempt node prompts from hash pinning and pin only the
file set plus `revision`.

### C3 — Six public fixtures were rewritten to match card vocabulary; the suite lost the near-miss coverage it had

The stricter admission boundary invalidated six existing fixtures. They were repaired by
editing the *model output* until it matched the cards, not by editing expectations:

| Fixture | Before | After |
|---|---|---|
| `arbiter-review.json` | `bark.flake_geometry` = `small_thin_scales` | `bark.texture` = `scaly_plates` |
| `primary-conflict.json` | `bark.flake_geometry` = `thin_irregular_edge_lifting` | `bark.texture` = `scaly_plates` |
| `foliage-unattached.json` | `bark.texture` = `grey_furrowed_old` | `bark.pattern` = `diamond_fissures` |
| `bark-light-trunk.json` | `bark.pattern` = `pale_with_dark_marks` | `white_papery_with_black_marks` |
| `bark-rough-oak-claim.json` | `weathered_grey_uneven`; support `["obs-1","inf-1"]` | `diamond_fissures`; support `["obs-2"]` |
| `primary-pass.json` | needles `visibility: partial` | `visibility: clear` |

Every one of those "after" values is a verbatim card token. The fixtures previously modelled
what a vision model actually emits — approximate, paraphrased, sometimes citing the wrong
observation id. That was the interesting input. After the rewrite, the validator that rejects
non-exact tokens is only ever exercised on input pre-conformed to it, in these six cases.

`AGENTS.md` §16 explicitly permits adding public cases, and the five new cases (C-side
regressions for the new boundary) are legitimate under it. The concern is narrower: it is the
*repair method* on the six old ones. `bark.flake_geometry` is a real feature path used by
`knowledge/comparisons/pinus-picea-larix.yaml:10`,
`prompts/nodes/evidence-extractor.md:106` and `prompts/nodes/planner.md:13` — so the planner
still asks for a feature no taxon card can match, and after these edits no public case covers
that mismatch. Two other fixtures (`malformed-retry`, `primary-insufficient`) still use it,
but neither asserts candidate admission.

`primary-pass.json` is the sharpest instance. The implementing agent reported the honest
reading: "primary-pass expects MEDIUM although its only Pinus-specific needle evidence is
`visibility=partial` and now correctly caps to LOW". The behaviour change was real and
intended. Flipping the fixture to `clear` preserved the green case and deleted the coverage —
recording `max_confidence: low` in `conifer-log-001.yaml` would have documented the new
boundary instead.

Suggested resolution: keep the rewritten fixtures as conformance cases, and add back one case
per deleted behaviour — near-miss vocabulary rejection, and partial-visibility confidence
capping on the happy path.

### C4 — Cards gained content that is not in the domain prompt, under provenance that says it is

Every card now declares a family-level `broader_identities` entry, e.g.
`knowledge/taxa/quercus.yaml`:

```yaml
broader_identities:
  - resolution: family
    taxon_id: fagaceae
    display_name: Fagaceae (букові)
```

The domain prompt contains no family names at all — `grep -c` for
`fagaceae|salicaceae|pinaceae|rosaceae|family` over `prompts/domain/system-prompt.md` returns
`0`. Each card's `provenance` block is unchanged and still reads:

```yaml
provenance:
  source: "Domain prompt section 14 (БАЗОВІ ПОРОДИ)"
  source_type: domain_prompt
```

`docs/dataset-policy.md:26-48` makes provenance mandatory precisely so a later reviewer can
list every rule nobody verified, and §16 states the 25 cards "derive from the domain prompt's
section 14 and nothing else". Both are now inaccurate for these fields. The botany is
uncontroversial — *Quercus* is in Fagaceae — which is what makes it easy to miss.

Fix is small: a per-field provenance override with `source_type: inferred`, or a note in
`docs/dataset-policy.md` scoping the card-level block to feature rules. The contract test
should then require it, or this recurs.

### C5 — `CHANGELOG.md` announces 0.2.2 as released; `docs/evaluation.md` says the results are not publishable yet

`docs/evaluation.md:169-177` (new text): "Do not publish v0.2.2 public-suite metrics until all
fourteen cases pass, **changes to existing outcomes are reviewed**, and `public-v0.2.2` lands
as the frozen baseline."

`CHANGELOG.md` already carries `## [0.2.2] — 2026-07-26` as a dated release section. Fourteen
cases pass and the baseline file exists, but the middle condition — human review of the
changed outcomes — is the thing this document is recording, and it has not happened.

The changelog is also silent on the two facts a reader would most want: six fixtures were
rewritten (C3), and `arbiter-ranking-001` confidence dropped `medium` → `low`. The "Added"
section says the suite "expands from nine to fourteen cases" and stops there. Under §3 that
omission is the reportable part, not the expansion.

Also note the drift comparison is not apples-to-apples: only one of nine outcomes changed, but
six of those nine ran on rewritten inputs.

### C6 — `eval` never configures logging, so warnings escape unformatted

`configure_logging` is called only in `cli.py:130`, inside `inspect`. The `eval` command
(`cli.py:180`) never calls it, so `logger.warning("candidate_validation_filtered", extra={…})`
in `nodes/candidate_generator.py:64-73` falls through to Python's last-resort handler.

Observed during the gate run, printed above the suite summary:

```text
candidate_validation_filtered
candidate_validation_filtered
```

`case_id`, `subject_id`, `dropped_evidence_ids` and `rejected_taxa` are all discarded — the
one signal that says which candidates the new boundary removed, and the field you would want
when a case unexpectedly abstains. Also bypasses `JsonFormatter`, hence the redaction filter.
One `configure_logging` call in `evaluate_suite` fixes it.

### C7 — Changelog compare links point at a repository that does not exist

```text
[Unreleased]: https://github.com/OWNER/dendro-inspector/compare/v0.2.2...HEAD
[0.2.2]:      https://github.com/OWNER/dendro-inspector/compare/v0.2.1...v0.2.2
```

`origin` is `https://github.com/Dendro-Inspector/Dendro-Inspector.git` — both the `OWNER`
placeholder and the repository name are wrong. `git tag` returns empty, so `v0.2.1` and
`v0.2.2` do not resolve either. The placeholder predates this work (the `0.1.0` line has it
too) but the new lines propagate it into a public repository.

### C8 — `escalations_correct` reads as a contradiction in the frozen baseline

`evals/baselines/public-v0.2.2.json` records `escalations_expected: 9`,
`escalations_observed: 9`, `escalations_correct: 14`. The value is right —
`evaluation/metrics.py:83-87` counts every case where `arbiter_used == require_escalation`,
including the correct non-escalations — but it sits beside two fields that are the
precision/recall denominators, so it reads as "14 correct out of 9". §16's "report the
denominator" rule is about exactly this kind of number. Rename to
`escalation_decisions_correct` and pair it with `cases`.

---

## Suggested order

1. **C1, C2** — before any public push. They break the documented workflow for the artifact
   the project is built around.
2. **C5, C7** — before tagging 0.2.2. Release record accuracy.
3. **C3, C4** — coverage and provenance debt. Cheap now, expensive after the first real
   photograph enters `evals/golden/`.
4. **C6, C8** — small, independent.

Nothing here is a correctness defect in the v0.2.2 hardening itself. The determinism boundary
work reads as sound and its 458 tests pass. The concerns are about what the change did to the
surfaces around it: the owner's prompt workflow, the evidence the suite still collects, and
the accuracy of the record.

---

## After the fixes

All five gates re-run against the resolved tree: `ruff format --check` 124 files formatted,
`ruff check` clean, `mypy` clean over 92 source files, `pytest` **480 passed**, public suite
**16 cases, 16 passed, 0 failed, overconfidence 0.0**. The domain prompt is still
`b4c38c00…cf7d7` and untouched since before the hardening began.
