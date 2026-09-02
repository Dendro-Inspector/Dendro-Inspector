# Model roles and escalation

- **Status:** Current
- **Owner:** Dendro Inspector maintainers
- **Date:** 2026-09-02
- **Last-verified:** 2026-09-02

Three logical roles. Business logic names only these; which vendor and model satisfies each is
configuration.

## `primary`

Plans, inspects the image, extracts evidence, and generates candidates. Needs strong
multimodal grounding and reliable structured output.

```bash
DENDRO_PRIMARY_PROVIDER=anthropic
DENDRO_PRIMARY_MODEL=claude-opus-5
```

## `reviewer`

Runs the botanical, confusion and confidence reviews concurrently. The three nodes are
independent tasks over the same evidence, but a gateway may satisfy them from a pool of
equivalent transports. Needs vision and reliable `ReviewResult` output.

```bash
DENDRO_REVIEWER_PROVIDER=openrouter
DENDRO_REVIEWER_MODEL=stealth/ox-alpha
```

For compatibility, an `AppConfig` constructed with only the released `primary` and
`arbiter` entries uses `primary` for review. `load_config()` always materializes all three
roles; when reviewer environment variables are omitted, it inherits the primary adapter and
model. The fallback lives only in `AppConfig.provider_for()`.

## `arbiter`

Independently challenges a disputed or high-risk result: identifies unsupported claims,
finds overlooked alternatives, assesses overconfidence, and recommends the highest
defensible taxonomic level.

```bash
DENDRO_ARBITER_PROVIDER=openai
DENDRO_ARBITER_MODEL=gpt-5.6-sol
```

**Bind the arbiter to a different model family than the primary.** Two instances of the same
model share failure modes, and a model that agrees with itself is not a second opinion — it
is the same opinion, billed twice.

## Ox factory profile

The local agent-provider bridge exposes `claude-main` for the primary role, backed by the
authenticated Claude Code `opus` alias. It exposes `ox-factory` for the reviewer role,
backed by OpenCode Zen, direct OpenRouter and the authenticated Cline gateway. The three
reviewer nodes arrive concurrently; each call is claimed by the first available Ox worker.
Those workers are transport alternatives for one Ox model family, not three independent
votes. Provider diversity improves availability; it does not create epistemic diversity.

Escalation uses the separate `sol-judge` route backed by Codex `gpt-5.6-sol`. The graph sees
the canonical `primary`, `reviewer` and `arbiter` roles, while deterministic synthesis and
final decision still own every admissibility and claim-inflation decision. Setup and verified
route details live in
[agent-as-provider.md](agent-as-provider.md), the canonical bridge workflow.

## Adapter matrix

| Adapter | Credential | Transport and structured-output dialect |
| --- | --- | --- |
| `openai` | `OPENAI_API_KEY` | Optional OpenAI SDK; native `json_schema` response format |
| `anthropic` | `ANTHROPIC_API_KEY` | Optional Anthropic SDK; Messages API with schema in the prompt and Pydantic validation |
| `gemini` | `GEMINI_API_KEY` | Direct HTTPS; native `responseSchema` after compatibility translation |
| `nvidia` | `NVIDIA_API_KEY` | Direct HTTPS; OpenAI-compatible chat-completions dialect |
| `openrouter` | `OPENROUTER_API_KEY` | Direct HTTPS; OpenAI-compatible chat-completions dialect |
| `ollama` | none | Local HTTP; Ollama schema format after compatibility translation |

All three logical roles accept any adapter in this table. The selected model must support image
input; a text-only model cannot serve even a reviewer because every model call receives the
case photographs. The `fake` adapter is reserved for deterministic tests and evaluations.

## Gemini

```bash
DENDRO_PRIMARY_PROVIDER=gemini
DENDRO_PRIMARY_MODEL=gemini-3.6-flash   # the adapter's default
```

Reads `GEMINI_API_KEY`, over plain HTTPS with no SDK. Structured output uses the API's
native `responseSchema`.

**Pro models are not on the free tier.** Verified 2026-07-27 against a free-tier key:
`gemini-3.1-pro-preview`, `gemini-3-pro-preview`, `gemini-2.5-pro` and `gemini-pro-latest`
all return `429` with `limit: 0` — a billing state, not a rate limit, so retrying never
clears it. `gemini-3.6-flash`, `gemini-3.5-flash` and `gemini-2.5-flash` serve requests.
Because a quota `429` is not a bad credential, the adapter reports it as a plain
`ProviderError`; only `401`/`403` accuse the key.

## NVIDIA NIM

```bash
DENDRO_PRIMARY_PROVIDER=nvidia
DENDRO_PRIMARY_MODEL=nvidia/nemotron-3-nano-omni-30b-a3b-reasoning   # the adapter's default
```

Reads `NVIDIA_API_KEY`. NIM speaks the OpenAI chat-completions dialect, so the adapter is
written against that dialect rather than the vendor: `NVIDIA_BASE_URL` repoints it at any
OpenAI-compatible server — a self-hosted NIM, vLLM — with no code change.

**The model must accept images.** Measured 2026-07-27 against `integrate.api.nvidia.com`
on a single photograph:

