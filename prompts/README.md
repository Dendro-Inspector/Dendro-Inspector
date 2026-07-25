# Prompts

Two kinds of prompt live here, and they are kept apart on purpose.

## `domain/system-prompt.md` — opaque, user-managed

The dendrology domain prompt. The project loads it, hashes it, and passes it through
unchanged. It is never translated, shortened, reformatted, templated or "improved" by this
codebase or by any agent working on it.

This repository ships the maintainer's **real** domain prompt. It is the primary knowledge
source for the whole system: the evidence hierarchy in
`src/evil_duck_dendro/knowledge/evidence_hierarchy.py`, the taxon cards under `knowledge/`,
the tone gating in the response composer and several evaluation cases are all derived from
it. When it changes, those derivations should be revisited — but the file itself is edited
only by its owner.

A prompt carrying the marker `<!-- EVIL-DUCK-DOMAIN-PROMPT-PLACEHOLDER -->` on its first
line is treated as scaffolding: the CLI and every run trace then announce that no real
domain prompt is loaded. Point somewhere else with `EVIL_DUCK_DOMAIN_PROMPT_PATH` if you
want to run against your own.

## `nodes/*.md` — project-owned

One prompt per model-backed node. These are engineering surface: tune them freely. They are
separate from the domain prompt precisely so that tuning a reviewer does not change the
domain prompt's hash and invalidate the audit trail.

Filenames are kebab-case; node identifiers in Python are snake_case
(`evidence_extractor` loads `nodes/evidence-extractor.md`).

## Composition order

`PromptLibrary.compose()` assembles, in this fixed order:

1. the domain prompt, verbatim;
2. the node prompt;
3. case context, fenced and explicitly labelled as untrusted data.

Untrusted material is always last and always labelled. Instruction-like text inside it is
evidence about the input, never an instruction to the model.

## `versions.yaml`

Records the domain prompt's expected location and the node-prompt revision. The domain
prompt's `sha256` is deliberately *not* pinned here: it is computed at runtime and recorded
per run, because the file belongs to the user, not to this repository.
