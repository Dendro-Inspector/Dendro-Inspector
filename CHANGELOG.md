# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Entries record what a **user** observes. Internal refactors that change no behaviour do not
get entries.

## [Unreleased]

### Fixed

- Bark-only tree assessments in frames that may contain multiple taxa now request a declared
  attachment photograph before a leaf-surface macro, so the next image establishes which tree
  owns the foliage before its morphology can affect the verdict. Unknown results also omit the
  empty "nearest alternatives: none recorded" block when no alternative was structurally
  ruled out.
- Codex-backed provider runs no longer exhaust their output allowance while constrained by
  regex patterns inside structured-output arrays. Regex validation remains enforced by the
  canonical Pydantic contract before any model answer is admitted.
- A contradiction can no longer reject the user's own version, mark a verdict as
  conflicting or be printed as the strongest contradiction unless the contradicting
  observation could itself have supported an identification: same subject, attached where
  attachment matters, and above bark level. Weaker contradictions are still recorded as
  findings and can still lower confidence.
- The user's own version is now read as they wrote it. A claim naming more than one taxon is
  a disjunction, ruled on by its most favourable member, so hedging between two trees is no
  longer resolved to whichever card happened to be listed first — which could return a
  rejection to a user who had named the right tree. A taxon named only to deny it ("не дуб")
  no longer becomes the claim being ruled on, and names shorter than four characters match
  whole words only, so a one-letter claim no longer matches most of the catalogue.
- An abstained run now says so in the structured result and in the limitations it prints,
  and its resolution is one level broader than the same evidence would otherwise have
  earned. Previously abstention could return the identical taxon, resolution and status with
  only the confidence lowered.

### Changed

- A candidate's support strength is now adjudicated against its own taxon card from the
  evidence that survived admission, and the model's label can only lower it, never raise it.
  Confidence and the `identified` status therefore follow visible features rather than the
  primary model's self-assessment. No frozen public case moved: every recorded fixture's
  scripted strength already matched its scripted support, so the deterministic policy
  revision stays at `0.9.0` and no deployment needs to re-seal.
- The package, graph, deterministic policy and public baseline move together to `0.9.0`.
  The escalation gate now computes and stores the deterministic verdict the graph would
  return before it decides whether to call the arbiter. High-confidence provisional verdicts
  therefore reach arbitration even when every internal reviewer passes silently; the
  broad/low-risk cost suppressor applies only below high confidence. Prompt bytes and model
  routing are unchanged.
- Frozen decision movement: `apple-with-fruit-001.arbiter_used` changes from `false` to
  `true` under F2. Its selected taxon, status, resolution and confidence do not move. The new
  `silent-reviewers-high-confidence-001` case locks the same path directly. Two further
  cases, `unattached-contradiction-claim-001` and `abstention-visible-001`, lock the
  contradiction-authority and abstention rules above; both are new, so neither moves a frozen
  decision, `strong-label-thin-support-001` locks the adjudicated strength above and
  `disjunctive-user-claim-001` locks the hedged claim. The public suite stands at twenty-four
  cases with zero overconfidence.

### Added

- Run traces record how each verdict was composed: every resolution bound considered and
  the one that bound, every confidence step applied or skipped, and where a rerank came
  from. They also record when a user's claim named a taxon only to deny it, since the
  verdict then rules on no version at all.
- Run traces retain the per-subject provisional verdicts shown to the arbiter and record
  whether arbitration changed status, taxon, resolution or confidence. Runs that did not
  call the arbiter leave those change fields empty rather than reporting a false comparison.
- Run traces record what each model call spent: input, cached-input and output tokens, and
  the cost the provider itself reported. Nothing is estimated from a price table, and a
  provider that reports nothing leaves the fields empty rather than zero, because silence
  and zero are different facts. Counts cover every attempt a call made, matching the
  duration recorded beside them, so a repaired call does not understate what it used.
- Run traces record `critical_path_ms`: the wall time no amount of concurrency could
  remove, being every serial node plus the slowest member of each reviewer fan-out. Read
  against the run's total duration it says what running the three reviewers together
  actually bought.
- `scripts/bench/` summarises those traces and, separately, local agent-bridge state:
  per-node latency, prompt size, token accounting, escalation reasons and arbiter verdict
  changes per trigger. Both scripts read local files only, need no credentials, and make no
  model calls.
- `OPENROUTER_DATA_COLLECTION` selects whether OpenRouter may route a request to an upstream
  that logs submitted content. It defaults to `deny`, so photographs are not sent to a
  training-data endpoint unless an operator opts in, and an unrecognised value is refused
  rather than guessed. OpenRouter requests additionally require parameter-compatible
  routing, so an upstream that silently drops the structured-output request is not selected.

### Fixed

- Latency and cost tables now print `n/a` when a provider did not report a metric. They no
  longer render missing cached-token or cost data as a measured zero.
- The Gemini adapter retries a peer connection reset within its existing bounded budget. On
  Windows an HTTPS reset can escape `urlopen` outside the handler the adapter watched, which
  aborted an otherwise healthy graph during the concurrent reviewer fan-out.
- Reviewer calls now carry the code-owned subject identifiers to the provider boundary, and
  an adapter whose schema dialect supports enums binds every returned `subject_id` to one of
  them. Gemini's supported schema subset has no length bound, so it returned a `subject_id`
  past the contract's limit and failed both structured attempts. Validation and review
  synthesis remain the authoritative boundaries; this only stops a provider from spending
  its attempts on an identifier the graph could never accept.
