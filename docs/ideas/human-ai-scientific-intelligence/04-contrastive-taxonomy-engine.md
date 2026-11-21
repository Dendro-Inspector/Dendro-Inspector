# 04 — Contrastive taxonomy engine

- **Status:** Draft — research proposal, not implemented
- **Owner:** Dendro Inspector maintainers
- **Date:** 2026-11-22
- **Last-verified:** 2026-11-22 (CURRENT statements checked against `36a961c`)

## Agent brief

| | |
|---|---|
| Spec ID | HAI-04 |
| Priority | P1 research |
| Goal | For every pair of competing taxa on one subject, compute deterministically which observations support each, which are shared, which discriminate, which are missing and which contradict — and the narrowest rank the contrast permits |
| Implementation approved | **No** |
| Prerequisites | HAI-E2 look-alike coverage audit ([18](18-research-roadmap.md)); reviewed comparison data for at least one pair |
| Must not touch | `candidate_validation.py` admission semantics, `final_decision.py` caps, existing cards' values, prompts |
| New model calls | None. The engine is pure code over the evidence packet and knowledge |

## Current state

- **CURRENT.** Admission is per card: a candidate survives when a trusted, same-subject
  observation exactly matches one of its card's strong or supporting expectations
  ([`candidate_validation.py`](../../../src/dendro_inspector/knowledge/candidate_validation.py)).
- **CURRENT.** Naming requires a *discriminating* reading of the leader's own card: a strong
  positive, a confidence exception, or a completed diagnostic combination
  (`final_decision._discrimination`). This is single-card discrimination: "is this reading
  diagnostic of the leader", not "does it separate the leader from rival B".
- **CURRENT.** Pairwise knowledge exists in `ComparisonCard.decisive_differences`
  (`feature`, `separates`, optional `favours`, `photo`), plus `shared_features` and
  `insufficient_features`. It is used to choose photographs
  (`comparison_cards.comparison_photo_features`) and by the confusion reviewer's look-alike
  check. It never decides a verdict. All 8 comparison cards are placeholders.
- **CURRENT.** `DecisiveDifference` names a feature, not the values on each side. The card
  says "`leaf.arrangement` separates acer_saccharinum and populus_alba" and the note says
  opposite vs alternate, but code cannot read which value favours which taxon unless
  `favours` is set, and `favours` names one taxon with no value.
- **CURRENT.** *Ulmus* and *Platanus* have no card; a contrast with them is open-world.

## Problem

1. A leader can be named on a reading that is diagnostic against the *catalogue* but not
   against the specific rival on the table, if the rival has no card or the rival's card
   does not declare that reading as a contradiction.
2. The reader sees "still consistent with this evidence: X, Y" but not *why* X and Y are
   not separated and *what would separate them*.
3. Comparison knowledge is unvalued: code cannot evaluate a decisive difference against
   observations.

## Proposal

**PROPOSED.** A deterministic `contrast(subject, taxon_a, taxon_b)` that produces a
`TaxonomicComparison` artifact. It is **telemetry and explanation first**; it gains decision
authority only through the staged path in "Integration stages".

### Character-state representation

Extend comparison knowledge from "feature separates A and B" to a small **character-state
table** per pair:

```yaml
# PROPOSED CONTRACT — NOT IMPLEMENTED. Illustrative values; botany UNVERIFIED.
comparison_id: alnus-ulmus
taxa: [alnus, ulmus]
characters:
  - feature: leaf.base_symmetry
    states:
      alnus: [symmetric]
      ulmus: [oblique_asymmetric]
    reliability: high            # how consistently the states separate the taxa
    applicability: {season: growing_season, life_stage: any}
    attachment_required: true
    photo: leaf_base_macro
    provenance: {source_type: literature, review_state: unreviewed}
  - feature: cones.type
    states:
      alnus: [small_woody_persistent]
      ulmus: []                  # empty list = state never occurs in this taxon
    reliability: high
    applicability: {season: any}
```

Rules:

- A state list per taxon replaces `favours`. An empty list means "never occurs", which is
  the only thing that lets an observation count *against* a taxon in a contrast.
- A missing taxon key means "unknown for this taxon". Unknown never counts for or against.
- `reliability` is a reviewer's judgement of separation, recorded with provenance. It is an
  ordinal label, not a probability.
- Applicability reuses `LifeStage` and `Applicability` from
  [`schemas/taxon.py`](../../../src/dendro_inspector/schemas/taxon.py).

### Contrast computation (pure code)

For subject `s` and pair `(A, B)`:

| Output | Computed from |
|---|---|
| `supports_a`, `supports_b` | Trusted observations (same trust projection as admission) whose value is in A's or B's state list |
| `shared` | Observations whose value is in both lists, or on a feature listed in `shared_features`/`insufficient_features` |
| `discriminating` | Observations whose value is in exactly one list **and** the other list is declared (possibly empty), **and** reliability is `high` or `medium`, **and** applicability holds |
| `missing` | Characters with declared states for both taxa and no trusted observation on `s` |
| `contradicting_a`, `contradicting_b` | Trusted observation whose value is in the other taxon's list and the character's list for this taxon is declared and excludes it |
| `not_assessable` | Characters observed but untrusted (attachment unknown, partial, low reliability) — reported, never counted |
| `narrowest_defensible_rank` | The rank at which A and B are no longer distinguished: shared genus → genus may stand; otherwise their lowest common ancestor in declared `broader_identities` |

