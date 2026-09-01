# Core modernisation — specification

- **Status:** Draft
- **Owner:** Dendro Inspector maintainers
- **Date:** 2026-09-01
- **Last-verified:** 2026-09-01

"Modernisation" here means one thing: every core concern gets exactly one canonical,
declared home, and the core scales from 25 demonstration taxa to a few hundred without any
prompt, table or contract growing with it. It does **not** mean an agent framework, a
rewrite of the executor, embeddings, or a database. The stack (`AGENTS.md` §12), the
determinism boundary (§4.6) and the five gates (§4.5) are fixed points.

Companions: [`core-logic-hardening.md`](core-logic-hardening.md) owns what the deterministic
layer decides; [`latency-and-cost.md`](latency-and-cost.md) owns speed. This document owns
the shape of the data and the API the other two sit on. Change N2 below is also the largest
input reduction available to the latency work, and it is quality-neutral by proof rather
than by measurement.

Every finding was verified against the working tree at `d6f8247` on 2026-09-01 by reading
the code and running the probes in the Evidence appendix. Nothing is implemented.

## Findings

### M1 — Feature knowledge has five homes, and the vocabulary is implicit

**Status:** `VERIFIED`.

What a feature path *means* to the system is spread across four modules:

| Fact about a feature | Where it lives today |
| --- | --- |
| Evidence tier of a family (`bark` → 3, `needles` → 6) | `knowledge/evidence_hierarchy._FAMILY_TIERS` |
| Whether it can belong to a neighbouring tree | `knowledge/evidence_hierarchy.DETACHABLE_FAMILIES` **and** `schemas/evidence._DETACHABLE_FAMILIES` |
| Whether it is colour or tone | `knowledge/evidence_hierarchy._COLOUR_SUFFIXES` |
| Whether it needs a prepared end grain, or any wood surface | `knowledge/evidence_hierarchy._PREPARED_END_GRAIN_PREFIXES`, `schemas/evidence.requires_wood_surface` |
| Whether it is insufficient on its own | `knowledge/comparison_cards.INSUFFICIENT_ALONE` |
| Which family a declared object type expects | `nodes/final_decision._DECLARED_FEATURE_FAMILY` |

The two detachable sets are equal today and nothing asserts that they stay equal. The
*vocabulary* of features and values has no declaration at all: it is whatever the union of
the 25 cards' expectations happens to be — 35 feature paths and 106 value tokens. Prompts
ask for `bark.flake_geometry` and `wood.resin_canals`, neither of which any card declares.
The first is a mismatch recorded on
2026-07-26 and still open. On 116 live evidence packets, a median 48 % of resolvable
observations fall outside that union: the extractor is describing things the knowledge
layer has no word for, and nothing can say whether the gap is a missing card value, a
missing feature, or a model inventing paths.

**Why it matters:** the Main Rule. Adding a feature today means editing up to five Python
tables, every card that uses it, two prompts and the vocabulary the extractor is shown, with
no gate that says which of those were missed.

### M2 — The whole catalogue is sent to the candidate generator; admission keeps three cards

**Status:** `VERIFIED` (probe, 116 live packets).

`candidate_generator` renders every card, every comparison card and the regional pack into
its prompt (≈ 88 k of its 116 k characters). Candidate admission then keeps only candidates
with at least one exact, trusted `(feature, value)` match on their own card. Applying that
same predicate *before* the call, over the observations the extractor actually produced,
keeps:

| Cards kept of 25 | Value |
| --- | ---: |
| median | 3 |
| p90 | 5 |
| max | 7 |
| packets keeping zero | 1 of 116 |

Every card the pre-filter drops is a card no candidate could have been admitted for. The
model is reading 22 cards per call that cannot change the answer, and at a few hundred taxa
the prompt would not fit at all.

### M3 — Card requirements are a string grammar inside a token type

**Status:** `VERIFIED`.

`required_for_high_confidence` entries are `ValueToken` strings such as
`leaf.underside_and_leaf.arrangement_or_fruit.type`, parsed by
`taxon_cards.requirement_selectors` with `_and_` binding tighter than `_or_`. Twenty-two
requirement lines across the cards use the grammar. The v0.6.0 changelog records eight
cards migrated and four requirements that no evidence could ever satisfy because of it. A
grammar that has to be documented in prose, in the docstring and in `docs/architecture.md`
is a second parser for what YAML already expresses.

### M4 — Taxonomy is repeated per card

**Status:** `VERIFIED`.

Sixteen `broader_identities` blocks across the cards restate family placements
(`quercus` → `fagaceae`, with a display name and a provenance block marked `inferred`), and
species cards restate their genus. Standard taxonomy is stated once per taxon that needs it,
in the card that needs it, with no single place that says what `fagaceae` is called.