- The OpenRouter default model asks for its schema in the prompt tail with a plain JSON
  object response, because that free endpoint accepts a `json_schema` request without
  enforcing it. Every response is still validated against the original contract, and a
  model configured explicitly keeps the native `json_schema` request.
- The local agent-provider factory now distinguishes a short Claude rate limit from an
  exhausted account spend or session quota. Exhausted quota fails the current bridge request
  promptly instead of re-running the same CLI job every 90 seconds until the bridge's
  3,000-second timeout; transient rate limits retain their cooldown and retry behavior.
- The source distribution now names its inputs instead of sweeping the working directory.
  A local build previously packaged any untracked file sitting in the tree, so working
  notes could reach a published artifact that cannot be unpublished. Build assets, draft
  articles and the golden corpus are excluded, and `log.md` is ignored outright.

## [0.8.0] — 2026-08-30

The package, graph, deterministic policy and public baseline move together to `0.8.0` because
what a reviewer model is allowed to cite is now part of the deterministic contract. Prompt
bytes and model routing are unchanged, and every baseline metric and per-case decision is
identical to `0.7.0`.

### Changed

- Reviewer model calls no longer receive the graph state. Orchestration builds an explicit
  projection for each reviewer, and the returned result is bound by code to the evidence ids
  that projection carried, so a model finding can cite only what the model was shown. A
  provider that supplies its own scope has it overwritten. Case photographs and evidence
  remain pass-through for now: candidate generation is under review and must not decide which
  subjects a reviewer may inspect.
- Rejected findings distinguish an invented evidence id from a cross-subject citation again.
  Citations are resolved before they are scope-checked; the reverse order reported every
  unknown id as out-of-scope and made the two failures indistinguishable in a trace.
- A review result carrying no recorded scope is treated as unscoped rather than empty-scoped.
  A rerank recommendation attached to such a result previously lost all of its supporting
  evidence and was rejected with no reason code that explained why.
- Withheld knowledge-card classes render as `null` rather than an empty list, so a model
  cannot read "you were not shown any" as "none exist".

### Added

- Run traces record the bounded input each reviewer received: reviewer, evidence ids,
  transmitted image ids, candidate subjects, taxon ids and knowledge-card selection. The
  image ids are the photographs that actually reached the provider, not the ones the case
  declared, so an unreadable file cannot appear in a trace as evidence a reviewer saw.

## [0.7.0] — 2026-08-24

The package, graph, deterministic policy and public baseline move together to `0.7.0`
because attachment authority can now change the returned scientific claim. Prompt bytes and
model routing are unchanged.

### Fixed

- Decision-critical detachable evidence is now tested counterfactually. If demoting one
  uncorroborated model-authored `confirmed_attached` observation changes status, taxon,
  resolution or confidence, the conservative outcome wins. The inverse, telemetry-only
  counterfactual records when confirming an `unknown` observation cited by a model proposal
  would change the verdict, without permitting it to strengthen the returned claim. A
  deterministic component-to-root projection remains valid independent corroboration, and
  direct photographs of detached organs remain scoped to the photographed object.
- Next-photo planning now asks for a continuous attachment view before a morphology macro
  when foliage or reproductive evidence ownership is unresolved or decision-critical.
- Escalation traces now distinguish reviewer disagreement from accepted critical findings;
  legacy/custom synthesis that supplies only the former combined boolean remains actionable
  but is labelled with unknown provenance instead of inventing a disagreement. The gate's
  trigger and suppressor behavior is unchanged.
- The private photo-ledger runner forces UTF-8 mode in its Python child and decodes stdout
  strictly, so an encoding mismatch fails the run instead of persisting replacement
  characters as a successful JSON result. Local agent-provider CLI workers use the same
  strict subprocess contract.

### Added

- Run traces record evidence-authority sensitivity, critical evidence ids, the alternate
  attachment state and outcome, correction-loop retry count, and whether correction changed
  status, taxon, resolution or confidence. Model proposals are retained only as internal
  graph state so both sides of the deterministic authority counterfactual can be evaluated
  without another provider call.
- Out-of-vocabulary evidence telemetry now separates intentionally weak colour/insufficient
  features from potential knowledge-card gaps, and distinguishes missing feature paths from
  unknown values on known paths.
- The local Codex worker records token usage exposed by `codex exec --json`. The private
  ledger runner can bind to one provider state directory and aggregate measured upstream
  tokens and provider-reported cost per immutable run; it does not estimate prices when the
  upstream reports no cost.

## [0.6.0] — 2026-08-24

The deterministic policy, graph, package and public baseline move together to `0.6.0`.
Unlike the earlier experiment branch, two runs can no longer claim the same policy identity
while executing different requirement or next-photo semantics. Traces additionally record
the Git commit and dirty state when that identity is available.

### Fixed

- Anatomical components visibly belonging to one tree or material sample can now declare a
  parent identity and are deterministically folded into that root before candidate admission.
  Attached shoots and upper/lower bark zones no longer become contradictory independent tree
  identifications, while neighbouring branches and separate pile pieces remain isolated.
  Canonical observations and run traces retain the original component id for auditability.
- Bark-only abstentions now request neutral attached foliage or reproductive evidence instead
  of assuming the subject is a conifer.