| Model | Image | `json_schema` |
|---|---|---|
| `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning` | yes | clean |
| `google/gemma-4-31b-it` | yes | clean |
| `meta/llama-3.2-90b-vision-instruct` | yes | clean |
| `nvidia/nemotron-nano-12b-v2-vl` | yes | valid, padded with trailing newlines |
| `meta/llama-3.2-11b-vision-instruct` | yes | ignored — returns prose |
| `nvidia/nemotron-3-super-120b-a12b` | **no** — `500 multimodal processing is not enabled` | reasoning prose |

A text-only model cannot serve any role here, because every node call carries the
photograph. The adapter turns that `500` into a named error rather than a generic failure.
Transient `500`/`503` from the shared endpoint are retried twice — an `EngineCore` 500 was
observed on a request that succeeded unchanged moments later.

The `omni` member of the `nemotron-3` family is the default because latency separated the
clean ones: on one photograph it answered in 3.9 s where `google/gemma-4-31b-it` and
`meta/llama-3.2-90b-vision-instruct` both exceeded 300 s on the shared endpoint. Its
text-only siblings — `nemotron-3-super-120b-a12b`, `nemotron-3-nano-30b-a3b` — cannot serve
any role here.

## Schema dialects

Neither Gemini nor Ollama accepts the JSON Schema Pydantic emits. `providers/schema_compat.py`
translates it per target, and the translation is never a relaxation — the response is always
validated by the original model, so a constraint dropped from the request is still enforced
on the answer.

| Target | Changed in the request | Why |
|---|---|---|
| Gemini | `$ref`/`$defs` inlined; `additionalProperties` and length bounds dropped; patterns normalized | `responseSchema` is an OpenAPI 3.0 subset |
| NVIDIA / OpenAI-compatible | patterns normalized | the server constrains decoding with its own grammar engine |
| Ollama | `pattern` dropped | llama.cpp's GBNF converter rejects an escaped hyphen in a character class — see below |

To check a translation against a host that refuses the untranslated form, without a vendor
account, see [agent-as-provider.md](agent-as-provider.md).

**Pattern normalization exists because of a silent failure, not a loud one.** Pydantic emits
`[a-z0-9_\-]`. Asked for a token matching it, Gemini returns `F1` — the schema is accepted
and the constraint ignored. Written `[a-z0-9_-]` the same request returns `f1`. Ollama
rejects the first form outright. `normalize_pattern` moves the hyphen to the end of the
class rather than merely unescaping it, because `[a\-z]` unescaped in place becomes the
range `a-z`; a test asserts the rewritten pattern matches exactly the same strings.

## Local models via Ollama

For offline or credential-free runs, any role can be bound to a locally hosted model
through the `ollama` adapter. It needs no API key and no SDK — it talks to a running
`ollama serve` over plain HTTP — but it is only as reliable as the local model's
structured-output following, which is generally weaker than the hosted frontier models
above. The adapter requests every call at `temperature: 0`.

The model **must be vision-capable**: every node call carries images, and a text-only
model ignores them silently rather than failing, which surfaces as a confidently wrong
identification rather than an error. The default is `gemma4:e4b` — multimodal, edge-sized,
128K context.

```bash
ollama pull gemma4:e4b
DENDRO_PRIMARY_PROVIDER=ollama
DENDRO_PRIMARY_MODEL=gemma4:e4b
```

Other multimodal options in the same range: `gemma4:e2b` (smaller), `gemma4:12b` (larger,
256K context), or the `qwen3-vl` line. Pick the `-instruct` variant over `-thinking` where
both exist — reasoning tokens fight the schema constraint.

`OLLAMA_HOST` overrides the default `http://localhost:11434` if the server listens
elsewhere.

### The grammar constraint

Ollama compiles the requested schema into a GBNF grammar before sampling, and its converter
accepts less than JSON Schema allows. Measured on Ollama 0.32.4 with `gemma4:e4b` and
`gemma3:4b`: a character class holding an escaped hyphen — `[a-z0-9_\-]`, exactly what
Pydantic emits for `Identifier` and `ValueToken` — is answered with
`400 failed to parse grammar`, while `[a-z0-9_-]` compiles. Small output caps hide it,
because the failure needs a generation budget above roughly 256 tokens to surface, so a
short smoke test passes and the real call does not.

`to_ollama_schema` therefore drops `pattern` outright rather than tracking which escapes
today's converter tolerates — upstream has a family of these, including PCRE shorthands and
large `maxLength` bounds. Structure, types, enums and required fields still constrain the
grammar; the regex is enforced where it always was, in validation.

The practical consequence is that a local model is free to answer `feature: "Log Data
Presence"` where the contract wants `bark.peeling`, and it will — that exact response came
back from `gemma4:e4b`. Validation rejects it and the repair retry returns the error to the
model. Budget for that: local runs spend more attempts per node than hosted ones.

## What the arbiter receives

Original images, original user context, the evidence packet, the candidate set, the stored
deterministic provisional verdict, and the relevant taxon and comparison cards. The
escalation gate computes that verdict before deciding to call the arbiter; the projection
fails closed if it is absent.

