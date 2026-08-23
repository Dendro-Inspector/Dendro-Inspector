# Agent-provider factory total-system review — 2026-08-23

- **Status:** Point-in-time engineering experiment
- **Owner:** Dendro Inspector maintainers
- **Date:** 2026-08-23

## Executive result

The Dendro-owned loopback bridge carried one owner-supplied tree photograph through the
deterministic graph using the Ox factory, then replayed the identical primary result to Sol
and Claude as separate forced arbiter runs. This verifies the integration path and exposes
several real failure modes. It does **not** validate field accuracy: the photograph has no
independently adjudicated label, and the shipped knowledge pack is demonstration content.

The natural run returned probable `carpinus` at genus resolution and low confidence. Sol
rejected that claim and caused deterministic abstention. Claude also argued that Carpinus was
contradicted and proposed Acer/Populus alternatives, but deterministic candidate admission
rejected those reranks and retained Carpinus at genus/low. The model diversity was material,
not cosmetic.

## Scope and evidence identity

- Input: one owner-supplied JPEG outside the repository
- Bytes: `3,539,291`
- SHA-256: `2D04C1A8E27F0C4006BE6FB621370455D04F94F895A174E7A45B80178E0E35FF`
- Declared object: `standing_tree`
- Season: `summer`
- Location: not supplied
- Expected taxon: not supplied to any model or to the graph
- Runtime artifacts: ignored `.bridge/full-photo-v2-20260823/`

The absolute source path is intentionally omitted: this is a public repository and local
owner paths are not public test data.

## Implemented bridge routes

| Logical route | Transport | Upstream | Role in this experiment |
| --- | --- | --- | --- |
| `ox-factory` | OpenCode Zen | `opencode/x-preview-f-free` | Primary worker |
| `ox-factory` | direct OpenRouter | `stealth/ox-alpha` | Primary/reviewer worker |
| `ox-factory` | authenticated Cline | `stealth/ox-alpha` | Primary/reviewer worker |
| `sol-judge` | Codex CLI | `gpt-5.6-sol` | Independent arbiter choice |
| `claude-review` | Claude Code CLI | `opus` alias; reported `claude-opus-5` | Independent arbiter choice |

OpenCode, OpenRouter and Cline are transports for the same Ox family. They improve throughput
and availability; they are not three independent scientific opinions. Sol and Claude were
isolated from each other by route-specific cache namespaces and never received the other
judge's output.

## Integration defects found and handled

### Restricted-host route failures

When the factory inherited the coding agent's restricted sandbox, OpenCode could not write
its user log and direct OpenRouter could not reach its network endpoint. Cline remained
available and correctly took over released claims. A user-launched terminal does not have
those sandbox restrictions. The final factory was therefore started with explicit external
access, matching the owner-authorized experiment.

### Cline structured-output instability

Cline/Ox completed a planner call, but three evidence-extractor attempts all violated the
frozen Pydantic contract. Failures included missing attachment/image references, forbidden
extra fields and a non-JSON wrapper. Removing the wrapper would not have repaired the packet:
local validation still found nine contract violations. Dendro correctly failed closed.

The final run used the same Ox family through OpenCode for planning, evidence and candidate
generation. Its evidence packet validated on the first attempt. This is operational evidence
that transport/agent behavior affects structured-output reliability even when the advertised
upstream model family is the same.

### Codex strict-schema incompatibility

The first Sol request was rejected before inference because Codex strict structured output
requires every object property in `required` and rejects schema defaults. The canonical schema
translation layer now supplies a strict transport schema while Dendro continues to validate
the answer against the original Pydantic model. The updated Sol answer validated on its first
model response.

### Windows launcher environment

Some agent hosts inject both `Path` and `PATH`. Windows PowerShell 5.1 then fails in
`Start-Process` before any worker launches. The factory launcher now canonicalizes the process
variable after resolving all executables.

## Natural Ox run