- A taxon card's `required_for_high_confidence` entries are read as an expression —
  canonical feature paths or feature families joined by `_and_` and `_or_`, `_and_` binding
  tighter — and eight cards that named features nothing could observe were migrated onto real
  paths. Results no longer quote an observation as supporting evidence and report the
  requirement it satisfies as missing in the same breath: a white-barked trunk stops asking
  for `bark_pattern_or_leaf` while citing `bark.pattern`. Four cards had requirements no
  evidence could ever satisfy (`fruit_present`, `leaf_underside_and_arrangement`) and so could
  never reach high confidence however complete the photograph; a contract test now fails on
  any requirement selector that matches no declared feature. Verdicts are unchanged where the
  evidence hierarchy already capped them — bark still caps at genus and low confidence.
- The requested next photograph is now chosen against what the subject has already resolved
  instead of being the first entry of a declared list. Comparison cards bind each decisive
  difference to the photograph that would resolve it, while the cards' explicit photo order
  remains authoritative. A feature counts as resolved only when its observed value belongs
  to a relevant candidate card; an unknown but visible value cannot suppress the photograph
  needed to interpret it. Single-candidate fallback requests and real comparison requests now
  give different, truthful reasons. The Betula/Populus card no longer labels their shared
  alternate leaf arrangement as a discriminator. A photograph with no declared binding is
  still offered — unknown information value is not zero.

### Added

- Run traces now carry `code_commit_sha` and `code_dirty` when executed from a Git checkout.
  A clean commit is an immutable experiment build identity; a dirty run is marked as such
  instead of silently borrowing its parent commit's provenance.

- A separate `reviewer` model role now owns the concurrent botanical, confusion and
  confidence review fan-out. Configure it with `DENDRO_REVIEWER_PROVIDER` and
  `DENDRO_REVIEWER_MODEL`; environment-loaded configurations inherit the primary binding
  when those variables are omitted, and directly constructed legacy two-role configs keep
  the same compatibility fallback in `AppConfig.provider_for()`.
- The Dendro-owned loopback bridge factory now has one explicit three-role profile: Claude
  Code on `claude-main` for primary analysis, the OpenCode/OpenRouter/Cline Ox pool on
  `ox-factory` for concurrent review, and Codex Sol on `sol-judge` when the deterministic
  escalation gate calls the arbiter. Route-specific cache keys and provenance prevent one
  role's answer from being replayed as another's.

## [0.5.0] — 2026-07-29

Four defects in how reviewer findings compose into a verdict, all found by one live
photograph of a standing tree and none of them caught by the conformance suite, which returns
byte-identical decisions before and after. The evaluation baseline is re-frozen as
`evals/baselines/public-v0.5.0.json` with every case and metric unchanged from v0.4.0.

### Changed

- `FinalDecision.strongest_support` (a single line) is now
  `FinalDecision.supporting_evidence` (an ordered tuple). Consumers reading the JSON result
  see every validated supporting observation where they previously saw the first one.
- The deterministic policy revision moves to `0.5.0`, because the composition rules above are
  part of it. A deployment pinning its own prompt manifest must re-seal it
  (`dendro prompt-seal --write`); a manifest still attesting `0.4.0` is rejected rather than
  silently accepted against different policy.

### Fixed

- A reviewer's `recommended_resolution` and `recommended_confidence` now act as a floor for
  that model's own findings, not only as a ceiling. Reviewers who agreed on "genus is the
  highest defensible level" and filed a `lower_resolution` finding to say so had the finding
  applied on top of the genus they asked for, and the answer came back at family; three
  reviewers writing up one overclaim each cost a full confidence step, so a claim every
  reviewer recommended at `high` was reported at `low`. Deterministic findings still apply
  past the recommendation — a model cannot waive the code's own guardrails by recommending a
  comfortable number.
- Foliage that could not be traced to the analysed trunk no longer reports the whole
  identification as unsupported when other foliage *was* traced. It is recorded as a minor,
  no-material-change finding instead of becoming the subject's headline contradiction, so
  honestly marking a peripheral observation `unknown` no longer contradicts the evidence tier
  the verdict was computed from.
- The nearest alternative now looks past a candidate that collapsed into the verdict. Two
  species of one genus resolve to the same identity at genus level, which previously reported
  "no alternative recorded" while the look-alike findings were naming one.
- A verdict now lists every validated supporting observation instead of the first one. The
  fallback for an abstaining subject already listed up to eight, so an identified subject
  standing beside an insufficient-evidence one displayed *less* evidence than its neighbour —
  the better-supported answer read as the weaker one. `FinalDecision.strongest_support`
  (single line) is now `FinalDecision.supporting_evidence` (ordered tuple).

## [0.4.0] — 2026-07-28

The first public release expands the live provider boundary and makes real-photo runs
measurable, cheaper and safe from cross-image cache contamination.

### Added

- A local agent-as-provider bridge now drives all six adapters through their real wire
  dialects while an agent supplies the structured multimodal answer. It records decoded
  image digests, schemas and pending requests, supports deterministic replay and fault
  injection, and documents the complete workflow in `docs/agent-as-provider.md`.
- The bridge now speaks Anthropic's Messages API dialect in addition to OpenAI-compatible,
  Gemini and Ollama envelopes. This exercises `x-api-key`, `anthropic-version`,
  `max_tokens`, base64 image decoding and prompt-tail schema recovery without weakening
  the schema accepted by Pydantic.
- `ANTHROPIC_TIMEOUT_SECONDS` overrides the SDK's 120-second default for long agent-driven
  answers. Raising the timeout prevents one logical request from expiring and producing
  two automatic retries while a human or agent is still preparing the answer.

