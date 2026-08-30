# Architecture

- **Status:** Current
- **Owner:** Dendro Inspector maintainers
- **Date:** 2026-08-30
- **Last-verified:** 2026-08-30

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

The raw subject-wide maximum is not enough: an unrelated fruit on another branch must not
raise a bark-supported candidate. `project_evidence()` first assigns every observation or
inference a deterministic trust level. Only same-subject image evidence is positive support;
clear medium/high-reliability evidence carries its normal tier, while partial or
low-reliability evidence is capped to bark-equivalent authority. Obscured, not-visible,
user/metadata/external and unattached evidence is context only. An inference inherits the
weakest trust and tier of every source observation.

The final cap comes from the selected candidate's already-admitted support ids. This makes
"по корі точно яблуня / горіх / дуб / ясен" structurally impossible and prevents unrelated
high-tier evidence elsewhere in the frame from widening the claim. Bark is capped in
*confidence*, not silenced: a bark-supported candidate may remain at genus and low confidence,
and weak/contextual evidence remains available for finding flaws without earning a taxon.

### Wood-surface provenance

Wood observations carry `prepared_end_grain | rough_end_grain | split_face | planed_face |
unknown`. Legacy packets without the field parse as `unknown`, but newly generated extractor
output must answer the surface question explicitly. Pores, rays, vessels and resin canals carry
wood-anatomy authority only on prepared transverse end grain; on any other surface they remain
context. Coarse rings and visible resin may still support a claim at a bark-equivalent cap.
Colour and tone are always capped and can never admit a candidate without exact non-colour
evidence above context.

Declared split firewood is reconciled after model extraction: deterministic planner state forces
`possible_multiple_taxa`, while conclusions remain scoped by subject id. A material-group pile
is not automatically rejected — corroborated pile-level evidence may support a conservative
result, without asserting that every separable piece is the same taxon.

### Identity scopes and anatomical components

`Subject` means one taxonomic identity scope, not every visually distinct surface. An attached
branch, an upper/lower bark zone or a wood face may declare `parent_subject_id` when it is
visibly part of the same organism or material sample. The evidence contract rejects unknown
parents and cycles. Generated packets are then normalized once: component observations are
re-scoped to the independent identity root and component records are removed before evidence
quality, candidate admission or review runs. Each re-scoped observation retains the internal
`source_component_id`; the run trace groups those observation ids into explicit
`source_component_id -> identity_subject_id` projection records. The extractor cannot author
that provenance field itself.

This is a narrow projection, not relaxed evidence sharing. Neighbouring or crossing branches,
separable firewood pieces and objects that merely share a pile never receive a parent. Their
evidence remains isolated by the existing same-subject boundary. Detachable observations still
need `confirmed_attached`; re-scoping an observation with `unknown` or `confirmed_detached`
attachment cannot make it positive evidence.

### Attachment provenance

A leaf at the edge of the frame may belong to the tree next door. `Observation.attachment`
therefore uses `confirmed_attached | confirmed_detached | unknown` on every detachable family
(fruit, seed, cone, leaf, needle, bud, branch) and is forbidden elsewhere — an unanswered
question defaults to a hopeful yes in practice, so the contract will not let it go unanswered.

Only `confirmed_attached` evidence may support identification. The other states remain in the
packet, appear in the report, and can justify a finding or photo request, but project to
context and cannot move the verdict.

`confirmed_attached` is still a perception claim produced by the extractor. The final-decision
engine therefore tests any detachable observation that is a hinge for the scientific outcome:
it revalidates the proposed candidates with that observation demoted to `unknown` and compares
taxon, status, resolution and confidence. It also records the opposite counterfactual when an
otherwise matching model-proposed observation is currently `unknown`; that alternate outcome
is telemetry only and can never strengthen the returned claim.

When the outcome changes and the extractor supplied no independently normalized
component-to-root chain, the conservative branch wins. A leaf on a branch that was visibly
parented to the trunk and then folded into the root carries `source_component_id`; that
code-owned projection corroborates attachment without banning useful foliage evidence as a
class. The decision and run trace record the critical observation ids, the alternate outcome,
the attachment state used for it and whether the authority policy changed the result.
Acquisition follows the same priority: proving which tree owns potentially decisive foliage
precedes a leaf-surface macro or another morphological discriminator.