The full natural run completed in `892.59 s` with 13 graph nodes. The deterministic escalation
gate did not trigger, so neither premium judge was called.

| Request | Contract | Worker | Provider | Upstream duration |
| ---: | --- | --- | --- | ---: |
| 1 | `InspectionPlan` | `opencode-zen-ox` | OpenCode | 241.2 s |
| 2 | `GeneratedEvidencePacket` | `opencode-zen-ox` | OpenCode | 226.9 s |
| 3 | `CandidateProposal` | `opencode-zen-ox` | OpenCode | 115.9 s |
| 4 | `ReviewResult` | `cline-ox` | Cline | 55.1 s |
| 5 | `ReviewResult` | `openrouter-ox` | OpenRouter | 120.0 s |
| 6 | `ReviewResult` | `opencode-zen-ox` | OpenCode | 95.4 s |
| 7 | repair `ReviewResult` | `cline-ox` | Cline | 78.3 s |
| 8 | repair `ReviewResult` | `openrouter-ox` | OpenRouter | 171.8 s |

The three reviewer requests ran concurrently and were claimed by three distinct transports.
The confusion reviewer needed two validation repairs. This is the intended throughput factory
behavior, with one deterministic barrier waiting for all three valid contracts.

Natural decision:

- selected taxon: `carpinus`
- resolution: `genus`
- confidence: `low` (`50–69/100` band)
- arbiter used: no
- evidence tier: 3
- main support: one non-exclusive bark-texture observation
- strongest limitation: foliage attachment to the photographed trunk was not confirmed

This is an engineering result, not a correctness claim. The photograph visibly contains
lobed foliage, creating a serious conflict with Carpinus if those leaves belong to the tree.

## Forced judge comparison

`forced_by_eval_case=true` was applied only to exercise the already-defined deterministic
escalation route. All Ox primary calls were route-isolated cache hits. These forced runs do
not imply that production policy would pay for an arbiter on this case.

| Run | Judge | Valid model attempts | Judge result | Final deterministic result |
| --- | --- | ---: | --- | --- |
| Natural | none | — | gate suppressed escalation | `carpinus`, genus, low |
| Forced Sol | `gpt-5.6-sol` | 1 | Carpinus genus unsupported; abstain | unknown taxon, low |
| Forced Claude | `claude-opus-5` | 1 | Carpinus contradicted; Acer/Populus alternatives | `carpinus`, genus, low |

Sol's live worker duration was `34.2 s`. Its two findings were admitted: abstain because the
available evidence did not defend Carpinus, and request connected foliage/full-trunk images.

Claude's live worker duration was `78.1 s`; Claude Code reported `$0.543812`. Four findings
were admitted, including weak Carpinus support and a request to re-extract foliage attachment.
The proposed Acer and Populus reranks were rejected as `not_actionable` because those taxa did
not cross Dendro's deterministic candidate-admission boundary. The only admitted reranks still
contained Carpinus with a weak score, so final selection remained Carpinus/genus/low.

This divergence demonstrates why the judge must propose findings rather than own the answer.
Sol was more conservative; Claude extracted more botanical alternatives; deterministic policy
constrained both.

## Role-split total-system run

After the comparison above, the owner changed the canonical topology: Claude became the
`primary` worker, Ox the separate concurrent `reviewer` pool, and Sol the only `arbiter`.
The same photograph was run again without a season or location value. This is a second
engineering experiment over the same unlabelled `n = 1` input, not a new accuracy datum.

The first role-split attempt used the OpenAI-compatible `openrouter` loopback dialect. Claude
completed planner and evidence extraction; candidate generation needed both configured repair
attempts before its contract validated. The three Ox requests then fanned out to Cline,
OpenCode and direct OpenRouter. OpenCode and OpenRouter published, but the Cline call exceeded
the adapter's fixed 300-second timeout. Dendro stopped with `ProviderUnavailableError` after
`674.6 s`; Sol and final decision were not called. This is a verified fail-closed result, not
a completed inspection.

