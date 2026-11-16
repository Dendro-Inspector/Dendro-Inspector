# Human review of a case

- **Status:** Current
- **Owner:** Dendro Inspector maintainers
- **Date:** 2026-11-16
- **Last-verified:** 2026-11-16

A specialist who looks at the same photographs can confirm an answer, correct it, or say
the photographs do not decide it. `dendro review-export` turns a stored answer into a
package for that person, and `dendro review-import` records what they answered as a
revision of that one case. The workflow is manual: nothing is sent anywhere, no model is
called, and nothing a reviewer writes reaches the knowledge base on its own.

**Before a package leaves your machine**, decide whether you may share those photographs
with that person. They are as sensitive as the originals ([dataset policy](dataset-policy.md)),
and a package is the originals, byte for byte.

## Export

```bash
dendro inspect --image bark.jpg --image leaf.jpg --json > answer.json
dendro review-export answer.json --image bark.jpg --image leaf.jpg --out review-oak
```

The stored answer is either the `dendro inspect --json` output or a batch case record
(`<run>/cases/<case>.json`, with the photographs from `<run>/inputs/`). Give the
photographs in the order `dendro inspect` received them: the answer calls them `img-1`,
`img-2` and so on. The export refuses:

- photographs the answer cites but you did not give, or a case record that lists a
  different number of photographs;
- a failed case, which holds no answer to review;
- a photograph whose metadata carries GPS coordinates, or whose metadata cannot be read
  (always the case without the `images` extra). `--allow-location` includes them as they
  are; stripping the metadata first is better;
- a folder that already exists with anything in it.

The folder holds:

| File | Contains |
| --- | --- |
| `photos/photo-N.*` | The photographs, unchanged |
| `package.json` | The photographs' SHA-256, the answer per subject (verdict, level, status, confidence band, evidence, limitations, nearest alternative, contradiction, hypothesis), the answer text, the questions, and the engine commit, graph and domain prompt version, and knowledge and settings digests the answer came from |
| `README.txt` | Instructions for the reviewer, the answer text and the questions |
| `answers.template.json` | The response to fill in |

`package_id` is derived from the content of `package.json`. A package edited after export,
or a photograph changed in it, no longer imports.

The trace itself is not in the package; only the provenance fields above are. The answer
text is, and it repeats a location the user typed.

## The questions

Code derives them from the stored answer, in this order, and each lists the answers it
accepts:

| Asked when | Question | Answers |
| --- | --- | --- |
| Several subjects and none designated | Which subject were the photographs taken to identify? | `subject`, `cannot_tell` |
| The subject's answer names a taxon | Is it that taxon at that level? The nearest alternative is named when there was one | `agree`, `disagree`, `name`, `cannot_tell` |
| It names nothing | What is it, at the finest level these photographs decide? | `name`, `cannot_tell` |
| A name was held back as an [unconfirmed hypothesis](review-pipeline.md#the-name-a-review-held-back) | Is it that name? | `agree`, `disagree`, `name`, `cannot_tell` |
| Review recorded a strongest contradiction | Is that observation right for these photographs? | `agree`, `disagree`, `cannot_tell` |

`name` gives `taxon` (lowercase Latin, `quercus` or `quercus_robur`) and `resolution`;
`subject` gives `subject_id`. Any answer may list the photographs that decided it and a
note of up to 1,000 characters. A question left `null` is skipped.

## Import

```bash
dendro review-import review-oak answers.json
```

The answers are untrusted input. The import refuses a file over 256 KiB, an unknown field,
answers for another package, a question the package does not ask, an answer that question
does not accept, a subject or photograph the package does not have, control characters in
text, an impossible date, a response that answers nothing, and the same response twice.
Nothing in a response is used as a path or a command.

An accepted response is appended to `review-oak/revisions.jsonl` as the case's next
revision, with the reviewer (initials or role, qualification, date), the response's SHA-256
and, per subject, what the answer named and what the review made of it:

| Outcome | Meaning |
| --- | --- |
| `confirmed` | The reviewer agrees, or gives the same taxon |
| `refined` | The reviewer gives a narrower taxon within it (`quercus` → `quercus_robur`) |
| `corrected` | The reviewer gives a different taxon |
| `rejected` | The name is wrong and the reviewer gives no other |
| `named` | The answer named nothing and the reviewer gives a name |
| `undecided` | These photographs do not decide it |
| `unanswered` | The question was skipped |

A hypothesis gets the same outcome vocabulary, and a contradiction is recorded as upheld or
rejected. A second reviewer's response becomes the next revision; earlier ones are never
rewritten, and the command reports where the reviews agree.

## What a revision does not do

It changes nothing outside the package folder. A correction to one case never edits a taxon
card, a prompt, a policy or another case. A reviewer's answer can still improve the
knowledge base, through the ordinary card review with its provenance, or label a held-out
unit: copy the reviewer, qualification, date and genus into that unit's `reviews` in the
[held-out manifest](evaluation.md#the-held-out-set). Both are a person's decision.

## Implementation references

- [`src/dendro_inspector/human_review.py`](../src/dendro_inspector/human_review.py)
- [`tests/unit/test_human_review.py`](../tests/unit/test_human_review.py)