### M5 — There is no public programmatic API

**Status:** `VERIFIED`.

`dendro_inspector/__init__.py` exports `__version__` and nothing else. The only ways to run a
case are the CLI and `dendro_inspector.runner.run_case`, an async function that returns a
private dataclass. `AGENTS.md` §15 says "public API is a contract" and semantic versioning
applies; there is no named surface for that contract to cover. The positioning the project
has chosen — an audit layer around vision providers — is an embedding use case, and today
embedding means importing an internal module.

### M6 — Provider I/O is fine

**Status:** `VERIFIED`, no change.

The direct-HTTPS adapters use `urllib` inside `asyncio.to_thread`. The 58 real traces show
the reviewer fan-out costing the slowest reviewer, not the sum, so the concurrency is real.
Deadlines are `latency-and-cost.md` L3. Recorded so nobody modernises it for its own sake.

## Changes

### N1 — A declared feature registry (closes M1)

**Rule.** One file declares every feature path the system can observe, what it means, and
what values it takes. Everything else derives from it or is validated against it.

**Where.** A new bottom layer `src/dendro_inspector/vocabulary/` holding `features.yaml` as
package data and a loader with no project dependencies. Layering becomes
`vocabulary ← schemas ← knowledge ← nodes`; schemas may import the registry, which
preserves "schemas know nothing about knowledge". The registry ships with the code rather
than under the knowledge root because tiers and detachability are policy derived from the
domain prompt §2, not pack content, and they move with `policy_revision`.

```yaml
# vocabulary/features.yaml — one entry per feature path
- path: needles.fascicles
  family: needles
  tier: foliage                 # context | silhouette | bark | wood_cut | leaf_arrangement | foliage | fruit_seed
  detachable: true
  colour: false
  wood_surface: none            # none | any | prepared_end_grain
  insufficient_alone: false
  values: [one, two, three, five]
  description: Needles per fascicle, counted on an attached shoot.
- path: bark.colour
  family: bark
  tier: bark
  detachable: false
  colour: true
  wood_surface: none
  insufficient_alone: true
  values: open                  # any ValueToken; capped to bark authority by the colour rule
  description: ...
```

**Derivations.** `evidence_hierarchy.tier_of_feature`, `requires_attachment`,
`is_colour_feature`, `_requires_prepared_end_grain`, `schemas/evidence.requires_wood_surface`
and the attachment validator, `comparison_cards.INSUFFICIENT_ALONE` and
`final_decision._DECLARED_FEATURE_FAMILY` all read the registry. The Python tables are
deleted, not kept as fallbacks. A feature path absent from the registry is context tier,
non-detachable, non-colour — exactly today's unknown-family behaviour — and is reported as
`unregistered_feature` in the quality gate's vocabulary telemetry, separately from
`unknown_value_on_registered_feature` and `registered_but_unused_by_any_card`.

**Validation, all contract-tested:**

- every card expectation names a registered feature, and its values are declared or the
  feature is `open`;
- every comparison-card feature is registered;
- every feature path in a fenced code span of a node prompt is registered (this fails
  today on `bark.flake_geometry` and `wood.resin_canals`; see Open decision 1). Colour paths
  are exempt: `bark.colour` and `inner_bark.colour` are requested deliberately, capped to
  bark authority wherever they appear, and reported as intentionally weak rather than as a
  card gap. A separate test pins that carve-out to exactly those two, so the gate above
  cannot be made green by widening it;
- the registry reproduces the current Python tables exactly for the 35 declared features
  and every family they belong to, so N1 changes no behaviour;
- one detachable set exists.

**Extractor vocabulary.** `evidence_value_vocabulary_context` renders the registry
(path, description, values) instead of the union of cards. The extractor is told what the
system can hear, including open-valued features, which is the honest version of the current
"use these tokens when they fit".

**Tests.** The contract tests above; unit tests that the derived predicates agree with the
deleted tables on every registered feature and on a sample of unregistered ones.

### N2 — Retrieval is admission, applied before the proposal (closes M2)

**Rule.** The candidate generator is shown only the cards that admission could accept.

**Where.** `knowledge/retrieval.py`:

```python
def cards_in_play(evidence: EvidencePacket, knowledge: KnowledgeBase) -> tuple[TaxonCard, ...]:
    """Cards with at least one admissible support hit on any subject, plus their comparison
    partners. Uses the same trust projection and exact expectation match as
    `candidate_validation._validated_support_ids`; the two share one predicate function."""
```

The candidate generator renders `knowledge_context` over that set instead of
`available_taxon_ids()`. Comparison partners are included so the model can still name a
look-alike — a named look-alike without admissible support is rejected at admission as
today, and `overlooked_alternative` findings keep their current admissibility test against
all known taxa. The regional pack is unchanged.

