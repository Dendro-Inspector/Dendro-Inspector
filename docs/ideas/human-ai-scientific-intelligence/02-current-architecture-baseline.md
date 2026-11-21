# 02 — Current architecture baseline

- **Status:** Draft — point-of-departure inventory for the research specs
- **Owner:** Dendro Inspector maintainers
- **Date:** 2026-11-22
- **Last-verified:** 2026-11-22 (every row checked against `36a961c` by file read)

## Agent brief

- **Use:** before proposing any change, find the capability here and read the cited file.
  Re-verify; this table is a snapshot (`AGENTS.md` §1.5).
- **Engineering status is not botanical accuracy.** "Implemented and operational" means the
  code path exists and is tested on synthetic fixtures. It says nothing about whether the
  answers are right on real trees. Accuracy evidence is listed separately at the end.

## Shape of the system

**CURRENT.** A typed graph declared once in
[`graph/definition.py`](../../../src/dendro_inspector/graph/definition.py): model nodes
(planner, evidence extractor, candidate generator, three reviewers, arbiter) propose typed
artifacts; deterministic nodes (input guard, evidence quality, attachment authority gate,
review synthesis, escalation gate, correction worker, abstain, final decision, response
composer, tone layer) adjudicate ([`docs/agent-graph.md`](../../agent-graph.md)). One
correction retry; termination is proven from
[`graph/routing.py`](../../../src/dendro_inspector/graph/routing.py)
([`docs/architecture.md`](../../architecture.md#termination)).

## Capability inventory

### Implemented and operational (on the default path)

| Capability | Where | Note for these specs |
|---|---|---|
| Observation vs inference as separate contracts; inference must cite existing observations | `schemas/evidence.py`: `Observation`, `Inference` | Basis of every evidence spec |
| Not visible is not absent | `Visibility.NOT_VISIBLE` vs `EvidencePacket.absent_features`; reviewer finding `invalid_negative_evidence` | Contrastive engine must reuse, not re-invent ([04](04-contrastive-taxonomy-engine.md)) |
| Identity scopes and anatomical components | `Subject.parent_subject_id`; component collapse ([architecture](../../architecture.md#identity-scopes-and-anatomical-components)) | Parent relation is model-asserted |
| Attachment provenance, four states | `AttachmentStatus`: `confirmed_attached`, `confirmed_detached`, `unknown`, `user_attested` | `user_attested` is code-set from uploader statements only |
| Attachment authority counterfactual | [`nodes/attachment_authority_gate.py`](../../../src/dendro_inspector/nodes/attachment_authority_gate.py) | Its docstring names the limit: closing it "needs an independent provenance reviewer, a continuity photograph, or the user" |
| Uploader part attestations | [`nodes/field_attestation.py`](../../../src/dendro_inspector/nodes/field_attestation.py): `apply_part_attestations`; `CaseInput.part_attestations` | Capped support; never settles a requirement |
| Evidence tiers and ceilings | [`knowledge/evidence_hierarchy.py`](../../../src/dendro_inspector/knowledge/evidence_hierarchy.py) | Bark: genus ceiling, low confidence |
| Exact card admission | [`knowledge/candidate_validation.py`](../../../src/dendro_inspector/knowledge/candidate_validation.py) | Exact `(feature, value)` equality; no top-k cutoff |
| Discriminating-support rule | `final_decision.discriminating_support`, `_discrimination`; `GraphConfig.require_discriminating_support` (default on) | A leader matched only on supporting features cannot be named |
| Diagnostic combinations and confidence exceptions | `schemas/taxon.py`: `DiagnosticCombination`, `ConfidenceException` | Existing route for "shared one by one, diagnostic together" |
| Comparison cards | `schemas/taxon.py`: `ComparisonCard`, `DecisiveDifference`; 8 files in [`knowledge/comparisons/`](../../../knowledge/comparisons) | All `placeholder_content: true`, unreviewed |
| Look-alike reviewer check | Confusion reviewer `overlooked_alternative` fires only when a comparison card declares the pair ([review pipeline](../../review-pipeline.md#confusion)) | No card, no declared confusion |
| Deterministic review admissibility | `review_synthesizer.adjudicate` | Rejected findings kept with reason codes |
| Escalation and arbiter | `nodes/escalation_gate.py`; [model roles](../../model-roles.md#escalation-policy) | Arbiter changed 20 of 1,450 live verdicts (cost measurement, not accuracy) |
| Next photograph, one per subject | `photo_planner.choose_request`; `final_decision._next_photo`; `comparison_cards.drop_resolved_photos`, `deprioritise_saturated_photos` | Attachment first, then owed combination, then comparison discriminators filtered by tier saturation |
| Unconfirmed hypothesis beside the verdict | `FinalDecision.visual_hypothesis`; `HypothesisStatus.EVIDENCE_SUPPORTED` | Never graded, never seen by a model |
| Subject roles | `response_composer.designate_targets` | Several subjects: none designated unless an experiment is on |
| Composition record | `DecisionDerivation` in the trace | Bounds, binding source, confidence ledger |
| Evidence history, photo coverage, runtime identity, replay | `observability/evidence_history.py`, `observability/identity.py`, `evaluation/trace_replay.py` | Trace schema 6 |
| Specialist review packages | [`human_review.py`](../../../src/dendro_inspector/human_review.py): `build_package`, `questions_for`, `Outcome` | Reviewer sees the AI answer first |
| Held-out manifest check | `evaluation/heldout.py`; `dendro heldout-check` | Tooling only; no held-out set exists in the repo |
| Spend limit | `GraphConfig.max_model_calls`, `max_tokens` (unset by default) | |
| Conformance suite | [`evals/public/`](../../../evals/public): 24 synthetic cases | Proves machinery, not accuracy |

### Implemented but experimental (off by default)

| Capability | Setting | Where |
|---|---|---|
| Review authority bound to checkable findings | `DENDRO_REVIEW_AUTHORITY=evidence_bound` | [review pipeline](../../review-pipeline.md#what-a-review-may-do-to-the-level) |
| Selective arbitration | `DENDRO_SELECTIVE_ARBITRATION` | [model roles](../../model-roles.md#selective-arbitration-experimental) |
| Adaptive reviewer routing | `DENDRO_ADAPTIVE_ROUTING` | `nodes/adaptive_gate.py` |
| Open-world candidates (taxa with no card) | `DENDRO_OPEN_WORLD_DISCOVERY` | [architecture](../../architecture.md#open-world-candidates) |
| Bounded semantic retrieval | `DENDRO_SEMANTIC_RETRIEVAL` | `knowledge/vocabulary.yaml` refinements |
| Card-independent vision shadow | `DENDRO_VISION_SHADOW` | `schemas/vision.py`: `VisionAssessment` |
| Target selection by the extractor | `DENDRO_TARGET_SELECTION=extractor` | `TargetedEvidencePacket.main_subject_id` |
| Per-node domain prompt sections | `DENDRO_DOMAIN_PROMPT_SECTIONS=per_node` | `prompts/sections.py` |
| Arm M (model alone), arm V (model checked against its card) | evaluation scripts only | `evaluation/model_alone.py`, `evaluation/validated.py` |
| Explicit subject binding | evaluation script only, opt-in | `evaluation/subject_binding.py` |

The deployed configuration lives in a separate infrastructure repository. Which of these
settings a deployment has on is **UNKNOWN** from this repository.

### Partially implemented

| Capability | What exists | What is missing |
|---|---|---|
| Seasonal applicability | `CaseInput.season`; `Provenance.season` on rules | `season` is only rendered into model context (`nodes/_support.py`, line 91); no deterministic rule reads a rule's season |
| Life-stage applicability | `FeatureExpectation.life_stage`; `taxon_cards.applicable_rules` | Limits disagreement only; does not gate positive matches (by design, [dataset policy](../../dataset-policy.md#age-specific-rules)) |
| Knowledge provenance | `Provenance` per card and per rule: source text, `source_type`, region, review state, reviewer, date | No structured citation (URL lives in free-text `note`), no publication/retrieval date, no taxonomic authority or synonyms |
| External reference verification | `ReferenceSource` protocol; `RecordedReferenceSource`; `ReferenceStatus` | Only operator-saved records ship; they can dispute, never verify ([`knowledge/references.py`](../../../src/dendro_inspector/knowledge/references.py)) |
| Card drafting from references | [`knowledge/drafts.py`](../../../src/dendro_inspector/knowledge/drafts.py) writes to `knowledge/review-queue/`, never read by the loader | No review workflow, no reviewer tooling |
| Scale | `ImageLimitation.scale`: `exact`, `approximate`, `absent` | No measurement contract; lengths appear only inside value tokens (Robinia pod) |
| Multi-image subject linking | `Subject.image_ids` | Linking is the extractor's assertion; no independent check across photographs |
| Human feedback into knowledge | `revisions.jsonl` per package | Deliberately nothing flows to cards; no correction tiers |
| User follow-up | `CaseInput.user_challenges_previous_result` | No case history; a follow-up photograph is a new, unrelated inspection |

### Proposed only (absent at `36a961c`)

Persistent tree identity across inspections; resumable investigation; cost-, safety- and
season-aware observation planning; expected-information-gain ranking; blind-first expert
protocol; character-state matrices or multi-access keys; segmentation or visual continuity
checks; physical measurement contracts; calibrated probabilities (ordinal scores are a
deliberate choice, [architecture](../../architecture.md#ordinal-scores-not-percentages));
expert correction tiers; human–AI controlled study.

### Unknown / requires verification

| Question | What would resolve it |
|---|---|
| Field accuracy on photographs outside the development set | The held-out run under [`v1-heldout-protocol.md`](../../specs/v1-heldout-protocol.md) (unsigned) |
| Botanical correctness of the 28 taxon cards | Independent dendrologist review; none recorded ([dataset policy](../../dataset-policy.md#knowledge-cards-record-their-review-status)) |
| Correctness of the 8 comparison cards' discriminators | Same; all are placeholders |
| How strongly errors correlate across providers | Paired multi-provider runs on labelled units |
| Deployed settings | The infrastructure repository and the live configuration |

## Evidence about accuracy (kept apart from engineering)

**EVIDENCE (in-sample, single run each).** On 41 units of the maintainers' own photographs
(7 genera, cards developed with these photographs at hand), 2026-11-14, one model in every
role ([`README.md`](../../../README.md#status)):

| Arm | Correct genus | Wrong genus | Family only | No name | Failed |
|---|---|---|---|---|---|
| Model alone | 38 | 2 | 0 | 0 | 1 |
| Graph | 18 | 1 | 7 | 14 | 1 |

The graph cost 7.9 calls, 196,000 input tokens and 141 s per unit against 1 call, 13,000
tokens and 43 s. Under `evidence_bound`, replay of 25 units gave 19 correct against 13 with
no wrong name.

**EVIDENCE (known limit, pinned by a test).** A misreading shared by naming and extraction
is confirmed by the card check: an oak's lobed leaves read as `palmate_lobed` confirm
*Acer* (`test_a_shared_misreading_is_confirmed_which_is_the_checks_known_limit`,
[evaluation](../../evaluation.md#arm-v-the-model-checked-against-its-card)).

What this means for the research specs: the current system's strength is **not naming
more trees**; on the only data available it names fewer than the model alone. Any proposal
that adds stages must show what it buys ([17](17-evil-duck-review.md)).

## Knowledge base facts used by later specs

| Fact (CURRENT) | Consequence |
|---|---|
| 28 taxon cards; no card for *Ulmus* or *Platanus* | Alnus-vs-Ulmus ([20](20-case-study-alnus-ulmus.md)) and Acer-vs-Platanus are open-world comparisons today |
| *Acer negundo* has no card; [`fraxinus.yaml`](../../../knowledge/taxa/fraxinus.yaml) records it as a look-alike gap | Opposite pinnate leaves stay supporting for *Fraxinus* |
| [`alnus.yaml`](../../../knowledge/taxa/alnus.yaml): only strong feature is `cones.type: small_woody_persistent` | A leafy alder without cones cannot be named under the discriminating-support rule |
| [`quercus.yaml`](../../../knowledge/taxa/quercus.yaml): strong `leaf.shape: simple_lobed` and `acorn.presence: present`; contradiction `leaf.shape: palmate_lobed` | One lobed-leaf reading names oak if attachment is trusted ([21](21-case-study-quercus.md)) |
| `pinus-picea-larix` comparison exists; *Abies* has a card but is not in it | A conifer comparison gap |
| `fagus-carpinus` comparison separates by `trunk.form` and `leaf.shape` | Existing seed for a contrast table |
