# Using a coding agent as the model provider

- **Status:** Current
- **Owner:** Dendro Inspector maintainers
- **Date:** 2026-07-28
- **Last-verified:** 2026-07-28

`scripts/agent-provider/bridge.py` is a local HTTP server that speaks the three wire dialects
this project's adapters talk, and answers nothing by itself. It writes each request to disk —
prompt, JSON schema, and the decoded image bytes — and blocks until an answer file appears.
Whoever drives the session writes that file: a coding agent with vision (Claude Code, or any
agent that can read an image and write JSON), or you with an editor.

The result is a real multimodal model, reached over a real socket, in the vendor's real wire
format, without a vendor account.

## What this is for, and what it is not

`providers/fake.py` replays a fixture and is what every gate runs on. It is the right tool for
almost everything, and it stays that way. This bridge covers the one thing a fixture cannot:
**does an adapter carry a live model's answer about a real photograph all the way through the
graph** — image encoding, schema translation, envelope extraction, validation, repair retry,
escalation, arbitration.

It is not a test of any vendor: no vendor is contacted. It is not an accuracy measurement
either — one photograph answered by one model is an anecdote, and §16 of `AGENTS.md` still
forbids tuning cards, thresholds or prompts against material in `evals/golden/`. Use it to
find out whether the machinery works, then say `n = 1` out loud.

## The loop

Two shells. In the first:

```bash
python scripts/agent-provider/bridge.py            # --port 8799, state in .bridge/
```

In the second:

```powershell
.\scripts\agent-provider\Invoke-BridgeInspect.ps1 -Dialect gemini `
    -Image evals\golden\your-photo.jpg -ObjectType log -Location "Kyiv Oblast, Ukraine"
```

The launcher binds both roles to one adapter and repoints that adapter's base URL at the
bridge, so a working vendor key sitting in `.env` cannot be used by accident. The credential it
exports is a placeholder — the bridge only checks the adapter put one in the right place.

Then, for each call, the bridge prints the file it is waiting for and you answer it:

| File | What it holds |
| --- | --- |
| `.bridge/pending/req-NNN-prompt.txt` | the full prompt, ending in the node brief and the case context |
| `.bridge/pending/req-NNN-schema.json` | the schema **as the host received it**, after translation |
| `.bridge/pending/req-NNN-meta.json` | dialect, response model, image fingerprints, and the answer path |
| `.bridge/images/req-NNN-imgN.jpg` | the photograph as the adapter transmitted it — open this one |
| `.bridge/answers/<key>.json` | **you write this**: the JSON object the schema asks for |

Write the answer file and the run continues. A malformed answer is not a problem: the contract
rejects it, `request_structured` appends the validation error to the prompt, and the next
pending request is the repair attempt with the error in it. Raise
`-StructuredRetries` if you want more than one repair.

One inspection is seven calls — planner, evidence extractor, candidate generator, three
reviewers, and the arbiter if the escalation gate fires. The three reviewers arrive
concurrently, so three answer files can be written at once.

### Answers are cached, so a second dialect costs nothing

The cache key is a hash of the prompt, the schema's top-level field names, and the SHA-256 of
every photograph served. The prompt already contains every upstream node's output, so an
identical prompt means an identical position in an identical run — and replaying it in another
dialect is the same model answering the same question at temperature 0. Any real divergence
upstream changes the prompt and falls through to a fresh request.

The photographs are hashed in because the prompt does **not** contain them: case context names
each image by id and media type only. Two different photographs inspected with the same season,
object type and locale therefore produce a byte-identical planner prompt. Keyed on the prompt
alone — as it was until 2026-07-28 — the second run silently received the answers authored while
looking at the first run's picture, which is a wrong result wearing the costume of a fast one.

A replay of a run that needed a repair retry will not complete from cache. The repair prompt
embeds the adapter name — `gemini returned unparseable output …` becomes `nvidia returned …` —
so every repair round misses and blocks for a fresh answer. Only clean runs replay end to end.

In practice: author the answers once through `gemini` or `ollama`, then re-run the same
photograph through `nvidia` and `openrouter`, which finish in seconds and exercise the other
dialect's schema translation and envelope. To re-author a call, move its file out of
`.bridge/cache/` and `.bridge/answers/`.

### Pick the dialect by its timeout

| Dialect | Per-call timeout | Good for |
| --- | --- | --- |
| `gemini` | `GEMINI_TIMEOUT_SECONDS`, raised to 3600 by the launcher | authoring answers live |
| `ollama` | `OLLAMA_TIMEOUT_SECONDS`, raised to 3600 by the launcher | authoring answers live, no credential at all |
| `anthropic` | `ANTHROPIC_TIMEOUT_SECONDS`, raised to 3600 by the launcher | authoring answers live |
| `nvidia`, `openrouter` | 300 s, fixed — the registry does not pass the constructor argument | replaying cached answers |

The `anthropic` dialect needs the optional extra: `pip install '.[anthropic]'`. It is the only
dialect whose adapter is an SDK rather than a raw `urlopen`, so two behaviours differ. The SDK
retries a timed-out request twice on its own, which against a socket waiting for a human means
three pending requests for one answer file — the raised timeout, not a retry setting, is what
prevents that, because the SDK reads no environment variable for retry count. And the base URL
is redirected through `ANTHROPIC_BASE_URL`, which the SDK reads itself, so `-Fault` cannot be
expressed on this dialect: the SDK appends `/v1/messages` to the base URL and a path prefix
would not survive.

## The bridge is a strict vendor

Each dialect rejects the schema constructs the real host rejects, with that host's own status
code and error shape, so `providers/schema_compat.py` is exercised rather than trusted:

| Dialect | Rejects |
| --- | --- |
| Gemini | `$ref`, `$defs`, `additionalProperties`, `const`, `allOf`, `oneOf`, `not` — `400 INVALID_ARGUMENT` |
| Ollama | a character class holding `\-` — `400 failed to parse grammar` |
| OpenAI-compatible | the same escaped hyphen — `400`, guided-decoding grammar failed to compile |
| Anthropic | nothing about the schema; a missing `anthropic-version` or `max_tokens` — `400` |
| Gemini, OpenAI-compatible, Anthropic | a missing or empty credential in the header that dialect uses |

Anthropic rejects no schema construct, and that is the point of including it. The other three
hand the schema to a constrained decoder, so a construct the decoder cannot compile is a
request-time error. `providers/anthropic_adapter.py` appends the raw Pydantic schema to the
prompt as prose, so nothing is compiled and nothing is rejected: `$ref`, `$defs`, `const` and
the escaped hyphen all travel intact. Two consequences follow. That path never passes through
`providers/schema_compat.py`, so constraints the other dialects lose — `maxLength` among them —
survive, and the model resolves `$defs` itself. And every constraint is enforced only by
Pydantic after the fact, which makes the repair retry this dialect's sole line of defence.

The forbidden sets are written out in `bridge.py` rather than imported from `schema_compat`, on
purpose: importing them would make the check agree with the code by construction and prove
nothing.

Confirm the checks still bite before trusting a green run:

```bash
python scripts/agent-provider/probe_dialects.py
```

It sends the untranslated Pydantic schema for two real node contracts to the same endpoints and
prints what each host did. Every line should be an HTTP 400, and the translated counts beside
them should be zero. Nothing in the probe needs an answer file.

## Injecting failures

Adapter error paths are otherwise reachable only with a patched `urlopen`. Prefix the base URL
path with `/fault/<name>` — the launcher's `-Fault` parameter does it — and the adapter under
test is unmodified:

| Fault | Exercises |
| --- | --- |
| `unauthorized` | 401/403 mapped to `ProviderUnavailableError` naming the credential variable |
| `quota` | Gemini `429` with `limit: 0` — must fail at once, never sleep |
| `rate-limit` | Gemini `429` with a `retryDelay` the adapter is supposed to honour |
| `multimodal` | NIM's `500` for a text-only model handed an image, mapped to a named error |
| `flaky` | one transient `500`, then success — the retry classification |
| `truncated` | a well-formed envelope with no content, and the finish reason in the message |
| `fenced` | a ```` ```json ```` fence around the object, which `_strip_fence` should absorb |
| `garbage` | prose where an object belongs — `StructuredOutputError` and the repair retry |

