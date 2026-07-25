---
name: Feature request
about: Propose a change
labels: enhancement
---

## Problem

What does not work today? Concrete situation, not a wish.

## Proposal

## Which layer does this touch

- [ ] Contracts (`schemas/`)
- [ ] Knowledge (`knowledge/` — usually a YAML change, no code)
- [ ] Graph or nodes
- [ ] Providers
- [ ] Evaluation
- [ ] Docs

## Determinism check

Does this put a model in charge of any of the following?

- whether evidence is sufficient
- whether a finding is admissible
- whether to escalate
- the final resolution or confidence
- what the user is told

If yes, say why that is better than deciding it in code. See
`docs/architecture.md#determinism-boundary`.

## How would it be evaluated

Which evaluation case would go red if this regressed?
