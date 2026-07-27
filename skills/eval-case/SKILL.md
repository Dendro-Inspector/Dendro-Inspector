---
name: eval-case
description: Add or change a case in the public conformance suite (evals/public/). Use when asked to add an evaluation case, reproduce a failure class as a test, script a fake-provider scenario, or when a change moves the frozen baseline. Covers fixture authoring, the expect block, and what re-freezing a baseline does and does not permit.
---

# Adding a public conformance case

**Status:** Draft — written from the implementation, not yet exercised on a real task.
Correct it in place when it misleads you.

`evals/public/` is a **conformance and regression suite over synthetic fixtures**, not an
accuracy benchmark. Adding a case for a newly-understood failure class is expected and is
not overfitting. `evals/golden/` is the opposite and is governed by
[`AGENTS.md` §16](../../AGENTS.md#16-benchmark-governance) — if the task involves golden
material, stop and read that first.

Procedure and the case catalogue live in
[`docs/evaluation.md`](../../docs/evaluation.md#adding-a-case). This skill covers what that
document assumes you already know.

## Two files, always

A case is a **fixture** (`evals/fixtures/<name>.json`, the scripted model output) plus a
**declaration** (`evals/public/<name>.yaml`, the input and the expectations). The
declaration's `scenario` field names the fixture.

## The fixture is a script, and unscripted keys fail loudly

Keys are `role:node`. The fake provider raises `UnscriptedCallError` rather than improvising
— a fake that quietly invents data is worse than no fake.

This makes the fixture a **statement about how far the graph gets**. Only script the keys
the case actually reaches:

- Stops at the evidence-quality gate → script `primary:planner` and
  `primary:evidence_extractor` only.
- Reaches candidates → add `primary:candidate_generator` and the three reviewer keys.
- Escalates → add `arbiter:arbiter`.

If you script a key the case never reaches, nothing tells you. If you omit one it does
reach, the run fails with the missing key named. Prefer the second failure.

## Extractor output is validated strictly

`primary:evidence_extractor` is parsed as `GeneratedEvidencePacket`, not `EvidencePacket`.
The generated contract is stricter than the persisted one: a wood-family observation
(`wood`, `cut`, `rings`, `pores`, `rays`, `resin`, `heartwood`, `sapwood`, `inner_bark`)
**must** state `wood_surface` explicitly, and a non-wood observation must omit it. Stored
packets may leave it unset and parse as `unknown`; freshly generated output may not.

Feature values are constrained tokens, not prose — `^[a-z0-9][a-z0-9_.\-]*$`. Write
`diffuse_porous`, never `"diffuse porous"`.

See [`src/dendro_inspector/schemas/evidence.py`](../../src/dendro_inspector/schemas/evidence.py).

## A case that asserts nothing is worse than no case

A contract test enforces that every case declares at least one expectation
(`tests/contract/test_data_contract.py`). Beyond that gate, assert the thing the case
exists to prove. `expected_status: insufficient_evidence` on an abstention case says
nothing unless `expected_evidence_tier` also pins *why* it abstained — tier 1 (nothing above
context survived) and tier 3 (bark-capped, still not enough) are different failures.

## Run it

```bash
dendro eval --suite public --verbose
```

## The baseline is not a formality

`evals/baselines/public-v<version>.json` is compared by
[`tests/evaluation/test_baseline.py`](../../tests/evaluation/test_baseline.py) against a
**live suite run** — metrics directionally, per-case decisions exactly. Adding a case makes
that test fail until the baseline is re-frozen. That failure is the feature.

Before re-freezing, check what moved. A new case appearing is expected. **An existing case's
decision changing is not**, and re-freezing without explaining it in `CHANGELOG.md` destroys
the only record that it happened. Adding a case and silently absorbing a regression in
another one is the specific failure this baseline exists to catch.

Re-freeze with the command in the `test_baseline.py` docstring, then trim to that file's
shape.
