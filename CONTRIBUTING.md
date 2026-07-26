# Contributing

Thanks for looking. This project is small, opinionated and gate-heavy on purpose — the gates
are impersonal, which is what lets us say no to code without saying no to a person.

## Setup

```bash
git clone <your-fork> && cd evil-duck-dendro-inspector
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest
```

No API key is needed for anything in this repository. Every test and every evaluation case
runs against the fake provider.

## The gates

Run these before opening a pull request. CI runs the same set.

```bash
ruff format --check .              # formatting
ruff check .                       # lint
mypy                               # strict typing on src, relaxed on tests
pytest                             # unit, contract, integration, evaluation
evil-duck eval --suite public      # the public conformance suite
```

All five gates must pass. A red evaluation case is a real signal, not flakiness — the suite
is fully deterministic. `docs/evaluation.md` describes the cases and what each one holds.

## What we look for

**Match the surrounding code.** Frozen contracts, typed node signatures, pure functions
where a pure function will do.

**Keep the determinism boundary.** A model proposes; code adjudicates. If your change makes a
model responsible for whether evidence suffices, whether a finding is admissible, whether to
escalate, or what the final confidence is, it will be sent back. See
[`docs/architecture.md`](docs/architecture.md#determinism-boundary).

**No new dependency without a reason.** Adding one needs maintainer agreement, a stated
justification, a licence check and a look at its transitive footprint. Fifteen lines of code
usually beats a package.

**Never weaken a claim cap to make a test pass.** If a case wants a species-level answer, the
question is whether the taxon card should support species — not whether the cap should be
loosened.

## Commits

- `type(scope): imperative summary` — `feat`, `fix`, `docs`, `chore`, `refactor`, `test`.
- One logical change per commit, independently reviewable and revertable.
- **No AI co-author trailers.** No `Co-Authored-By: Claude`, no
  `Co-authored-by: Copilot <...>`, none of the variants. Commits are authored by the person
  submitting them.
- **No internal references.** No employer or client names, internal hostnames, ticket ids,
  private wiki links. If an outside reader cannot follow the reference, it does not belong.
- Write for strangers. "Fix the thing we discussed" is not a commit message.
- Never commit photographs, binaries, generated output or secrets. See
  [`docs/dataset-policy.md`](docs/dataset-policy.md).

## Pull requests

Say what changed and why, how you verified it, and what you did not cover. If you changed
behaviour, an evaluation case or a test should show the difference.

For anything structural — a new node, a contract change, a new provider — open an issue
first. It is cheaper to disagree about an approach than about an implementation.

## Common contributions

### Adding a taxon

1. `knowledge/taxa/<taxon-id>.yaml`, keeping `placeholder_content: true`;
2. add it to the relevant comparison card, and keep `common_confusions` symmetric — a
   contract test checks that if Pinus lists Picea, Picea lists Pinus;
3. `pytest tests/contract/test_data_contract.py`.

No code change is needed. If it seems to need one, that is worth an issue.

### Adding an evaluation case

See [`docs/evaluation.md`](docs/evaluation.md#adding-a-case). A case that asserts nothing
cannot fail; a contract test rejects one that declares no expectations.

### Adding a node

See [`docs/agent-graph.md`](docs/agent-graph.md#adding-a-node). The contract tests catch
every step you forget.

### Tuning prompts

`prompts/nodes/*.md` are project-owned — change them when a node needs different
instructions. `prompts/versions.yaml` pins every node prompt's SHA-256 and validation is
fail-closed, so an edit is not finished until the manifest attests the new bytes:

```bash
evil-duck prompt-seal            # dry run: every hash it would change, old -> new
evil-duck prompt-seal --write    # rewrite the manifest, then revalidate
```

Commit the re-sealed manifest with the prompt change. Re-sealing attests bytes, not semantic
compatibility — it never rewrites `schema_version` or `policy_revision`.

`prompts/domain/system-prompt.md` is **not** project-owned. It is a user-managed artifact,
and this codebase does not translate, shorten, reformat or improve it. Replacing it is the
owner's call and is re-sealed the same way; the handling of it may not change.

## Code of conduct

By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).

## Licence

Contributions are licensed under [Apache-2.0](LICENSE). By submitting a pull request you
confirm you have the right to submit the work under that licence.