When the set is empty, the node makes no model call: it records `no_card_matches_evidence`
on the trace and returns empty candidate sets for every usable subject, which is exactly
the all-rejected outcome the shared validator would have produced.

**Proof of neutrality.** For any proposal, `validate_candidate_set` over the full catalogue
equals `validate_candidate_set` over `cards_in_play`, because a candidate survives only with
an admissible hit on its own card, and every card with such a hit is in the set. A contract
test asserts this equality over every fixture packet and over a generated sample; a public
case need not change.

**Effect.** Candidate-generator prompt ≈ 116 k → ≈ 35 k characters at the median (3 cards
plus partners), independent of catalogue size. Reviewer projections already restrict to
proposed taxa; their `taxon_ids` gain the retrieved partners so the confusion reviewer sees
the same closure.

**Trace.** `RunTrace` records `cards_in_play` per run; the evidence-quality warning reports
observations that matched no card *within the set* separately from observations that
matched none at all.

### N3 — Structured requirement expressions (closes M3)

**Rule.** A high-confidence requirement is YAML, not a token to parse.

```yaml
required_for_high_confidence:
  - any_of:
      - all_of: [leaf.underside, leaf.arrangement]
      - all_of: [fruit.type]
  - all_of: [needles]           # a bare family selector still matches as a prefix
```

**Where.** `schemas/taxon.RequirementExpression` (`any_of` of `all_of` of selectors);
`taxon_cards._requirement_satisfied` evaluates it directly; `requirement_selectors` and the
`_and_` / `_or_` grammar are removed after the migration. `unreachable_selectors` keeps its
contract test. A migration script rewrites the 22 lines mechanically and a test asserts that
every migrated expression is satisfied by exactly the observation sets the old string was.

**Compatibility.** The string form is accepted for one minor release with a deprecation
warning naming the card, then rejected.

### N4 — Taxonomy declared once (closes M4)

**Rule.** Family and genus placements, display names and their provenance live in
`knowledge/taxonomy.yaml`; a card declares `broader_identities` only to override.

```yaml
# knowledge/taxonomy.yaml
- taxon_id: fagaceae
  resolution: family
  display_name: Fagaceae (букові)
  provenance: {source: "Standard taxonomy", source_type: inferred}
- taxon_id: quercus
  resolution: genus
  parent: fagaceae
- taxon_id: populus_alba
  resolution: species
  parent: populus
```

**Where.** `knowledge/loader.KnowledgeBase` builds each card's `broader_identities` from the
taxonomy chain at load and merges any card-level override on top. `identity_at_or_broader`
and everything downstream are unchanged. The sixteen duplicated blocks are deleted by a
migration; a test asserts every card's derived identities equal its pre-migration tuple.

### N5 — A public API (closes M5)

**Rule.** One importable surface, covered by semantic versioning.

```python
# dendro_inspector/api.py
class InspectionResult(Contract):
    case_id: Identifier
    decisions: tuple[FinalDecision, ...]
    response: CaseResponse
    trace: RunTrace


async def ainspect(
    case: CaseInput, *, config: AppConfig | None = None, root: Path | None = None
) -> InspectionResult: ...
def inspect(
    case: CaseInput, *, config: AppConfig | None = None, root: Path | None = None
) -> InspectionResult: ...
```

`__init__.py` exports `inspect`, `ainspect`, `InspectionResult`, `CaseInput`, `AppConfig`,
`load_config` and `__version__`. The CLI's `inspect` command calls the API. `runner.py`
becomes an implementation detail. `README.md`'s minimal example uses the API; `CHANGELOG.md`
states what is now covered by the §15 contract. No behaviour changes.

### Deferred: subject-scoped correction

`CorrectionDirective.subject_id` already exists, but a retry re-runs extraction and all
three reviewers regardless of subject, so scoping the state clears saves no model call.
The only benefit is a per-subject retry budget, which touches the termination argument in
`graph/routing.py`. Recorded; not scheduled.

## Phasing

| Phase | Contents | Behaviour change | Data migration |
| --- | --- | --- | --- |
| 0 | Contract tests that can fail today: non-colour prompt feature paths ⊆ card vocabulary (fails on `bark.flake_geometry` and `wood.resin_canals`), the colour carve-out pinned to exactly two paths, no invented feature namespace, one detachable set, and the admission-inside-the-pre-filter harness | no | no |
| 1 | N1 registry; Python tables deleted; extractor vocabulary from the registry | no — table equality asserted | `features.yaml` authored from the current tables and cards |
| 2 | N2 retrieval | no — equality proven and tested; prompt *content* shrinks | no |
| 3 | N3 requirements, N4 taxonomy | no — per-card equality asserted | 22 requirement lines, 16 identity blocks |
| 4 | N5 API | no | no |

