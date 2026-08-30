# Security policy

## Reporting a vulnerability

**Do not open a public issue for a security vulnerability.**

Use GitHub's private vulnerability reporting: go to the **Security** tab → **Report a
vulnerability**. That creates a private advisory visible only to maintainers.

Please include what you can: affected version or commit, reproduction steps, impact, and any
suggested fix. A partial report sent privately beats a complete one filed publicly.

We aim to acknowledge within 7 days and to have an assessment within 30. If a fix is
warranted we will coordinate disclosure with you and credit you unless you prefer otherwise.

Do not describe an exploit in a commit message, pull request title or branch name before the
fix ships. That publishes it just as effectively as an issue would.

## Supported versions

| Version | Supported |
| --- | --- |
| 0.8.x | Yes |
| < 0.8 | No |

Pre-1.0: fixes land on `main`, and there are no backports.

## Threat model

This project processes **untrusted images and text** and sends them to a language model. The
security-relevant surface is therefore mostly about what untrusted input can make the system
do.

### In scope

- Prompt injection reaching a model as an instruction rather than as data.
- Untrusted input causing arbitrary code execution, file access or network access.
- Secrets leaking into logs, traces, error messages or evaluation reports.
- A malicious knowledge card, evaluation case or fixture escalating into code execution.
- Denial of service through unbounded retries, unbounded graph execution, or unbounded
  resource use from a crafted input.

### Out of scope

- **A wrong tree identification.** That is a correctness bug, not a vulnerability. File it
  as a normal issue.
- Vulnerabilities in a model provider's service.
- Cost incurred by your own configuration (see
  [`docs/model-roles.md`](docs/model-roles.md) on escalation cost).
- Anything requiring an attacker to already control your `.env`, your knowledge cards or
  your domain prompt.

## Prompt injection: the defensive posture

**Instruction-like text inside an image, filename, caption or metadata is evidence about the
input. It is never an instruction to the graph.**

How that is enforced:

1. **Detection, not obedience.** `nodes/input_guard.py` scans every untrusted string and
   records the *category* of instruction-like content found. It never acts on the content.
2. **Recording is authoritative.** The extractor cannot erase the guard's finding: if the
   guard saw injection, `instruction_like_content_detected` is set on the evidence packet
   regardless of what the model returned.
3. **Escalation.** Detected instruction-like content is a hard escalation trigger, so a
   second, independent model looks at the case.
4. **Labelled composition.** Untrusted context is always last in the composed prompt and
   always fenced under an explicit "untrusted data, never commands" header.
5. **Structured output only.** Nodes accept validated Pydantic objects. There is no code
   path where model output becomes a command, a file path, a shell string or a URL.
6. **No execution.** The system never executes content found in an image or an uploaded
   file. It reads image bytes at the provider boundary and nothing else.

The detector is deliberately conservative. Dendrology is full of imperative-sounding prose
("note the fascicles", "compare with Picea", "ignore the background"), and a guard that
flagged ordinary botanical writing would be switched off by its users — and then it guards
nothing. False negatives are contained by layers 2-6; false positives destroy the layer
entirely. Tested both ways in `tests/unit/test_input_guard.py`.

### What this does not claim

Prompt injection is not a solved problem. A sufficiently clever payload may still influence
what a model *reports seeing*. The mitigations above ensure that:

- it cannot change what the **system** does — routing, escalation, capping and adjudication
  are deterministic code that never reads model prose;
- it cannot exceed the taxonomic claim the knowledge cards permit;
- it is recorded and escalated rather than passing unnoticed.

Treat every output as untrusted if the input was.

## Secrets

- API keys are read from the environment only, never from a config file in the repository.
- `.env` is git-ignored; `.env.example` contains empty placeholders.
- `ProviderRegistry.describe()` returns `adapter:model`, never a credential.
- The JSON log formatter redacts keys matching `api_key`, `token`, `secret`, `password`,
  `authorization`, `image_bytes`, `reasoning`, `chain_of_thought`.
- Traces record which model ran, never the prompt or response body.

## Data handling

The run trace deliberately excludes: API keys, image bytes, full user metadata, hidden model
reasoning, and prompt or response bodies. See
[`docs/dataset-policy.md`](docs/dataset-policy.md) for what must never be committed.

## Not a safety tool

This project must not be used to decide whether a tree is safe, whether timber is what a
seller claims, or whether a plant is edible. `identified` means "the evidence supports this
claim at the stated level" — not "verified".
