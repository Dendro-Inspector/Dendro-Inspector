# Prompts

Two kinds of prompt live here, and they are kept apart on purpose.

## `domain/system-prompt.md` — opaque, user-managed

The dendrology domain prompt. The project loads it, hashes it, and passes it through
unchanged. It is never translated, shortened, reformatted, templated or "improved" by this
codebase or by any agent working on it.

This repository ships the maintainer's **real** domain prompt. It is the primary knowledge
source for the whole system: the evidence hierarchy in
`src/dendro_inspector/knowledge/evidence_hierarchy.py`, the taxon cards under `knowledge/`,
the tone gating in the response composer and several evaluation cases are all derived from
it. When it changes, those derivations should be revisited — but the file itself is edited
only by its owner.

A prompt carrying the marker `<!-- DENDRO-DOMAIN-PROMPT-PLACEHOLDER -->` on its first
line is treated as scaffolding: the CLI and every run trace then announce that no real
domain prompt is loaded. A deployment using another domain prompt must set both
`DENDRO_DOMAIN_PROMPT_PATH` and `DENDRO_PROMPT_MANIFEST_PATH`; the external manifest
must bind that exact path and hash to the active deterministic policy.

Replacing this file — here, or at a configured path — changes its hash, so the manifest that
pins it has to be re-sealed in the same step. See [re-sealing](#re-sealing).

## `nodes/*.md` — project-owned

One prompt per model-backed node. These are engineering surface: change them when a node
needs different instructions, without owner sign-off and without touching the domain prompt.
What editing one *does* require is a re-seal — `versions.yaml` pins every node prompt's
SHA-256 and validation is fail-closed, so an edited node prompt aborts the next run until
the manifest attests its new bytes. See [re-sealing](#re-sealing).

They are separate from the domain prompt precisely so that changing a reviewer does not
change the domain prompt's hash and invalidate the audit trail.

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
- deterministic policy revision `0.2.3`;
- canonical domain-prompt path and SHA-256;
- node-prompt root and revision;
- the exact node-prompt file set and every file's SHA-256.

`runner.build_context()` validates the complete bundle before constructing the provider
registry. `dendro prompt-info` reports the prompt hash, manifest hash, revisions and
`compatible` status; an incompatible bundle exits before any provider can be called.

A custom manifest is an operator attestation that the supplied natural-language prompt is
compatible with policy `0.2.3`. Hash validation proves byte identity only; it does not prove
semantic equivalence.

This file is generated. `dendro prompt-seal --write` renders it from one template, so it
is the command's output rather than something hand-maintained beside a hash computed
elsewhere. A contract test asserts the checked-in file is byte-identical to what the
generator produces.

## Re-sealing

Any prompt file that changes — the domain prompt at its own default path included — leaves
the manifest attesting bytes that no longer exist, and the next run fails closed with a hash
mismatch. `dendro prompt-seal` is the way back:

```bash
dendro prompt-seal            # dry run: prints every hash it would change, old -> new
dendro prompt-seal --write    # rewrite the configured manifest, then revalidate
```

The dry run exits `0` for an out-of-date manifest — being stale is the normal state after an
edit, not an error. It exits non-zero only when the manifest is missing, unreadable, or
bound to a policy revision this code does not support. The node-prompt file set is taken from
whatever is on disk under the configured root, so adding or deleting a node prompt is sealed
the same way as editing one.

**Re-sealing attests bytes, not semantic compatibility.** It never rewrites `schema_version`
or `policy_revision`: those stay pinned to what the code supports, and a manifest bound to a
different revision is refused rather than upgraded. Whether the changed prompt still means
what deterministic policy `0.2.3` expects is a review — the derivations listed in `AGENTS.md`
and the evaluation suite are how that question gets answered, not a SHA-256.