**It never receives the primary model's private reasoning.** This is structural, not a
policy: the system stores no hidden chain-of-thought anywhere, so there is nothing to pass
on. What it cannot see, it cannot be anchored by.

## What the arbiter can and cannot do

It returns **structured findings only**. It cannot write the answer.

To change the ranking it must supply `recommended_candidates` **in the same `ReviewResult`**
as a finding with `required_action: rerank_candidates`, for the same unambiguous subject. The
ranking passes the shared candidate validator, and `proposed_taxon`, when present, must survive.
A recommendation without that exact admitted finding changes nothing; a finding without a
validated recommendation changes nothing.

Synthesis stores the accepted finding and validated ranking together as `AdmittedRerank`.
Final decision consumes only that artifact, never raw recommendations. One unambiguous arbiter
rerank takes precedence over internal reranks; conflicting arbiter rankings preserve the
current order rather than choosing arbitrarily.

Its findings then go through `adjudicate()` — the *same* function the internal reviewers
face, with deterministic findings adjudicated first. A second model does not get a lower bar
because it is expensive or because it disagreed confidently. See
[`docs/review-pipeline.md`](review-pipeline.md).

## Escalation policy

Fully configurable via `EscalationPolicy`. Every trigger is individually switchable, so an
operator can tune cost against risk without editing code, and so evaluation can measure
escalation precision and recall.

### Triggers

| Trigger | Hard? | Why |
| --- | --- | --- |
| `species_level_proposed` | yes | The claim most likely to be wrong and most likely to be believed |
| `possible_multiple_taxa` | yes | Averaging two subjects into one answer is a silent, plausible error |
| `user_challenged_result` | yes | The user has information the system does not |
| `instruction_like_content_detected` | yes | Untrusted content in play; a second look is cheap |
| `unresolved_contradiction` | yes | A critical finding survived adjudication |
| `high_confidence_proposed` | no | A reviewer recommends high or the deterministic provisional verdict is high |
| `leading_candidates_close` | no | The ranking is doing work the evidence may not support |
| `reviewer_disagreement` | no | Reviewer recommendations or admitted reranks conflict |
| `critical_finding` | no | An accepted reviewer finding has critical severity |
| `escalation_provenance_unknown` | no | Compatibility fallback for an external/legacy synthesis that set only the combined flag |
| `bark_colour_dependence` | no | The single most common overweighted feature |
| `bark_only_input` | no | Structurally the weakest input class |
| `forced_by_configuration` | yes | Explicit operator override |

### Suppressors

**Blocking** — a second opinion could not help; these override everything:

* `evidence_insufficient` — arbitrating "I cannot tell" yields "I cannot tell", at twice
  the price.
* `already_abstaining`.

**Cost** — these trade risk for money and are overridden by any hard trigger:

* `broad_and_low_risk` — a clean genus-or-broader result across all subjects, with no
  high-confidence provisional verdict.
* `clean_review_and_modest_confidence` — no accepted findings and no high-confidence
  provisional verdict.

### Precedence

```text
policy disabled        -> no
blocking suppressor    -> no
hard trigger           -> YES        (cost suppressors cannot override this)
cost suppressor        -> no
any remaining trigger  -> yes
```

The hard-trigger tier exists because of a specific failure: a two-log photograph produced a
clean, broad, cheap-looking result, and `broad_and_low_risk` suppressed the escalation that
the mixed-taxa flag had correctly requested. A cost suppressor must never talk the gate out
of a safety trigger. Regression-tested in
`tests/unit/test_escalation_gate.py::TestPrecedence`.

## Cost

The arbiter roughly doubles model cost on escalated cases. The public conformance suite is
deliberately weighted toward hard cases, so its escalation rate is not a production cost
forecast. v0.2.3 expanded the suite from sixteen to nineteen cases; v0.9.0 adds the
high-confidence silent-reviewer escalation case, bringing the current suite to twenty. Tune
with:

```python
EscalationPolicy(
    on_close_leading_candidates=False,  # the most frequent soft trigger
    on_bark_colour_dependence=False,  # if your inputs are mostly bark
)
```

Measure the effect with `dendro eval --suite public`: `escalation_precision`,
`escalation_recall` and `unnecessary_arbiter_call_rate` are all reported.

Do not disable the hard triggers to save money. They exist for the cases where a wrong
answer is both most likely and most costly.

## Adding a provider

1. implement the `ModelProvider` Protocol in `providers/` (lazy-import the SDK);
2. add a value to the `Adapter` enum;
3. add a branch to `providers/registry.py:build_provider`.

Nodes need no change. `assert_never` in the registry's match statement makes a forgotten
branch a type error rather than a runtime surprise.

## Implementation references

- [`src/dendro_inspector/config.py`](../src/dendro_inspector/config.py) — `Role`, `EscalationPolicy`
- [`src/dendro_inspector/nodes/escalation_gate.py`](../src/dendro_inspector/nodes/escalation_gate.py)
- [`src/dendro_inspector/providers/`](../src/dendro_inspector/providers)
- [`prompts/nodes/arbiter.md`](../prompts/nodes/arbiter.md)