- `gemini` provider adapter, selectable for either role. Reads `GEMINI_API_KEY`, talks to the
  Generative Language API over plain HTTPS with no SDK and no new dependency, and requests
  structured output natively via `responseSchema`. Defaults to `gemini-3.6-flash`.
  Pro-tier models are not reachable on a free-tier key — they answer `429` with `limit: 0`,
  which no amount of retrying clears. A rate-limit `429` is different and is retried up to
  three times, waiting the delay the API itself reports: a free-tier per-minute cap is easy
  to trip when one inspection calls seven nodes. The two are told apart by `limit: 0`, so a
  billing state fails immediately instead of sleeping first.
- `nvidia` provider adapter for NVIDIA NIM, reading `NVIDIA_API_KEY`. It is written against
  the OpenAI chat-completions dialect rather than the vendor, so `NVIDIA_BASE_URL` repoints
  it at any OpenAI-compatible server. Defaults to
  `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning`, the multimodal member of that family. A
  model that cannot accept images is reported as such by name, because every node call
  carries the photograph and the server's own error for it is an opaque `500`.
- `openrouter` provider adapter, sharing the OpenAI-compatible implementation with `nvidia`;
  the two differ only in base URL, credential and default model. Reads `OPENROUTER_API_KEY`.
- `DENDRO_STRUCTURED_RETRIES` sets how many times a malformed structured response is handed
  back to the model with its validation error. Still 1 by default. A hosted frontier model
  satisfies these contracts first time; a smaller one frequently needs two or three.
- `.env` is now loaded at startup. `README.md` and `.env.example` had told the reader to
  create it since the first release; nothing read it back, so provider settings placed there
  were silently ignored. An exported variable still wins over the file.

- `ollama` provider adapter, selectable for either role via `DENDRO_PRIMARY_PROVIDER` or
  `DENDRO_ARBITER_PROVIDER`. It runs against a local `ollama serve` over plain HTTP: no API
  key, no SDK, no new dependency. `OLLAMA_HOST` overrides the default
  `http://localhost:11434`, and `DENDRO_*_MODEL` selects the model — it defaults to
  `gemma4:e4b` and **must be vision-capable**, because every node call carries images and a
  text-only model ignores them rather than failing. Calls are made at `temperature: 0`. A
  local model's schema-following is weaker than the hosted models', so malformed output —
  and the one repair retry that follows it — is the expected failure mode rather than an
  exceptional one.

- `GraphConfig.image_max_edge_px` bounds the longest edge of the photograph actually
  transmitted, defaulting to 1568 px. Every node in a case sends the same image, so an
  unbounded original is uploaded once per node: a nine-photograph run moved 349 MB for 47 MB
  of distinct images. On that run's photographs the bound cuts the transfer 7.6× with no
  change to what any node is allowed to see, since vision models downsample server-side
  anyway. Set it to `None` to send originals. Resizing needs the new `images` extra
  (`pip install dendro-inspector[images]`); without Pillow the original is sent and a
  warning names the missing extra once. EXIF orientation is applied to the pixels before
  resizing, since re-encoding drops the tag: a phone stores a portrait photograph as
  landscape plus a rotation flag, and discarding that flag without applying it hands the
  model a tree lying on its side. Images already inside the bound *and* already upright are
  passed through untouched rather than re-encoded, and formats other than JPEG and PNG are
  never rewritten.
- The domain prompt is now offered to adapters as a cacheable prompt prefix. It frames all
  seven calls in a case, so on one measured case it was 138,318 of 360,078 prompt characters
  — 38% — re-sent unchanged, and the photograph sits inside the same prefix. The Anthropic
  adapter marks a breakpoint there; adapters whose providers cache automatically, or not at
  all, ignore the advisory boundary. The text sent to the model is byte-identical either
  way, and a contract test binds the reported boundary to what `compose` actually emits so
  the two cannot drift into caching a prefix no later call reproduces.
- The evidence-quality report now records `unmatchable_evidence_ids`: trusted observations
  that no knowledge card can match on both feature and value. They are extracted, carried
  through the packet, shown to every reviewer, and then dropped at the admission boundary,
  where "the model saw nothing useful" and "the cards describe nothing the model saw" had
  been indistinguishable. A warning additionally names the features absent from every card.
  On the first live nine-photograph run this was 30% of all extracted observations, led by
  `bark.colour`, which no card mentions at all.

### Changed

- Package metadata, graph traces, deterministic prompt policy and the frozen public
  baseline now share release identity `0.4.0`. All nineteen decisions and every metric are
  identical to v0.3.0.

- The README now presents the executable graph as the central idea. Its Mermaid diagram and
  the detailed graph document are contract-tested against the declaration the executor
  walks, so public architecture diagrams cannot silently drift from runtime routing.

- Section 14 of the domain prompt (БАЗОВІ ПОРОДИ) now names the canonical evidence token
  beside each feature it describes — `біла паперова кора;` is followed by
  `→ bark.pattern = white_papery_with_black_marks`. Candidate validation matches cards by
  exact `(feature, value)` equality, so the extractor previously had to translate Ukrainian
  prose into English snake_case unaided, and 30% of one live run's trusted observations
  landed outside the vocabulary and were discarded. 110 lines carry 97 tokens; all 100
  `(feature, value)` pairs are verified against the knowledge cards by a contract test, and
  a second test enforces that the annotation is additive — the owner's prose is untouched,
  every token sits on its own line. Six card tokens remain unannotated because section 14
  has no line describing them; four of those belong to `larix`, which the prompt has never
  named. **The prompt hash changed, so any deployment pinning the old manifest must
  re-seal** (`dendro prompt-seal --write`).

### Fixed