Phase 1 moves policy tables from Python into YAML without changing a value, so
`policy_revision` does not move; a later edit to `features.yaml` that changes a tier is a
policy change and moves it. Phase 2 changes what the candidate generator is shown, which is
prompt *context*, not prompt *bytes*: no re-seal, and the public suite must be
byte-identical. It also gets one live A/B on the repeatability corpus, because "the model
proposes the same candidates from three cards as from twenty-five" is a belief until
measured, even though admission makes the *verdict* identical either way.

## Gates and acceptance

All five §4.5 gates on every phase. In addition:

- Phase 1: a generated diff of `tier_of_feature`, `requires_attachment`,
  `is_colour_feature`, wood-surface and insufficient-alone answers over every registered
  feature and every feature path in every fixture, before and after, is empty;
- Phase 2: the retrieval-equals-admission test passes over all fixtures and a generated
  sample; public-suite decisions byte-identical; live A/B verdict agreement at or above
  run-to-run agreement, denominators stated;
- Phase 3: every card's `match_card` result on every fixture packet is identical before
  and after migration, requirement by requirement;
- Phase 4: `README.md` example runs as written against the fake provider;
- documents in the same commit: `docs/architecture.md` (layering gains `vocabulary`;
  "Knowledge is data" gains retrieval and the taxonomy file; the requirement-grammar
  paragraph is replaced), `docs/agent-graph.md` (candidate generator responsibility),
  `docs/dataset-policy.md` (where provenance for taxonomy lives), `AGENTS.md` §12 key-files
  table (`vocabulary/features.yaml`, `knowledge/retrieval.py`, `knowledge/taxonomy.yaml`
  with their "when to modify" lines), `README.md` (API example).

## Non-goals

- Embedding or fuzzy retrieval. Exact `(feature, value)` matching *is* the admission rule;
  a retrieval that returns more than admission accepts would be showing the model taxa it
  cannot earn. Value synonyms remain the owner's knowledge-layer decision.
- Replacing the executor, the routing function or the frozen-state model. They cost
  nothing measurable and their termination argument is the project's safety case.
- A database, a service, or a hosted API. `CHANGELOG.md` "Intentionally deferred".
- New dependencies. Every change here is YAML, Pydantic and the standard library (§14).

## Open decisions

1. **`bark.flake_geometry` and `wood.resin_canals`** (Phase 0 makes both a red gate).
   Register them with declared values
   and give at least one card a rule for it, or remove it from the two prompts and the
   comparison card. A dendrology judgement; the §12 conformance process.
2. **Registry location.** Package data under `vocabulary/` (recommended, policy moves with
   code) or under the knowledge root (a pack could then redefine tiers, which the domain
   prompt does not permit).
3. **Deprecation window for the string requirement grammar.** One minor release
   (recommended) or immediate.
4. **API shape.** `inspect` / `ainspect` returning `InspectionResult` (recommended), or
   exposing `run_case` as is.

## Evidence appendix

Probes run 2026-09-01 on the working tree at `d6f8247`, shipped knowledge pack, 116 live
evidence packets from `.bridge/*/answers/` (local, git-ignored).

```text
detachable sets equal: True
declared features: 35   values: 106
declared features by tier: {1: 1, 2: 3, 3: 4, 4: 6, 5: 3, 6: 10, 7: 8}
packets=116 cards kept by exact pre-filter: med=3.0 p90=5 max=7 zero=1
share of resolvable observations outside card vocabulary: med=0.48
requirement lines using _and_/_or_: 22
family placements repeated in broader_identities: 16
backticked feature-path tokens in node prompts: 26; not matchable by any card: 4
  bark.flake_geometry   planner.md            <- genuine gap, Open decision 1
  wood.resin_canals     planner.md            <- genuine gap, Open decision 1
  bark.colour           evidence-extractor.md <- colour, deliberate
  inner_bark.colour     evidence-extractor.md <- colour, deliberate
flake_geometry is also named by knowledge/comparisons/pinus-picea-larix.yaml:10
__init__.__all__ == ["__version__"]
adapters: urllib.request.urlopen inside asyncio.to_thread (openai_compatible, gemini, ollama)
```

Pre-filter predicate used by the probe: an observation with `source: image` and visibility
not in `{not_visible, obscured}` whose `(feature, value)` equals a `strong_positive_features`
or `supporting_features` expectation of the card. The production predicate in N2 adds the
full trust projection (attachment, reliability, wood surface), which can only keep fewer
cards.