**Absence rule.** A `not_visible` or unobserved character is `missing`, never contradicting.
Only an entry in `EvidencePacket.absent_features` for a character the contrast declares
*observable when present* may count, and only when the feature is in the same tier rules
that already govern negative evidence. Default: absence never discriminates.

### Integration stages

| Stage | Authority | Gate to the next stage |
|---|---|---|
| C0 | Telemetry only: contrast artifacts in the trace, a disclosure line in the answer | Owner approval of the vocabulary; replay shows no verdict changed |
| C1 | **Narrowing only**: if a carded rival is not separated (no discriminating observation for the leader against it), the verdict is bounded at the pair's common rank | Offline replay on labelled units: confident wrong names fall, coverage loss within a pre-registered margin |
| C2 | **Open-world rival**: an uncovered rival (no card) with trusted support on a shared character bounds the verdict the same way, through the existing `open_world_outranked` mechanism | Requires open-world discovery on and reviewed contrast data for that rival |
| C3 | Contrast-driven Next Best Observation ([05](05-active-field-investigator.md)): `missing` characters ranked by reliability and acquisition cost | HAI-05 approved |

A contrast **never widens** a claim. Exact card matching stays the admission boundary;
contrast can only narrow, explain, or plan.

## Contract sketch

See `TaxonomicComparison` and `DiscriminatingEvidence` in
[15](15-proposed-data-contracts.md). Both are trace artifacts, not `FinalDecision` fields.

## Invariants

1. Deterministic and provider-free; identical across runs on the same packet.
2. Never raises resolution or confidence above what existing bounds allow.
3. Uses the existing trust projection (`project_evidence`) — no parallel notion of trusted.
4. A character with no reviewed provenance can explain and plan (C0, C3) but cannot bound a
   verdict (C1, C2).
5. Absence of an observation is never evidence of absence of a character.
6. A pair with no declared characters produces `not_comparable`, not "no difference found".

## Abstain and stop conditions

The engine returns `not_comparable` and changes nothing when: either taxon has no contrast
data; all discriminating characters are inapplicable for the season or life stage; the
subject has unresolved organism binding ([06](06-organism-binding-and-provenance.md)); or
the evidence for both sides comes from one model call that also proposed the names (the
correlated-error case — the contrast is recorded but flagged `single_source`).

## Failure modes

| Failure | Effect | Control |
|---|---|---|
| Wrong state in a contrast table | Confident wrong separation | Provenance with reviewer; contrast data has its own review state; C1 needs reviewed data |
| Vocabulary drift between cards and contrast tables | Characters never match | Contract test: every contrast feature/value must exist in `value_sets.yaml` or card vocabulary |
| Combinatorial growth (28 taxa → 378 pairs) | Unmaintainable | Only pairs declared in `common_confusions` or comparison cards; audit HAI-E2 sizes it first |
| Shared misreading (oak read as `palmate_lobed`) | Contrast "discriminates" for the wrong taxon | Cannot be fixed by contrast; flagged `single_source`; cross-model evidence ([11](11-adversarial-scientific-review.md)) |
| Over-narrowing | Coverage collapse on easy trees | C1 gate measures coverage loss; narrowing only against carded rivals actually in play |

## Evaluation

- **Unit**: synthetic packets per character rule (supports, shared, discriminating,
  missing, contradicting, inapplicable, untrusted).
- **Integration**: contrast artifacts recorded for every public case; `dendro eval --suite
  public` byte-identical decisions at C0.
- **Botanical conformance**: each reviewed contrast table gets a reviewer-signed set of
  synthetic expectations (for example "symmetric base + serrate margin, no cones →
  alnus/ulmus not separated at genus" if a reviewer says so). Written by the reviewer, not
  derived from golden cases.
- **Replay**: C1 on recorded traces with labels; report wrong-name and coverage changes with
  denominators and Wilson intervals.

## Acceptance criteria for a future PR

- [ ] Contrast data schema added with a contract test tying it to the vocabulary.
- [ ] At least one pair reviewed by a named, qualified person, recorded in provenance.
- [ ] C0 only; public suite decisions unchanged; trace schema version bumped and documented.
- [ ] `docs/architecture.md` updated in the same commit (`AGENTS.md` §4.3).

## Open questions

- Should contrast data live in comparison cards (extended) or in a new character registry
  shared by cards and contrasts (the modernisation spec's N1 registry,
  [`core-modernisation.md`](../../specs/core-modernisation.md))? Recommendation: extend
  comparison cards first; migrate when N1 lands.
- Can multi-access keys (DELTA, TDWG SDD; see README references) be imported for the Eastern
  European flora under a compatible licence? **UNKNOWN.**