- Agent-bridge cache keys now include every transmitted image's content digest. Two
  different photographs with the same context and schema can no longer share an answer
  authored while viewing the first image. Repair-round replays remain deliberately
  uncacheable.

- Provider calls made by the three concurrent reviewers are now recorded against the
  reviewer that made them. They share one recorder, and the trace attached every pending
  call to whichever node was written first, so every trace reported
  `botanical_reviewer calls=3` beside `confusion_reviewer calls=0` and
  `confidence_reviewer calls=0` — each with several minutes of wall time next to a call
  count of zero. Per-node cost and latency read off a trace were wrong for any run that
  reached review. A call whose node never records an event is now reported rather than
  silently discarded.
- Evidence extraction now receives a taxon-neutral vocabulary of the exact feature values
  used by the knowledge cards. Visible evidence can therefore survive strict candidate
  validation without relying on a model to guess spellings such as `scaly_plates`; the
  extractor is still told to preserve honest out-of-vocabulary observations when no listed
  token fits.
- Arbiter calls now include the deterministic pre-arbitration taxon, resolution, confidence,
  confidence band and status that would otherwise become final. This makes the runtime input
  match the arbiter prompt without exposing or inventing hidden model reasoning.
- Weak-photo results with no surviving candidate now report the subject's recorded visible
  observations and the evidence packet's context and image limitations. They no longer print
  `none recorded` when the trace contains usable observations or explicit uncertainty.
- Each adapter now reads its own credential variable. `api_key_env` was pinned per *role* —
  `primary` to `OPENAI_API_KEY`, `arbiter` to `ANTHROPIC_API_KEY` — so binding a role to any
  other vendor reported a missing credential for a key that was set.
- Regex patterns are normalized before being sent to any provider that constrains decoding.
  Pydantic writes a hyphen inside a character class as `\-`, which Gemini **accepts and then
  silently ignores** — asked for a value matching `^[a-z0-9][a-z0-9_\-]*$` it answers `F1`,
  and answers `f1` once the class is written `[a-z0-9_-]`. The identifier and value-token
  contracts were therefore unenforced on every structured call. The rewrite moves the hyphen
  to the end of the class rather than unescaping it in place, since `[a\-z]` would otherwise
  become the range `a-z`.
- The Ollama adapter no longer sends `pattern` constraints. Ollama compiles the schema into a
  GBNF grammar, and llama.cpp's converter rejects a character class containing an escaped
  hyphen (`[a-z0-9_\-]`), which is what Pydantic emits for this project's identifier and
  value-token types; every image-bearing call failed with `400 failed to parse grammar`.
  Patterns are still enforced when the response is validated.

- The distribution now ships a PEP 561 `py.typed` marker and advertises Apache-2.0 in its
  package classifiers, matching the inline type hints and repository license.

## [0.3.0] — 2026-07-27

The project no longer ships under a mascot name. Identification behaviour is unchanged — the
19-case public baseline records the same decision for every case — but every name a user
types moved, which is what makes this a major-for-0.x release rather than a patch.

### Changed — breaking

- The installed package is `dendro-inspector` and the import path is `dendro_inspector`.
- The CLI command is `dendro`, replacing `evil-duck`.
- Environment variables use the `DENDRO_` prefix, replacing `EVIL_DUCK_`. The old names are
  not read and no fallback is provided; a deployment that sets them silently gets defaults.
- Tone profiles are `standard` (default) and `direct`, replacing `evil_duck_public` and
  `evil_duck`. Their profile files moved to `prompts/personality/standard.md` and
  `prompts/personality/direct.md`.
- The domain prompt placeholder marker is `<!-- DENDRO-DOMAIN-PROMPT-PLACEHOLDER -->`.
- The domain prompt and the response-composer node prompt were reworded to drop the mascot,
  so `prompts/versions.yaml` records new hashes. Deployments pinning the previous domain
  prompt hash must re-run `dendro prompt-seal` against their own copy.
- Deterministic policy revision, graph version and package version are `0.3.0`. No
  identification rule changed — the wording that moved never had authority over a verdict —
  but the names in every trace, manifest and import moved together, and a trace is only
  useful if its version string tells you which vocabulary produced it.
- The frozen baseline is `evals/baselines/public-v0.3.0.json`. It was regenerated from a live
  run rather than copied: all 19 cases record the same decision as `public-v0.2.3.json`, and
  every metric is identical. `public-v0.2.1` through `v0.2.3` are retained as history.

## [0.2.3] — 2026-07-27

Firewood evidence now records the physical wood surface, rejects unsupported anatomy and
colour-only guesses deterministically, and preserves the domain prompt's qualified pile-level
identification path without turning one field case into botanical knowledge.

### Changed

- Wood/cut observations carry `prepared_end_grain`, `rough_end_grain`, `split_face`,
  `planed_face` or `unknown`. Legacy packets without the field parse as `unknown`; newly
  generated extractor output must answer explicitly. Pores, rays, vessels and resin canals
  require prepared transverse end grain, while coarse rings and visible resin remain usable at
  a conservative cap.
- Colour and tone are always bark-capped and cannot admit a candidate without exact non-colour
  evidence above context. Feature vocabulary remains exact: `.color`, `.colour` and `.tone`
  are classified as colour but never rewritten into one another for card matching.
- Declared split firewood deterministically forces mixed-taxa scope and requests a clean end
  grain plus surrounding bark from one labelled piece. `material_group` subjects are not
  blanket-rejected: corroborated pile-level evidence can still support a conservative result.
- Package, graph trace, deterministic prompt policy and the public baseline now share release
  identity `0.2.3`; node prompts are revision `0.2.0`.

