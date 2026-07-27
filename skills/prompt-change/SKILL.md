---
name: prompt-change
description: Edit the domain prompt or any node prompt and re-seal the manifest. Use when changing prompts/domain/system-prompt.md or prompts/nodes/*.md, when a run fails with a prompt hash mismatch, when asked to run prompt-seal, or when deciding whether a prompt edit moves the deterministic policy revision.
---

# Changing a prompt

**Status:** Draft — written from the implementation, not yet exercised on a real task.
Correct it in place when it misleads you.

Prompts here are **hash-attested artifacts**, not editable text. `prompts/versions.yaml`
pins schema version, deterministic policy revision, the domain prompt path and SHA-256, the
node-prompt root and revision, and the exact node-prompt file set with per-file hashes.
`runner.build_context()` validates the whole bundle before any provider is constructed, and
composition uses the cached validated bytes — so a prompt changed after validation cannot
enter a request.

Consequence: **every prompt edit is a two-part change.** Edit, then re-seal. A prompt edit
alone leaves the tree in a state where nothing runs.

## The loop

```bash
dendro prompt-seal            # dry run: shows every hash that would move
dendro prompt-seal --write    # rewrite the manifest
dendro prompt-info            # confirm compatibility_status: compatible
```

Re-sealing rewrites **hashes only**. `schema_version` and `policy_revision` are pinned by
the code that reads the file and are never rewritten by the seal command — that is
deliberate, and working around it is not a fix.

## Hashes are pinned in tests too

Re-sealing updates the manifest, not the test suite. Three test assertions hard-code the
domain prompt SHA-256 and must move with it:

- `tests/contract/test_domain_prompt_contract.py` — two occurrences
- `tests/integration/test_cli.py` — one occurrence

Grep the old hash before assuming you have them all.

## Do not "fix" the domain prompt's line endings

`.gitattributes` ends with `prompts/domain/system-prompt.md -text -diff` as the **last**
matching rule, deliberately overriding `*.md text eol=lf`. The file's SHA-256 appears in
every execution trace; normalising its line endings would change that hash without changing
a word, silently invalidating every prior trace.

Two practical effects:

- `git grep -I` **skips this file** — git classifies it as binary. Searching it with the
  default flags will report clean when it is not. Use `git grep -a`, or read it directly.
- Any script that rewrites it must preserve bytes exactly. In Python that means
  `open(path, newline="")` on both read and write.

Verify after editing: the staged blob, the file on disk, and the manifest must agree.

## Does the policy revision move?

`policy_revision` describes what the **deterministic layer** expects, not what the prompt
says. Re-word a section, fix a typo, drop a name — the hash moves, the revision does not.

It moves when the prompt's rules and the code's rules would otherwise disagree: a new
evidence tier, a changed ceiling, a rule the deterministic layer now enforces or stops
enforcing. When it moves it moves everywhere at once — package version, graph version,
policy revision, manifest and baseline are asserted to be one value by
[`tests/contract/test_version_contract.py`](../../tests/contract/test_version_contract.py).

Re-sealing attests **bytes, not meaning**. The manifest records that the prompt on disk is
the prompt that was reviewed. It cannot tell you the new wording still means what the
deterministic policy expects. That review is the owner's.

## The prompt is user-managed

The domain prompt is an opaque, user-managed artifact — the repository does not own its
content. Deployments override it via `DENDRO_DOMAIN_PROMPT_PATH` with a matching manifest at
`DENDRO_PROMPT_MANIFEST_PATH`. A prompt carrying
`<!-- DENDRO-DOMAIN-PROMPT-PLACEHOLDER -->` on its first line is recognised as a placeholder.

Reference: [`docs/architecture.md`](../../docs/architecture.md#domain-prompt-handling) and
[`prompts/README.md`](../../prompts/README.md).
