# Golden evaluation set

**This directory is git-ignored except for this file.**

`evals/public/` holds cases anyone can run: synthetic inputs replaying recorded fixtures, no
photographs, no personal data. It is the suite CI runs.

This directory is for the material you cannot publish — real photographs with known answers,
which is what actually measures whether the system identifies trees rather than merely
handling evidence correctly.

## Why it is not committed

- Photographs are usually someone else's copyright.
- Image files carry EXIF: GPS coordinates, timestamps, camera serials.
- Binaries in git history are permanent.

See [`docs/dataset-policy.md`](../../docs/dataset-policy.md).

## Using it

Same format as `evals/public/`. Point `input.images[].path` at local files:

```bash
evil-duck eval --suite golden
```

With real providers configured this makes live, billable calls. Unlike the public suite, it
is not deterministic — model output varies between runs, so treat a single result as a
sample rather than a measurement.

## What to measure here

`overconfidence_rate` first. Accuracy that comes with overconfidence is not an improvement;
it is the failure mode wearing a better score.

Then `abstention_quality`: on photographs that genuinely cannot support an identification,
does the system abstain *and* ask for the right next photograph?
