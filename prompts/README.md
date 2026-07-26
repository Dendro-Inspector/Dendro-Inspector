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
domain prompt is loaded. A deployment using another domain prompt must set both
`EVIL_DUCK_DOMAIN_PROMPT_PATH` and `EVIL_DUCK_PROMPT_MANIFEST_PATH`; the external manifest
must bind that exact path and hash to the active deterministic policy.

## `nodes/*.md` — project-owned

One prompt per model-backed node. These are engineering surface: tune them freely. They are
separate from the domain prompt precisely so that tuning a reviewer does not change the
domain prompt's hash and invalidate the audit trail.

Filenames are kebab-case; node identifiers in Python are snake_case
(`evidence_extractor` loads `nodes/evidence-extractor.md`).

## Composition order

After the manifest validates, `PromptLibrary.compose()` assembles the cached admitted bytes
in this fixed order:

1. the domain prompt, verbatim;
2. the selected response-register note, when non-empty;
3. the node prompt;
4. case context, fenced and explicitly labelled as untrusted data.

Untrusted material is always last and always labelled. Instruction-like text inside it is
evidence about the input, never an instruction to the model. If the bundle has not yet been
validated, composition performs validation first and fails closed on any mismatch.

## `versions.yaml`

This is the fail-closed runtime compatibility manifest, not advisory release metadata. Its
frozen schema binds:

- manifest schema version `1`;
- deterministic policy revision `0.2.2`;
- canonical domain-prompt path and SHA-256;
- node-prompt root and revision;
- the exact node-prompt file set and every file's SHA-256.

`runner.build_context()` validates the complete bundle before constructing the provider
registry. `evil-duck prompt-info` reports the prompt hash, manifest hash, revisions and
`compatible` status; an incompatible bundle exits before any provider can be called.

A custom manifest is an operator attestation that the supplied natural-language prompt is
compatible with policy `0.2.2`. Hash validation proves byte identity only; it does not prove
semantic equivalence.
