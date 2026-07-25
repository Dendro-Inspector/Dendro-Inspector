# Architecture

- **Status:** Current
- **Owner:** Evil Duck Dendro Inspector maintainers
- **Date:** 2026-07-25
- **Last-verified:** 2026-07-25

## The problem this shape solves

A multimodal model asked "what tree is this?" will almost always answer with a species. It
will do so from a bark close-up taken in warm light with no scale reference, which is a
photograph that cannot support a species-level claim from anyone. The model is not lying —
it has no mechanism for distinguishing "I can see this" from "this is the sort of thing that
usually goes with what I can see".

So the architecture supplies that mechanism, in three layers, none of which is a prompt.

## Layering

```text
contracts   (schemas/)      what can be said at all
knowledge   (knowledge/)    what counts as evidence for what
graph       (graph/, nodes/) who says it, in what order, and who checks
```

Dependencies point one way: `schemas` knows nothing; `knowledge` depends on `schemas`;
`nodes` depend on both plus `providers`; nothing depends on `nodes` except the registry and
the runner. No node imports another node.

## Contracts

Every contract inherits `schemas/base.py:Contract` — frozen, `extra="forbid"`. Two
consequences:

* a node cannot smuggle state by mutating an object another node holds;
* a model that invents a field fails validation instead of silently widening a contract.

Three constrained string types do most of the work:

| Type | Pattern | Purpose |
| --- | --- | --- |
| `FeaturePath` | `bark.flake_geometry` | namespaced, machine-comparable |
| `ValueToken` | `thin_irregular_edge_lifting` | a token, never a sentence |
| `Identifier` | `foreground_log_1` | a stable id |

`"This is Pinus because the bark is red"` cannot be stored as an observation value. Not
"should not" — the contract rejects it. That single constraint is what forces prose out of
the evidence layer and into the fields where prose belongs (`notes`, `summary`, and the
human-readable response).

### Observation vs inference

The distinction the whole system rests on:

```python
Observation(
    feature="bark.flake_geometry",
    value="thin_irregular_edge_lifting",
    visibility="clear",
    reliability="medium",
)  # something visible

Inference(
    claim="morphology_is_compatible_with_pinus", derived_from=("obs-1", "obs-2"), strength="medium"
)  # a claim about it
```

They are separate types, so an inference cannot occupy an observation's slot.
`derived_from` ids must exist in the same packet — enforced by a model validator, so an
inference with no observable basis cannot be constructed.

### Not visible is not absent

`Visibility.NOT_VISIBLE` means the structure could not be resolved.
`EvidencePacket.absent_features` means it is judged genuinely absent. Conflating them turns
a photograph of a shaded trunk into a confident negative claim, which is why
`visible_observations_for()` exists and why the confidence reviewer raises
`invalid_negative_evidence` when a feature appears in both.

### The evidence hierarchy

Section 2 of the domain prompt is explicit that evidence is not interchangeable, and gives
the ordering. `knowledge/evidence_hierarchy.py` encodes it:

| Tier | Evidence | Resolution ceiling | Confidence ceiling |
| --- | --- | --- | --- |
| 7 | fruit, seed, cone, acorn | species | high (95–100 band) |
| 6 | clear attached foliage | species group | high |
| 5 | leaf arrangement | genus | medium |
| 4 | cut face, wood anatomy | genus | medium |
| 3 | bark | genus | **low** |
| 2 | silhouette, crown form | family | low |
| 1 | context | family | low |

**The best available tier caps the claim**, and the cap is applied in `final_decision.py`
before anything else. This is what makes "по корі точно яблуня / горіх / дуб / ясен"
structurally impossible rather than merely discouraged: bark tops out at low confidence
however characteristic it looks.

Bark is capped in *confidence*, not silenced. An oak candidate from bark remains an oak
candidate at genus level — the prompt is equally clear that a weathered, damaged or urban
trunk must not be used to *reject* a taxon either (FAILURE 3).

### Attachment provenance

A leaf at the edge of the frame may belong to the tree next door. `Observation` therefore
requires `attachment_confirmed` on every detachable family (fruit, seed, cone, leaf, needle,
bud, branch) and forbids it elsewhere — an unanswered question defaults to a hopeful yes in
practice, so the contract will not let it go unanswered.

Unconfirmed detachable evidence demotes to tier 1. It stays in the packet, it appears in the
report, and it raises a finding asking for the photograph that would settle it — but it
cannot move the verdict.

### Ordinal scores, not percentages

`SupportStrength` is `weak | moderate | strong`. `Confidence` is `low | medium | high`.
A model emitting `0.873` for a bark photograph is reporting a number it cannot justify, and
a number invites arithmetic that the underlying evidence does not support.

## Knowledge is data, not agents

Each taxon is a YAML card, not a class, a function or a sub-agent:

```yaml
taxon_id: pinus
supported_resolution: [genus]
strong_positive_features:
  - feature: needles.fascicles
    values: [two, three, five]
contradictions:
  - feature: needles.attachment
    values: [single_on_woody_peg]
required_for_high_confidence: [needles_or_cones]
```