### Added

- Three public conformance cases: rough-end-grain anatomy fails closed, split-face colour-only
  firewood abstains with a targeted request, and a corroborated Pinus log pile remains
  candidate-bearing. All nineteen cases pass with zero overconfidence, and the original sixteen
  decisions have no drift from `public-v0.2.2`.
- Duplicate `ImageLimitation.image_id` values now fail schema validation, removing tuple-order
  dependence from evidence authority.
- First piece of scientific benchmark governance, landed before any real photograph exists —
  a rule against overfitting is worthless if it arrives after the first tempting failure.

- `AGENTS.md` §16 **Benchmark Governance**: golden cases are immutable evaluation assets.
  Cards, prompts, thresholds and routing rules must not be tuned against an individual case.
  A benchmark failure may reveal a defect; it may not by itself define the fix. Changes
  motivated by one carry a `change_justification` block naming an independent domain source
  and new non-golden tests.
- Stated separation between `evals/public/` (conformance and regression, synthetic) and
  `evals/golden/` (botanical correctness, private), and the blind-evaluation requirement.

### Fixed

- Context-tier observations such as `context.site` can no longer count as positive candidate
  support, so colour plus location cannot manufacture structural corroboration.
- Five existing fake-provider fixtures now state `rough_end_grain` on their rough cut-face
  observations. This is a schema migration only: all sixteen pre-existing public decisions are
  unchanged against the frozen v0.2.2 baseline.

## [0.2.2] — 2026-07-26

Fail-closed correctness hardening. Model-proposed evidence, candidates, findings and reranks
can no longer widen a claim unless deterministic code admits the exact supporting artifact.

### Changed

**Evidence authority is candidate-specific and trust-projected.** Only same-subject image
evidence can positively support identification. Clear medium/high-reliability observations
carry their normal tier; partial or low-reliability evidence is capped to bark-equivalent
authority; obscured, not-visible, contextual-source and unattached evidence remains available
for review but cannot raise a taxonomic claim. Inferences inherit the weakest trust of every
source observation.

**Candidates fail closed at one shared admission boundary.** Primary and reviewer/arbiter
rankings now retain only known taxa with exact taxon-card matches backed by trusted evidence.
Unsupported evidence ids are removed, survivors are densely reranked, and an all-rejected set
produces an explicit abstention rather than a best-effort guess. Confidence, evidence tier and
resolution are computed from the selected candidate's admitted support, not unrelated evidence
elsewhere in the frame.

**Taxon identity follows final resolution.** Cards declare canonical broader identities. When
an upper bound broadens a species proposal to genus or family, the selected id and display name
broaden with it; if no compatible identity is declared, the result is `unknown`. Alternatives
are broadened to the same level and omitted when both candidates collapse to the same identity.

**Deterministic findings cannot be preempted by model restatements.** Findings carry an origin,
deterministic findings are adjudicated first, and duplicate detection uses material fields
rather than category/subject alone. Evidence-backed findings with foreign-subject sources are
rejected and retained with their reason code.

**Reranks are bound to the finding that admitted them.** A `rerank_candidates` finding must
have a same-result recommendation for the same subject, and that ranking passes the shared
candidate validator. Final decision consumes only `AdmittedRerank` artifacts; absent, rejected,
unsupported or conflicting recommendations leave the current ranking unchanged.

**Prompt and deterministic policy compatibility is fail-closed.** `prompts/versions.yaml` now
pins schema `1`, policy revision `0.2.2`, the canonical domain prompt path/hash, node-prompt
root/revision, exact file set and per-file hashes. Validation occurs before provider
construction. Custom `DENDRO_DOMAIN_PROMPT_PATH` deployments must also set
`DENDRO_PROMPT_MANIFEST_PATH` to a matching external compatibility attestation.

### Added

- `dendro prompt-seal` re-pins the manifest after the owner edits a prompt. Without it the
  manifest above made the documented workflow — replace `prompts/domain/system-prompt.md` —
  impossible: the run aborted on a hash mismatch and nothing could rewrite the hash. Dry run by
  default, `--write` to apply, and it never touches the policy revision, because re-sealing
  attests bytes and cannot attest that a changed prompt still means what the code implements.
- Prompt trace and `dendro prompt-info` metadata now include manifest schema, manifest path
  and hash, policy revision, node-prompt revision and compatibility status.
- The public conformance suite expands from nine to sixteen cases: unrelated high-tier evidence,
  all-candidates-removed abstention, resolution-consistent identity, deterministic-finding
  precedence, finding-bound reranks, near-miss vocabulary rejection and partial-visibility
  confidence capping. All sixteen pass with zero overconfidence against the frozen
  `public-v0.2.2` baseline; `public-v0.2.1` is preserved as the historical record.
- Broader taxon identities and the `larix` card carry their own provenance. Family placements
  are standard taxonomy that the domain prompt never states, and `larix` is not in the prompt
  at all — both previously sat under a card-level block claiming section 14. A contract test
  now fails if a family identity claims `domain_prompt` as its source.

### Fixed

- `dendro eval` configures logging like `inspect` does. Without it the new
  `candidate_validation_filtered` warning printed bare and lost `case_id`, `subject_id`,
  `dropped_evidence_ids` and `rejected_taxa` — the fields that say which candidates the new
  boundary removed — along with the JSON formatter's redaction.
- `EvalMetrics.escalations_correct` is now `escalation_decisions_correct`. It counts correct
  arbiter *decisions* over `cases`, including correct non-escalations, but sat between the two
  precision/recall denominators where it read as "16 correct out of 9".