### Candidate admission

`knowledge/candidate_validation.py` is the shared boundary for primary candidates and
reviewer/arbiter recommendations. For each candidate it requires a known taxon card, resolves
cited observations and inferences to exactly the candidate set's subject, and keeps only
trusted ids whose source observations exactly match that card's strong or supporting
feature/value expectations. Contradiction ids survive only when they match the card's declared
contradictions.

Candidates with no surviving positive support are removed. Context-tier evidence never counts
as support, and a candidate whose surviving support is entirely colour/tone is rejected. Exact
feature vocabulary is preserved — `.color` is not silently rewritten to `.colour` or `.tone`.
Survivors preserve order but are renumbered densely; when none survive, the explicit empty
`CandidateSet` drives abstention. This same validated support determines evidence tier,
confidence and resolution, so a model cannot cite unrelated evidence to make a plausible name
look earned.

### Ordinal scores, not percentages

`SupportStrength` is `weak | moderate | strong`. `Confidence` is `low | medium | high`.
A model emitting `0.873` for a bark photograph is reporting a number it cannot justify, and
a number invites arithmetic that the underlying evidence does not support.

## Knowledge is data, not agents

Each taxon is a YAML card, not a class, a function or a sub-agent:

```yaml
taxon_id: pinus
native_resolution: genus
supported_resolution: [genus]
strong_positive_features:
  - feature: needles.fascicles
    values: [two, three, five]
contradictions:
  - feature: needles.attachment
    values: [single_on_woody_peg]
required_for_high_confidence: [needles_or_cones]
```

`required_for_high_confidence` is the one place a card carries an expression rather than a
plain token. Entries are canonical feature paths or feature families joined by `_and_` and
`_or_`, where `_and_` binds tighter: `leaf.underside_and_leaf.arrangement_or_fruit.type`
means "both leaf characters, or the fruit". A selector matches a feature exactly or as a
namespace prefix, and nothing rewrites underscores into dots — `bark_pattern` is not a
spelling of `bark.pattern`, it is a feature that does not exist. Requirements are checked
against full-trust positive observations only, so detached foliage never satisfies one.
`knowledge.taxon_cards.requirement_selectors` is the grammar's only implementation, and a
contract test fails on any selector no declared feature could ever match.

Why data:

* **it is inspectable.** A dendrologist can review `pinus.yaml` without reading Python.
* **it is checkable.** `match_card()` answers "does the evidence contradict this taxon?"
  deterministically. A per-taxon agent would answer it differently every run.
* **it caps and names the claim.** `supported_resolution` limits authority, while
  `native_resolution` plus `broader_identities` declare the canonical id/display name to use
  at each broader level. Final decision composes the candidate, card, trusted-support and
  review bounds first, then selects an identity at or broader than that bound. If none exists,
  it returns `unknown`; a species name can never survive under a genus or family resolution.
* **it scales down.** The loader is lazy and per-taxon. A thousand cards would not enlarge
  a single prompt, because a request only ever loads the handful of taxa in play.

Adding a genus is a YAML file plus an entry in a comparison card. It is not a code change.

## Model providers

Nodes depend on the `ModelProvider` Protocol and on the logical roles `primary`, `reviewer`
and `arbiter`. No node imports a vendor SDK or names a commercial model. `providers/registry.py`
is the only module that knows those exist, and vendor SDKs are imported lazily inside the
branch that selects them — so the package installs, imports and tests cleanly without them.

Two failure classes are kept strictly apart:

* `ProviderError` — the model or transport failed. **Not evidence about a tree.**
* a valid response saying "insufficient evidence" — a scientific result. **Not a failure.**

Conflating them is how an outage becomes a confident answer, or how genuine uncertainty gets
reported as a bug. `request_structured()` repairs malformed output once and then *raises*,
rather than degrading to a low-confidence guess.

## Domain prompt handling

The dendrology prompt is an **opaque, user-managed artifact**, but it is not admitted alone.
`prompts/versions.yaml` is a frozen compatibility manifest that binds schema `1`, deterministic
policy revision `0.8.0`, the canonical domain path/hash, node-prompt root/revision, and the
exact node-prompt file set and hashes.