Why data:

* **it is inspectable.** A dendrologist can review `pinus.yaml` without reading Python.
* **it is checkable.** `match_card()` answers "does the evidence contradict this taxon?"
  deterministically. A per-taxon agent would answer it differently every run.
* **it caps the claim.** `supported_resolution: [genus]` is why no amount of model
  confidence produces a species-level Pinus answer. The cap is applied in
  `nodes/final_decision.py:cap_resolution`, tested in `tests/unit/test_final_decision.py`.
* **it scales down.** The loader is lazy and per-taxon. A thousand cards would not enlarge
  a single prompt, because a request only ever loads the handful of taxa in play.

Adding a genus is a YAML file plus an entry in a comparison card. It is not a code change.

## Model providers

Nodes depend on the `ModelProvider` Protocol and on the logical roles `primary` and
`arbiter`. No node imports a vendor SDK or names a commercial model. `providers/registry.py`
is the only module that knows those exist, and vendor SDKs are imported lazily inside the
branch that selects them — so the package installs, imports and tests cleanly without them.

Two failure classes are kept strictly apart:

* `ProviderError` — the model or transport failed. **Not evidence about a tree.**
* a valid response saying "insufficient evidence" — a scientific result. **Not a failure.**

Conflating them is how an outage becomes a confident answer, or how genuine uncertainty gets
reported as a bug. `request_structured()` repairs malformed output once and then *raises*,
rather than degrading to a low-confidence guess.

## Domain prompt handling

The dendrology prompt is an **opaque, user-managed artifact**:

* read from disk at runtime, never embedded in Python;
* SHA-256 hashed, with the hash in every trace;
* passed to models byte-for-byte — no normalisation, no line-ending rewriting, no templating;
* a missing file raises `DomainPromptMissingError` naming the path and the override variable;
* node prompts live separately, so tuning a reviewer never changes the domain prompt's hash.

Backed by `tests/contract/test_domain_prompt_contract.py`, which asserts the bytes reaching
the composed prompt are identical to the bytes on disk.

Composition order is fixed: domain prompt, node prompt, then case context — fenced and
labelled as untrusted. Untrusted material is always last and always labelled.

## Determinism boundary

Which nodes call a model, and which do not, is a deliberate line:

| Model-backed | Deterministic |
| --- | --- |
| planner | input guard |
| evidence extractor | evidence quality gate |
| candidate generator | review synthesis (admissibility) |
| botanical / confusion / confidence reviewers | escalation gate |
| arbiter | correction worker, abstain |
| | final decision engine |
| | response composer, tone layer |

The rule: **a model proposes, code adjudicates.** Every decision that could inflate a claim
— whether evidence suffices, whether a finding is admissible, whether to escalate, what the
final resolution and confidence are, and what the user is told — is deterministic, testable
without a provider, and identical across runs.

The reviewers are hybrid on purpose. The model brings judgement; a deterministic layer in
each reviewer adds the findings that are cheap to check and expensive to miss (card
contradictions, colour dependence, unsupported resolution, invalid negative evidence). The
colour-overweighting evaluation case has all three model reviewers return `pass`, and still
goes red if that deterministic layer is removed.

## State and execution

`GraphState` is frozen and serializable. Nodes are `async (state, ctx) -> state`; they never
mutate what they are given. "No hidden global state" is checkable rather than aspirational:
if a node wants to change something, the change is in its return value or it did not happen.

The executor (`graph/executor.py`) walks a pure routing function, runs the three reviewers
concurrently, records an event per node, and refuses to exceed `max_steps`. It is about a
hundred lines and knows nothing about dendrology.

## Termination

Provable from `graph/routing.py` alone:

1. the only backward edge is `correction_worker -> evidence_extractor`
   (asserted in `tests/contract/test_graph_contract.py`);
2. it is taken only while `state.retries < config.retry_budget`;
3. `correction_worker` increments `retries` on every pass.

Therefore the loop runs at most `retry_budget` times (1 in v0.1) and the graph terminates.
When the budget is spent, routing degrades to abstention rather than looping. `max_steps` is
a backstop against a routing bug, not the primary guarantee.

## Implementation references

- [`src/evil_duck_dendro/schemas/`](../src/evil_duck_dendro/schemas) — contracts
- [`src/evil_duck_dendro/graph/definition.py`](../src/evil_duck_dendro/graph/definition.py) — graph declaration
- [`src/evil_duck_dendro/graph/routing.py`](../src/evil_duck_dendro/graph/routing.py) — termination argument
- [`src/evil_duck_dendro/nodes/final_decision.py`](../src/evil_duck_dendro/nodes/final_decision.py) — the cap
- [`src/evil_duck_dendro/prompts/library.py`](../src/evil_duck_dendro/prompts/library.py) — domain prompt handling
- [`docs/agent-graph.md`](agent-graph.md), [`docs/review-pipeline.md`](review-pipeline.md), [`docs/model-roles.md`](model-roles.md)
