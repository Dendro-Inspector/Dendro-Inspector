# Agent-provider quota latency review — 2026-08-31

- **Status:** Resolved and verified in the working tree
- **Owner:** Dendro Inspector maintainers
- **Date:** 2026-09-01

## Claim

The extreme factory latency was retry amplification after Claude Code exhausted an account
spend/session quota. Local Python execution was not the bottleneck.

## Evidence

- Median cold CLI import/startup was approximately `0.55 s`; a complete fake-provider
  inspection was approximately `0.75 s`.
- The ignored factory state contained `759` worker failure records. One planner request was
  attempted `104` times across approximately `158 minutes`.
- The upstream error reported an exhausted monthly spend limit and a later session reset, but
  `worker.py` classified any message containing `429` as a transient rate limit.
- The worker released the claim, slept `90 s`, and reclaimed the same request. The bridge
  watched only for an answer and waited up to `3,000 s` before returning `504`.
- Healthy uncached Opus answers still took `19-134 s` each. Observed seven-call graph critical
  paths were approximately `4.9-6.3 minutes`, with prompts between `30,529` and `133,349`
  characters. That baseline latency is separate from the quota defect.

## Resolution

[The worker](../../scripts/agent-provider/worker.py) now treats Claude account spend/session
exhaustion as terminal for the current request while keeping ordinary rate limits retryable.
It publishes a request-scoped terminal marker and excludes that request from subsequent
claims.

[The bridge](../../scripts/agent-provider/bridge.py) watches the marker alongside the answer
file and returns HTTP `424 Failed Dependency` on the next poll. The marker is cleared when a
new bridge request with that request id is written, so restarting the bridge or retrying after
quota reset does not inherit a stale terminal state.

[Regression coverage](../../tests/unit/test_agent_provider_worker.py) proves:

- spend/session quota is terminal;
- an ordinary `429` remains retryable;
- a terminal request leaves the worker queue; and
- the bridge surfaces terminal failure in under two seconds instead of waiting for timeout.

## Verification record

```text
.venv\Scripts\python.exe -m ruff format --check --no-cache --exclude .tmp .
158 files already formatted

.venv\Scripts\python.exe -m ruff check --no-cache --exclude .tmp .
All checks passed!

.venv\Scripts\python.exe -m mypy --no-incremental
Success: no issues found in 116 source files

.venv\Scripts\python.exe -m pytest -p no:cacheprovider
715 passed in 102.43s

.venv\Scripts\python.exe -m dendro_inspector.cli eval --suite public
Cases: 19   Passed: 19   Failed: 0
```

The ignored `.tmp/` directory is retained local test output with Windows permissions that
prevent repository traversal, so Ruff excluded that non-project path. Disabling pytest's cache
provider avoids writing another ignored cache and does not change test collection or behavior.

## Remaining performance work

This fix removes multi-hour failure amplification. It does not make a healthy uncached Opus
graph interactive: planner, evidence extraction and candidate generation are serial, the
three reviewers fan out concurrently, and arbitration adds another call. Reducing that
baseline requires a separate quality/cost decision about model routing, prompt size, session
reuse or graph topology; it is not part of this incident fix.

## Free OpenRouter vision route

Status: web-verified candidate; live botanical quality remains unverified.

The selected free route is `google/gemma-4-31b-it:free`. OpenRouter currently declares that
route free, able to accept image and text input, and able to return JSON through
`response_format`. It has a 262,144-token context window. The free endpoint does not enforce
the supplied JSON Schema, but the provider adapter validates every response against the
original Pydantic model and rejects invalid output. The repository already names this exact
route as the [OpenRouter default](../../src/dendro_inspector/providers/openrouter_adapter.py),
so selecting it needs configuration rather than another source-code change.

Sources checked on 2026-08-31:

