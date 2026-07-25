## What changed

## Why

## How it was verified

```bash
ruff format --check .
ruff check .
mypy
pytest
evil-duck eval --suite public
```

Paste anything notable — a new evaluation case, a metric that moved, a case that would have
gone red before this change.

## What is not covered

Be specific. "Nothing" is rarely true.

## Checklist

- [ ] All five gates pass locally
- [ ] No new dependency (or: justified in the description and agreed in an issue)
- [ ] No AI co-author trailer in any commit
- [ ] No photographs, binaries, secrets or internal references
- [ ] Behaviour change is covered by a test or an evaluation case
- [ ] Docs updated if a rule or contract changed
- [ ] `prompts/domain/system-prompt.md` handling unchanged (it is a user artifact)