### Notes on this release

Two things a reader of the metrics above should know.

**One pre-existing outcome moved.** `arbiter-ranking-001` confidence `medium` → `low`. The
deterministic contradiction against the Pinus proposal now contributes its conservative
downgrade even though the arbiter still reranks the answer to Picea. No other case changed its
taxon, resolution, arbiter use or retry count.

**Six fixtures were rewritten, not just re-expected.** The stricter admission boundary rejected
`arbiter-review`, `primary-conflict`, `foliage-unattached`, `bark-light-trunk`,
`bark-rough-oak-claim` and `primary-pass`, whose scripted model output used paraphrased feature
tokens and, in one case, partial visibility. Each was repaired by conforming the model output to
card vocabulary. That keeps them as conformance cases but removes the near-miss input they used
to carry, so the drift comparison is not apples to apples: one of nine outcomes changed, but six
of the nine ran on rewritten inputs. Cases 15 and 16 restore the deleted behaviours explicitly.

## [0.2.1] — 2026-07-25

Hardening pass before public push. No new capability; several things that were nearly right
made properly right.

### Changed

**Attachment provenance is a three-state enum**, not a boolean. `AttachmentStatus` is
`confirmed_attached` / `confirmed_detached` / `unknown`. A boolean collapsed "I could not
tell" into "definitely detached", and the next reader would have treated `false` as a
positive finding — a stronger claim than the extractor could make. Only
`confirmed_attached` counts at tier; the other two demote and are reported differently,
because "I could not trace the branch" asks for a different photograph than "I watched it
fall".

**`not_evaluable` added to the user-claim verdict.** Distinct from `doubtful`: doubtful
means there are reasons to doubt, not-evaluable means the photograph cannot support any
assessment in either direction. A photograph that fails the quality gate now returns
`not_evaluable` rather than crediting the system with an opinion it does not have.

**The domain prompt is a specification, not scripture.** `AGENTS.md` previously said the
prompt is always right and the code is always the bug. It now states that a divergence is a
**specification-conformance failure requiring review**, resolved through tests and domain
review rather than by assuming either side. Prompts carry errors, contradictions, stale
rules and requirements that cannot be formally implemented; the old rule made all of those
unfixable. The file is still edited only by its owner.

**Presentation register separated from domain policy.** Personality vocabulary moved out of
hardcoded Python into `prompts/personality/`, selected by `DENDRO_TONE_PROFILE`:

- `standard` (**default**) — dry and direct, safe for demos, workplaces and
  corporate deployment;
- `direct` — the author's original register.

The profile also supplies a register note appended after the domain prompt, so a public
deployment does not reproduce the prompt's own profanity examples. Switching profile changes
wording and nothing else — the contract test proves decisions are identical across both.

**Discriminators can point one way.** Confusion *edges* stay symmetric; `DecisiveDifference`
gained `favours`, because a counted fascicle of two rules Picea out and does not rule Pinus
out for anybody. Nine of the shipped discriminators are now directional.

### Added

**Provenance on every knowledge rule.** `Provenance` records source, source type, region,
life stage, season, confidence, review state, reviewer and review date — on each card, with
per-feature override. It does not make a rule true; it makes a rule *attributable*, so the
list of never-verified rules can actually be produced before the pack grows to 100 taxa.
`review_state: reviewed` requires both a reviewer and a date.

**Duplicate-prompt gate.** A contract test fails if any file is byte-identical to the
canonical domain prompt, with its own CI step. Two copies is a split brain waiting to
happen: one gets updated, the other does not, and the trace hash attests to a stale file.

**Frozen evaluation baseline** at `evals/baselines/public-v0.2.1.json`, with a regression
test. Metrics are compared directionally and per-case decisions exactly — the suite staying
green is not the same as the suite behaving the same way.

### Removed

- The duplicate domain prompt at the repository root. `prompts/domain/system-prompt.md` is
  the only canonical copy; point elsewhere with `DENDRO_DOMAIN_PROMPT_PATH`.

### Fixed

- A `fruit_photo` was requested even when the fruit was already in the frame. At the
  decisive tier with high confidence there is nothing left to ask for, and reflexive hedging
  teaches people to ignore the request entirely.

## 0.2.0 — 2026-07-25

The real dendrology domain prompt replaces the placeholder, and the system is rebuilt around
what it actually says. Previously the prompt was a text file the models read; now it is the
primary knowledge source, and its rules are enforced in code that runs whether a model
cooperates or not.

### Added

**Evidence hierarchy** (`knowledge/evidence_hierarchy.py`) — seven ranked tiers from fruit
down to context, each with a resolution and confidence ceiling. The best available tier caps
the answer. Bark tops out at low confidence however characteristic it looks.

**Attachment provenance** — `Observation.attachment_confirmed` is now mandatory on every
detachable feature family (fruit, seed, cone, leaf, needle, bud, branch) and forbidden
elsewhere. Foliage that cannot be shown to grow on the analysed trunk demotes to context: it
is recorded and reported, but cannot move the verdict.

**Ruling on the user's own version** — `CaseInput.user_claim` plus
`FinalDecision.user_claim_verdict` (`accepted` / `possible` / `doubtful` / `rejected`).
Claims are matched against taxon-card aliases in any language. Rejection requires contrary
evidence above bark level and no field context from the user; `--field-context` blocks it
outright.

**Tone gating** — hard mode is now a conjunction of seven conditions computed from the
evidence, not a status lookup. Being corrected outranks all of them. Jokes require hard mode
plus explicit permission, which is withheld after any correction or restraint finding. The
tone layer can no longer change its own permission level — the contract check enforces it.