- [Gemma 4 31B free route](https://openrouter.ai/google/gemma-4-31b-it%3Afree)
- [OpenRouter free-model limits](https://openrouter.ai/docs/faq)
- [Nemotron 3 Ultra free route](https://openrouter.ai/nvidia/nemotron-3-ultra-550b-a55b%3Afree)
- [Nemotron 3 Nano Omni free route](https://openrouter.ai/nvidia/nemotron-3-nano-omni-30b-a3b-reasoning%3Afree)

Rejected alternatives:

- `nvidia/nemotron-3-ultra-550b-a55b:free` is text-only and therefore cannot inspect a tree
  photograph.
- `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free` accepts images and is substantially
  faster in OpenRouter's published measurements, but it does not support `response_format`.
  Its free endpoint also warns that submitted content is logged to improve NVIDIA products;
  private golden photographs must not be sent to that trial endpoint.
- `openrouter/free` can choose an image-capable route dynamically, but that makes model
  identity and botanical behavior vary between evaluation runs.

OpenRouter documents a shared free-model allowance of 50 requests per day without purchased
credits, or 1,000 per day after purchasing at least $10 in credits. A Dendro inspection uses
multiple model requests, so the no-credit tier is suitable for evaluation, not production.
No live photograph was uploaded during this web-only selection. A labeled, privacy-approved
photo run is still required before making a claim about tree-identification accuracy.

## Live free-route verification

### OpenRouter

Live checks on 2026-08-31 found that catalog metadata alone did not prove routability:

- `google/gemma-4-31b-it:free` and the smaller free Gemma route reached Google AI Studio,
  but the shared upstream pool returned HTTP `429`.
- The free Mistral Small 3.2 catalog entry had no active endpoint.
- `openrouter/dots3-note-preview` accepted image 99 and the project contracts. Case
  `openrouter-dots3-photo-099-v7` completed seven nodes in `58.82 s`, with zero retries and
  no arbiter. It produced no usable evidence, so deterministic routing abstained and asked
  for a foliage close-up plus whole-crown photograph. The route expires on 2026-09-30 and
  is therefore not a suitable project default.

Photo 99 is marked `unlabelled` in the private manifest. The Dots3 run verifies transport
and contract compatibility, not identification accuracy.

### Google Gemini

Live `listModels` and generation checks on 2026-09-01 verified that the key in `.env`
authenticates and can access `gemini-3.6-flash`. Google lists that model's input and output
as free of charge on the free tier and documents structured-output support. Google also
states that free-tier submissions may be used to improve its products.

The first full graph exposed two independent adapter defects:

1. Google reset one HTTPS connection during the concurrent reviewer fan-out. On Windows,
   `urlopen` raised `ConnectionResetError` directly, outside the adapter's `URLError`
   handler, so the graph aborted instead of using its bounded retry budget.
2. Gemini then generated a `ReviewResult.subject_id` longer than the Pydantic 120-character
   bound on both structured-output attempts. Gemini's supported schema subset does not
   include `maxLength`, so native JSON mode could not enforce that constraint.

The adapter now retries peer resets within its existing bounded budget. Reviewer calls also
pass code-owned subject identifiers to the provider boundary, where Gemini's supported
`enum` constraint binds every reviewer `subject_id` to an exact orchestration-owned value.
Pydantic validation and deterministic review synthesis remain authoritative.

Final case `gemini-36-photo-099-v4` completed all 16 graph nodes in `78.94 s` using seven
`gemini-3.6-flash` calls. Every provider call validated on its first structured attempt,
there were zero graph retries, reviewer disagreement correctly invoked the arbiter, and the
deterministic claim cap reduced a proposed `Picea` identification to low-confidence
`Pinaceae` at family resolution.

Photo 99 remains unlabelled, and the shipped taxon knowledge is demonstration content.
Therefore tree-identification accuracy is still `UNKNOWN`; this run verifies the key,
multimodal transport, schemas, review fan-out, arbitration, and deterministic claim cap.
The local trace is intentionally untracked because it derives from a private photograph.

Sources checked on 2026-09-01:

- [Gemini 3.6 Flash pricing](https://ai.google.dev/gemini-api/docs/pricing)
- [Gemini 3.6 Flash model capabilities](https://ai.google.dev/gemini-api/docs/models/gemini-3.6-flash)
- [Gemini structured output](https://ai.google.dev/gemini-api/docs/structured-output)

Google now describes `generateContent` as legacy and its `responseSchema` field as
deprecated in favor of the Interactions API and newer JSON-schema surface. The current
adapter remains operational, but migration is separate future compatibility work rather
than part of this incident fix.
