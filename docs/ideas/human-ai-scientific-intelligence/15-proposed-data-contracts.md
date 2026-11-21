# 15 — Proposed data contracts

- **Status:** Draft — **PROPOSED CONTRACTS — NOT IMPLEMENTED**
- **Owner:** Dendro Inspector maintainers
- **Date:** 2026-11-22
- **Last-verified:** 2026-11-22 (compatibility notes checked against `36a961c`)

## Agent brief

- These are documentation sketches. **Do not add them to `src/dendro_inspector/`.** A future
  implementation follows `schemas/base.py:Contract` (frozen, `extra="forbid"`), uses
  `FeaturePath`, `ValueToken`, `Identifier`, and changes schema, consumers and docs in one
  commit (`AGENTS.md` §4.3).
- **Reuse before adding.** Several requested entities already exist under another name.
  The "Necessary?" column says when *not* to create one.

## Entity index

| Entity | Purpose | Owner spec | Existing equivalent (CURRENT) | Necessary? |
|---|---|---|---|---|
| `TreeIdentity` | Stable identity across inspections | 09 | None (`group_id` in held-out manifest, evaluation only) | Deferred |
| `FieldInspection` | One visit, several rounds | 05, 09 | `CaseInput` = one round | Option B only |
| `MorphologicalCharacter` | A character with states per taxon | 04 | `FeatureExpectation` (per card), `DecisiveDifference` (feature only) | Yes, as contrast-table rows |
| `CharacterObservation` | A reading of a character | — | **`Observation`** | **No — reuse `Observation`** |
| `ObservationProvenance` | How ownership is known | 06 | `AttachmentStatus`, `AttestationOutcome`, `source_component_id` | Yes, additive basis only |
| `OrganismBinding` | Observation → organism, with basis | 06 | Arm V binding records (evaluation only) | Yes |
| `TaxonomicHypothesis` | A hypothesis with state | 05 | `Candidate`, `OpenWorldCandidate`, `visual_hypothesis` | Only as a view over these |
| `TaxonomicComparison` | Contrast result for a pair | 04 | None | Yes (trace artifact) |
| `DiscriminatingEvidence` | One observation that separates a pair | 04 | `discriminating_support` (single-card) | Yes, inside `TaxonomicComparison` |
| `KnowledgeSource` | Citation-level source | 07 | `Provenance.source` (free text) | Yes |
| `NextBestObservation` | Ranked request with safety and stop | 05 | **`PhotoRequest`** | Extend `PhotoRequest` compatibly |
| `Measurement` | Measured value with precision | 10 | `ImageLimitation.scale` only | Later |
| `ExpertAssessment` | Sealed stage-1 assessment | 08 | `ExpertAnswer` (AI-first) | Yes, beside `ExpertAnswer` |
| `HumanAIDisagreement` | Code-computed comparison | 08 | None | Yes |
| `ExpertCorrection` | Tiered correction | 12 | `SubjectRevision` outcomes | Extend with tier and error class |
| `ScientificDecisionRecord` | Decision + derivation + identity | 09 | **`FinalDecision` + `DecisionDerivation` + `runtime_identity`** | **No — compose existing** |
| `LongitudinalEvidenceRecord` | Evidence across visits | 09 | `evidence_history` (one inspection) | Deferred |

## Sketches

All JSON below is illustrative. Taxon examples are not botanical claims.

### `TaxonomicComparison` (trace artifact)

| Field | Type | Invariant |
|---|---|---|
| `subject_id` | Identifier | Must be an identity root in the packet |
| `taxa` | (Identifier, Identifier) | Ordered; both carded, or one open-world flagged |
| `status` | `compared` \| `not_comparable` \| `single_source` | `not_comparable` → all lists empty |
| `supports` | map taxon → observation ids | Ids resolve to trusted observations on `subject_id` |
| `shared`, `missing`, `not_assessable` | tuples | `missing` lists characters, never observations |
| `discriminating` | tuple of `DiscriminatingEvidence` | Each cites a reviewed character |
| `contradicting` | map taxon → observation ids | Requires a declared state list for that taxon |
| `narrowest_defensible_rank` | Resolution | Never finer than the current verdict |
| `contrast_data_version` | string | Hash of the contrast table used |

