# Node: Response composer

> **v0.1 status.** Response composition is currently **deterministic code**
> (`src/evil_duck_dendro/nodes/response_composer.py`), not a model call. This file is the
> contract that composition follows, and the prompt a model-backed composer would be given
> if one is introduced later. It is kept here so the rule set has one home rather than
> living only inside a formatter.

Turn the final decisions into the factual answer. The Evil Duck tone layer runs *after*
this and may not change anything you produce here.

## Structured result

One `StructuredFinalResult` per subject:

`verdict`, `subject`, `taxonomic_resolution`, `confidence`, `supporting_evidence`,
`strongest_contradiction`, `nearest_alternative`, `limitations`, `best_next_photo`.

## Human-readable structure — exactly five parts, in order

1. **Verdict.**
2. **Strongest evidence.**
3. **Strongest alternative.**
4. **What remains uncertain.**
5. **What photograph would most improve the result.**

## Rules

- State the taxonomic level explicitly every time. "Pine" is ambiguous; "Pinus, at genus
  level" is not.
- Never present a probable result as certain. The status word carries real meaning:
  `identified`, `probable`, `insufficient_evidence`, `conflicting_evidence`,
  `unsupported_user_claim`.
- Name the nearest alternative even when the leading candidate is comfortable. A reader who
  cannot see what was ruled out cannot judge the answer.
- The photograph request must be specific and achievable — `needle_fascicle_macro`, not
  "a better photo".
- When the knowledge cards backing the answer are marked as placeholder content, say so.
- No invented evidence. Everything cited must appear in the evidence packet.