The second attempt used the bridge's `anthropic` wire dialect, whose launcher timeout is 3600
seconds. The model routes did not change. It completed all 15 nodes in `354.715 s`:

| Logical role | Bridge route | Upstream | Calls | Validation failures | Measured node time |
| --- | --- | --- | ---: | ---: | ---: |
| `primary` | `claude-main` | Claude Code `opus` | 3 | 0 | 34.0 s, 81.7 s, 37.6 s |
| `reviewer` | `ox-factory` | OpenCode/OpenRouter Ox pool | 3 | 0 | 126.1 s, 172.8 s, 178.3 s |
| `arbiter` | `sol-judge` | Codex `gpt-5.6-sol` | 1 | 0 | 22.3 s |

The reviewer durations overlap because the graph uses `asyncio.gather`; the fan-out wall time
is the slowest review, not their sum. Cline was still occupied by the orphaned request from the
failed attempt, so OpenCode served two of the three current reviews and direct OpenRouter
served one. Deterministic synthesis escalated for
`reviewers_disagree_or_critical_finding` and `possible_multiple_taxa`, then deterministic final
decision returned:

- selected taxon: `acer`
- resolution: `genus`
- confidence: `low` (`50–69/100`)
- arbiter used: yes
- evidence tier: 3
- main admitted support: `leaf.shape = palmate_lobed`
- next photograph: `leaf_upper_macro`

The full trace is an ignored runtime artifact at
`.bridge/full-photo-v2-20260823/traces-claude-main-ox-sol-long-timeout/cli-case.trace.json`.
It records `anthropic:claude-main`, `anthropic:ox-factory` and
`anthropic:sol-judge` as distinct logical provider bindings and labels every reviewer call
with `role: reviewer`.

The failed attempt exposed a queue-lifecycle defect: when the Dendro client times out, the
bridge cannot currently mark its pending request orphaned. Cline timed out later, released the
claim, and reclaimed the same stale request. The clean factory must therefore use a fresh
state directory after an abandoned client run. This behavior is not repaired here; it needs a
separate lifecycle contract and test rather than an ad hoc deletion rule.

## Newly exposed review item

Claude's admitted `re_extract_evidence` finding set `arbiter_synthesis.retry_required=true`,
but routing sends `arbiter_synthesizer` directly to `final_decision`. The architecture document
lists the arbiter as able to reach the correction worker, while the executable graph does not.
This is a specification-conformance mismatch requiring an owner decision and a failing test
before either side changes. It was not repaired as part of the provider integration.

## What is verified and what is not

### Verified by runtime evidence

- atomic first-worker claims and route-isolated caches;
- real image bytes crossed the Dendro adapter/bridge boundary;
- three Ox transports executed the concurrent reviewer fan-out;
- explicit provider failures remained transport failures rather than botanical evidence;
- Sol and Claude each produced a schema-valid arbiter contract;
- deterministic synthesis admitted/rejected judge findings and owned final resolution;
- natural abstention/escalation policy remained deterministic.

### Not verified

- botanical correctness against an expert or labelled benchmark;
- provider retention/privacy beyond each provider's published policy;
- throughput at dataset scale;
- fairness: a fast-polling worker can claim consecutive sequential jobs;
- hedged takeover: another worker waits until the current claim fails or times out;
- the arbiter correction-path mismatch described above.

## Conclusion

The architecture is valuable as a cost-aware epistemic pipeline, and the experiment verified
that the bridge can host Ox throughput plus genuinely different Sol/Claude reviews without
giving any model authority over the final claim. The most important result is not the proposed
taxon. It is that the system preserved provenance, rejected malformed packets, exposed model
disagreement and could abstain when an admitted judge finding made the original answer
indefensible.

The next accuracy experiment needs an independently labelled, held-out photo and must follow
`AGENTS.md` §16: the golden image may measure the cards, but may not author them.
