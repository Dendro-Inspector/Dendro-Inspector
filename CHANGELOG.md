# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Entries record what a **user** observes. Internal refactors that change no behaviour do not
get entries.

## [Unreleased]

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
hardcoded Python into `prompts/personality/`, selected by `EVIL_DUCK_TONE_PROFILE`:

- `evil_duck_public` (**default**) — dry and direct, safe for demos, workplaces and
  corporate deployment;
- `evil_duck` — the author's original register.

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
  the only canonical copy; point elsewhere with `EVIL_DUCK_DOMAIN_PROMPT_PATH`.

### Fixed

- A `fruit_photo` was requested even when the fruit was already in the frame. At the
  decisive tier with high confidence there is nothing left to ask for, and reflexive hedging
  teaches people to ignore the request entirely.

## [0.2.0] — 2026-07-25

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

## [0.1.0] — 2026-07-25

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

**Tone layer that cannot lie.** The factual answer is composed first; the Evil Duck voice is
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

[Unreleased]: https://github.com/OWNER/evil-duck-dendro-inspector/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/OWNER/evil-duck-dendro-inspector/releases/tag/v0.1.0