```json
{
  "_comment": "PROPOSED CONTRACT — NOT IMPLEMENTED",
  "subject_id": "tree_1",
  "taxa": ["alnus", "ulmus"],
  "status": "compared",
  "supports": {"alnus": ["obs-4"], "ulmus": ["obs-4"]},
  "shared": ["obs-4"],
  "discriminating": [],
  "missing": ["leaf.base_symmetry", "cones.type", "fruit.type"],
  "not_assessable": ["obs-7"],
  "contradicting": {},
  "narrowest_defensible_rank": "unknown",
  "contrast_data_version": "sha256:…"
}
```

### `DiscriminatingEvidence`

`observation_id`, `character` (FeaturePath), `favours` (Identifier), `against`
(Identifier), `reliability` (`high | medium`), `applicability_checked` (season, life stage),
`source_ids`.

### `NextBestObservation` (extends `PhotoRequest`)

Keeps `target`, `reason`, `subject_id`; adds optional `rank`, `characters`, `separates`,
`worst_case_remaining`, `tier_gain`, `acquisition` (`cost`, `how`, `reachable_alternatives`,
`alternative_limits`), `requires_attachment_proof`, `season_available`, `safety_checked`,
`ranked_by`. A sibling `InvestigationStop` has `reason` (`resolved`, `ceiling_reached`,
`nothing_obtainable`, `budget_spent`, `no_discriminator_known`, `repeated_failure`,
`target_unresolved`), `explanation`, optional `revisit`. Full example in
[05](05-active-field-investigator.md).

**Invariant:** an NBO that fails the safety filter cannot be constructed (validator), not
merely filtered later.

### `ObservationProvenance` and `OrganismBinding`

```json
{
  "_comment": "PROPOSED CONTRACT — NOT IMPLEMENTED",
  "observation_id": "obs-12",
  "attachment": "confirmed_attached",
  "basis": "extractor_asserted",
  "witnesses": [{"kind": "model_call", "call_ref": "evidence_extractor#1"}],
  "single_witness": true,
  "binding": {
    "organism_id": "org-1",
    "anchors": [{"image_id": "img-1", "kind": "shown_alone"}],
    "designated_by": "user_description:trunk with white paint mark"
  }
}
```

Invariants: `basis` never raises trust above `attachment`'s CURRENT semantics; a model
`witness` added twice is one witness; `organism_id` is local to an investigation.

### `KnowledgeSource`

`source_id`, `kind`, `title`, `authors`, `year`, `publisher`, `url?`, `doi?`,
`retrieved_on`, `retrieved_by` (role), `scope` (region, life stages, seasons),
`licence_note`. Invariant: `url` and `doi` are optional and are never filled by a model.

### `ExpertAssessment` (sealed stage 1)

```json
{
  "_comment": "PROPOSED CONTRACT — NOT IMPLEMENTED",
  "assessment_id": "ea-001",
  "package_id": "sha256:…",
  "expert": {"role": "arborist", "qualification": "certified arborist, 10y", "initials": "X.Y."},
  "sealed_at": "2026-11-22T10:00:00Z",
  "sealed_sha256": "…",
  "hypothesis": {"taxon": "alnus", "resolution": "genus"},
  "certainty": "medium",
  "supporting_observations": ["leaf apex rounded", "near water"],
  "main_uncertainty": "leaf base not visible",
  "alternatives": ["ulmus"],
  "evidence_wanted": ["leaf_base_macro", "cones"]
}
```

Invariant: no AI output for the package may be read before `sealed_at`; the record is
immutable.

### `HumanAIDisagreement`

Computed by code from `ExpertAssessment` and `FinalDecision`: `agreement`
(`same_taxon`, `nested`, `different`, `one_abstained`), `evidence_only_human`,
`evidence_only_ai`, `unverified_assumptions` (attachment basis, season), `alternatives_only_ai`,
`alternatives_only_human`, `missing_characters` (from contrast).

### `ExpertCorrection`

Extends `SubjectRevision` with `tier` (T1–T5, [12](12-learning-from-corrections.md)),
`error_class`, `cited_observations`, `cited_sources`, `second_reviewer` (for T3+),
`change_class` (from [08](08-human-ai-collaboration.md) stage 6).

### `Measurement`

`feature`, `value` (decimal), `unit`, `precision`, `method` (`measured`,
`image_estimated`, `model_estimated`, `reconstructed`), `instrument`, `scale_image_id?`,
`measured_by`. Invariant: `model_estimated` and `reconstructed` project to context.

## Compatibility rules for any implementation

1. New fields optional; old traces and answers parse.
2. `CaseResponse.schema_version` and trace schema version bumped with a documented reader
   negotiation (CURRENT practice).
3. No contract here duplicates `Observation`, `FinalDecision` or `DecisionDerivation`.