`runner.build_context()` validates the complete bundle before constructing
`ProviderRegistry`. A missing file, unexpected manifest field, incompatible revision, path
mismatch, file-set mismatch, hash mismatch or non-UTF-8 prompt raises `PromptPolicyError`
before any provider can be called. `PromptLibrary.compose()` uses the cached admitted bytes,
so a file modified after validation cannot enter the request.

The default domain prompt is still passed byte-for-byte — no normalisation, line-ending
rewriting or templating. A custom `DENDRO_DOMAIN_PROMPT_PATH` requires a non-default
`DENDRO_PROMPT_MANIFEST_PATH` whose path and hash match. That manifest is an explicit
operator attestation of compatibility, not a proof of natural-language semantic equivalence.

Prompt trace metadata and `dendro prompt-info` record the domain and manifest hashes,
manifest schema, policy revision, node revision and compatibility status. Composition order is
fixed: domain prompt, optional response-register note, node prompt, then case context fenced as
untrusted data.

### Re-sealing

Fail-closed hashing needs a supported way to attest new bytes, or replacing the user-managed
prompt — the one workflow the project exists to carry — becomes unrecoverable without
hand-edited YAML and an out-of-band SHA-256. `dendro prompt-seal` recomputes the domain and
node hashes for the configured paths and regenerates the configured manifest from one
template; `prompts/seal.py` holds the logic and the CLI only renders it. The node-prompt file
set is read from the configured root, so an added or deleted prompt is sealed like an edited
one. The default is a dry run printing `old -> new` per changed hash and exiting `0`: a stale
manifest is the expected state after an edit, and only an unreadable or policy-incompatible
manifest is an error.

The command attests bytes and stops there. `schema_version` and `policy_revision` are copied
from the validated manifest, never recomputed, so a manifest bound to an unsupported revision
is refused instead of upgraded — a hashing command must not be able to imply that a rewritten
prompt still satisfies the deterministic policy. That question is answered by the derivation
table in `AGENTS.md` and by the evaluation suite.

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
contradictions, colour dependence, unsupported resolution, invalid negative evidence).
`FindingOrigin` makes the source explicit, and synthesis always adjudicates deterministic
findings first. Material duplicate detection includes subject, action, impact, evidence ids and
proposed taxon, so a model restatement cannot preempt a deterministic finding by sharing only
its category.

Reviewer model calls do not receive `GraphState`. Orchestration constructs a typed
`ReviewProjection` containing the case, evidence, admitted candidates, knowledge-selection
flags and (for arbitration) the deterministic proposed assessment. The initial boundary is
intentionally pass-through for case photographs and evidence: candidate output is under
review and therefore cannot decide which subjects or photographs the reviewers may inspect.
Each returned `ReviewResult` is code-bound to the projection's evidence ids before synthesis.
The projection reports the photographs that actually reach the provider, not the ones the case
declares, so an unreadable file cannot appear in the trace as evidence a reviewer saw.

Candidate changes cross an additional boundary: the exact `rerank_candidates` finding and its
same-result recommendation are validated and stored together as `AdmittedRerank`. Final
decision never scans raw recommendations. An absent, rejected, unsupported or conflicting
ranking cannot move the answer.

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

- [`src/dendro_inspector/schemas/`](../src/dendro_inspector/schemas) — contracts
- [`src/dendro_inspector/graph/definition.py`](../src/dendro_inspector/graph/definition.py) — graph declaration
- [`src/dendro_inspector/graph/routing.py`](../src/dendro_inspector/graph/routing.py) — termination argument
- [`src/dendro_inspector/knowledge/candidate_validation.py`](../src/dendro_inspector/knowledge/candidate_validation.py) — candidate admission
- [`src/dendro_inspector/nodes/final_decision.py`](../src/dendro_inspector/nodes/final_decision.py) — resolution, identity and confidence bounds
- [`src/dendro_inspector/nodes/review_synthesizer.py`](../src/dendro_inspector/nodes/review_synthesizer.py) — finding and rerank admission
- [`src/dendro_inspector/prompts/library.py`](../src/dendro_inspector/prompts/library.py) — prompt-policy compatibility
- [`docs/agent-graph.md`](agent-graph.md), [`docs/review-pipeline.md`](review-pipeline.md), [`docs/model-roles.md`](model-roles.md)
