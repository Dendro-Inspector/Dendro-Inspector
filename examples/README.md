# Examples

Put your own photographs here. This directory is git-ignored for image files — see
[`docs/dataset-policy.md`](../docs/dataset-policy.md) for why.

## Fake mode does not need a real file

```bash
evil-duck inspect --fake primary-pass --image examples/log.jpg
```

The fixture supplies the evidence, so the path need not exist; a missing file is recorded as
a limitation rather than crashing the run. That is what makes the demo work on a fresh clone.

## With a real provider

```bash
cp ~/Pictures/some-log.jpg examples/log.jpg
export EVIL_DUCK_PRIMARY_PROVIDER=openai
export OPENAI_API_KEY=...
evil-duck inspect --image examples/log.jpg --location "Kyiv Oblast, Ukraine" --object-type log
```

## What makes a photograph worth sending

The system will tell you, in part 5 of every answer. In general: foliage beats bark, a scale
reference beats none, even shade beats direct sun, and two photographs of one subject beat
one photograph of two.