The first five answer with an HTTP error and are raised before any answer is looked up. The last
three damage a valid answer on its way out, and fall back to `{}` when the cache has nothing —
a repair retry carries a different prompt, so it would otherwise block waiting for an answer
nobody is going to write. **These routes have not themselves been exercised end to end yet**:
the workflow below the fault layer has, and the adapter behaviour each fault targets is covered
by `tests/integration/test_provider_boundary.py` with a patched `urlopen`.

## Safety

- Nothing leaves the machine. Every request goes to `127.0.0.1`, and the launcher overrides the
  base URL of the adapter it selects.
- `.bridge/` is git-ignored and holds **decoded copies of every photograph** the run served,
  along with prompts and answers. Treat it like `evals/golden/` — see
  [dataset-policy.md](dataset-policy.md) — and do not commit it.
- The bridge is a test harness with no authentication. It binds to loopback only; keep it that
  way.

## Findings from the first run, 2026-07-28

Recorded so the next run is not misread as a broken harness. One photograph of a freshly sawn
log — bark at the rim, rough transverse face — answered live by Claude Opus 5 through the
`gemini` adapter, then replayed through `nvidia` and `ollama`. Twenty-one accepted calls, seven
per dialect, no validation failures, no repair retries, and the same 4.9 MB image arriving with
an identical SHA-256 in all three dialects.

The system answered `unknown` at `low` confidence, which is correct behaviour for what the
photograph proves, but it arrived there for a reason worth knowing:

- **Every candidate was dropped on vocabulary near-misses.** The extractor prompt supplies
  feature *families* and free-form example values, never the value vocabulary the taxon cards
  key on. The model wrote `bark.texture: scaly_plated` and `wood.tone: pale_yellow_cream`; the
  `pinus` card matches `scaly_plates` and `light_yellow_honey`. `candidate_validation_filtered`
  rejected every candidate, and both the reviewers and the arbiter received empty candidate
  sets.
- **The arbiter prompt promises inputs the node does not pass.** It says it receives the
  proposed resolution and confidence; the assembled prompt carries case context, evidence
  packet, candidate sets and cards only.
- **The `weak_photo` response under-reports.** It printed `Evidence: none recorded` and
  `What remains uncertain: none recorded` while the trace held 25 observations, 4 inferences and
  5 context limitations.

None of the three is a defect in the provider adapters, and none was changed in response to this
run.