**Five response formats** and **Ukrainian output by default**, with `--lang en` available.
Confidence renders on the prompt's X/100 scale as a band, never a point value.

**Failure-case checks**, one per named failure mode: unattached foliage, bark-only claims,
and card-driven look-alikes the model failed to name. `ReviewFinding.proposed_taxon` lets a
finding name its alternative structurally instead of in prose.

**Knowledge pack** grown from 3 conifer genera to 25 taxa with 8 comparison cards, derived
from section 14 of the domain prompt, each carrying Ukrainian aliases.

**Four new evaluation cases** — pale trunk without a named alternative, rough bark with a
user claiming oak, foliage at the frame edge, and an apple on the branch. Nine cases total.

### Changed

- A card-declared contradiction visible only in bark no longer disqualifies a candidate; it
  is recorded as a minor note. Old, weathered and urban trunks look nothing like the
  textbook.
- The look-alike check fires only when a comparison card actually links the taxa. No card
  means no declared basis for the confusion.
- Comparison discriminators are matched on the exact feature, not the family.

### Fixed

- `cached_property` on a slotted dataclass crashed every run at prompt load.

## 0.1.0 — 2026-07-25

Initial vertical slice: image and optional context → evidence extraction → candidate
generation → structured internal review → optional arbitration → confidence and resolution
decision → final response.

### Added

**Typed contracts.** Frozen, closed-world Pydantic v2 models. Observations and inferences are
separate types with enforced referential integrity; constrained string types keep prose out
of structured fields; ordinal support and confidence rather than percentages.

**Executable agent graph.** 18 nodes, declared once and rendered from that declaration, so
the diagram cannot drift from the executor. Pure routing, concurrent reviewer fan-out,
per-node execution events, retry budget of 1 with a provable termination argument.

**Knowledge as data.** Taxon, comparison and regional cards in YAML, loaded lazily per taxon.
A card's `supported_resolution` caps what any answer can claim. Adding a genus is a YAML file,
not a code change.

**Three-way internal review plus independent arbitration.** Botanical, confusion and
confidence reviewers, each combining model judgement with deterministic checks. Findings face
a fixed admissibility test with reason codes; rejections are recorded, not discarded. The
arbiter faces the same bar and can change the answer only through accepted findings.

**Configurable escalation.** Eleven individually switchable triggers and four suppressors,
with a hard-trigger tier that cost suppressors cannot override.

**Provider abstraction.** Logical `primary` / `arbiter` roles; deterministic fake adapter for
all tests; OpenAI and Anthropic adapters as lazy-imported integration boundaries. Malformed
structured output is repaired once, then raises — provider failure is never reported as
scientific uncertainty.

**Input guard.** Instruction-like content in images, filenames, captions and metadata is
recorded as evidence and never obeyed, and forces escalation. Deliberately conservative
against false positives on ordinary botanical prose.

**Tone layer that cannot lie.** The factual answer is composed first; the presentation register is
applied afterwards and a contract check fails the run if any taxon, resolution or confidence
moved.

**Evaluation.** Five deterministic public cases with nine metrics, all reported alongside
their denominators. Runs offline in CI, including on fork pull requests.

**CLI.** `inspect`, `eval`, `graph`, `prompt-info`.

**Domain prompt handling.** Loaded from disk, SHA-256 hashed into every trace, passed to
models byte-for-byte, never normalised or templated, loudly missing rather than silently
defaulted.

**Observability.** JSON logs with secret redaction; inspectable run traces recording nodes,
retries, escalation reasons, arbiter use and the domain-prompt hash — and never keys, image
bytes or hidden reasoning.

**Project scaffolding.** Apache-2.0, contribution and security policy, seven design
documents, GitHub Actions CI with a blocking secret scan.

### Known limitations

- **Knowledge is placeholder content.** Three conifer genera, no dendrologist review. Every
  card declares `placeholder_content: true` and answers backed by one carry a warning.
- **Not validated against real photographs.** The evaluation suite proves the machinery
  behaves on the situations it was built for. It measures nothing about field accuracy.
- **Provider adapters are integration boundaries, not hardened clients.** No streaming, no
  rate-limit backoff, no token accounting, no cost ceiling.
- **One regional pack**, with no `unlikely_taxa` entries.
- **Response composition is deterministic**, not model-backed. `prompts/nodes/response-composer.md`
  documents the contract a future model-backed composer would follow.
- **Retry budget is 1** and not per-node.
- Not a substitute for professional identification. Not for safety, commercial or legal
  decisions.

### Intentionally deferred

- Model-backed response composition.
- Streaming and partial results.
- Cost accounting and per-run budget ceilings.
- A wider knowledge pack, and any pack promoted past placeholder status.
- Multi-image reasoning about the same subject across frames.
- Conversation state across turns (the user-challenge escalation trigger exists but nothing
  currently carries a previous result into a new case).
- A hosted API surface.

Git history begins at v0.2.1; the older entries remain as unlinked release notes.

[Unreleased]: https://github.com/Dendro-Inspector/Dendro-Inspector/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/Dendro-Inspector/Dendro-Inspector/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/Dendro-Inspector/Dendro-Inspector/compare/v0.2.3...v0.3.0
[0.2.3]: https://github.com/Dendro-Inspector/Dendro-Inspector/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/Dendro-Inspector/Dendro-Inspector/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/Dendro-Inspector/Dendro-Inspector/releases/tag/v0.2.1
