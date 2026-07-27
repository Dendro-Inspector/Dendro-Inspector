---
name: Bug report
about: Something behaves incorrectly
labels: bug
---

## What happened

## What you expected

## Reproduction

```bash
dendro inspect --fake <scenario> ...
```

Please reproduce with `--fake` where possible. A run that needs your API key is one nobody
else can investigate.

## Output

Paste the relevant output. If you can, attach the trace:

```bash
dendro inspect ... --trace-out ./traces
```

Check the trace before attaching: it records your location if you supplied one.

## Environment

- Version / commit:
- Python:
- OS:
- Providers (`primary` / `arbiter`):
- Domain prompt: placeholder / custom

## Note

A wrong tree identification is usually **not** a bug in this project — the shipped knowledge
is a three-genus placeholder pack. A bug is: a species-level claim that escaped the card cap,
an answer more confident than the evidence supports, evidence leaking between subjects, a
crash, or the graph failing to terminate.
