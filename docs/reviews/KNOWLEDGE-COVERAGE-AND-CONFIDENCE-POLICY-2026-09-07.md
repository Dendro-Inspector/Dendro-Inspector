# Review — knowledge coverage and confidence policy, from two live photographs

- **Status:** Point-in-time review of the working tree on `feat/core-logic-hardening`
  (`62681fc`, dirty). Findings below are labelled VERIFIED (re-derived from code or from a
  run artifact on disk) or REPORTED (taken from run artifacts supplied for the review but not
  re-derived here). **B3 was corrected after the first draft** — see the note in that section;
  the original claim that coverage was never measured was wrong. Owner disposition recorded at
  the end of this document.
- **Owner:** Dendro Inspector maintainers
- **Date:** 2026-09-07
- **Scope:** the evidence-hierarchy confidence policy, the candidate admission boundary, and
  knowledge-base coverage — read against two real photographs and their runs.

Two live cases were examined. They fail in two different, non-overlapping ways, and neither
failure is a model failure:

- **Case A (birch bark):** correct taxon perception, suppressed by a policy ceiling.
  *Policy-induced underconfidence.*
- **Case B (photo 058, conifer trunk):** defensible abstention reached through a candidate
  set that could not physically contain the most likely answer.
  *Knowledge-coverage-induced candidate blindness.*

Both matter for the same decision: whether the Golden-100 suite is currently measuring
recognition quality or agent behaviour inside an incomplete knowledge base.

---

## Case A — birch: correct perception, capped by policy

### The subject

Two photographs of a mature light-barked trunk with a dark, coarsely fissured base.

External sources agree that this is normal morphology for a mature *Betula pendula*: white
peeling bark that turns black and rough near the base. The project's own domain prompt says
the same thing — an old birch trunk may be dark and cracked low down, while the typical white
papery bark with black marks is strongly genus-diagnostic for *Betula*.

Assessment offered for these frames:

| Level | Estimate |
|---|---|
| *Betula*, genus | ~90–94/100 |
| *Betula pendula*, species | ~75–85/100 |
| *Populus alba* | materially weaker alternative |
| *Quercus* | effectively not a competitor |

The dark, coarse, cracked bark low on the trunk is **not** an argument against birch, and must
not be used as one.

### What the runs produced

**REPORTED.** Photo 1 → family `Betulaceae`, 50–69/100, `abstained=true`; 11 model calls,
~79.9k output tokens, ~13.9 min critical path. Photo 2 → genus `Betula`, 50–69/100; 7 model
calls, ~63.9k output tokens, ~11.6 min. Both traces carry `code_dirty=true`, so neither is a
clean reproduction of any published commit.

The taxon on photo 2 is correct. The confidence is not an estimate — it is a ceiling.

Photo 1 is worse: family-level abstention discards a resolution the frame supports. The image
does not show merely "a pale trunk" — it shows white bark, black marks and local papery
peeling. It is not the distant, backlit, bark-only failure case for which the eval suite
deliberately requires *Betula*/*Populus* ambiguity.

### A1 — Bark caps confidence at LOW unconditionally

**Status: VERIFIED in code.**

[`evidence_hierarchy.py:168-176`](../../src/dendro_inspector/knowledge/evidence_hierarchy.py)

```python
_CONFIDENCE_CEILING: dict[EvidenceTier, Confidence] = {
    ...
    EvidenceTier.BARK: Confidence.LOW,
```

There is no exemption path. Any bark observation, however diagnostic, is deterministically
capped at the 50–69/100 band. The companion `_RESOLUTION_CEILING` maps `BARK -> GENUS`, so
genus *resolution* was permitted — which is exactly what run 002 returned. `Betula / genus /
50–69` is the arithmetic maximum this policy allows for a bark-only frame.

The module docstring states the intent plainly: *"bark alone does not earn a strong claim no
matter how characteristic it looks."*

This collides head-on with the project's own knowledge card. `knowledge/taxa/betula.yaml`
declares:

```yaml
strong_positive_features:
  - feature: bark.pattern
    values:
      - white_papery_with_black_marks

required_for_high_confidence:
  - bark.pattern_or_leaf
```

The card states that this bark pattern alone can carry a strong genus claim. The core policy
states that no bark can. The card loses, silently.

The requirement grammar itself is **not** at fault: `requirement_selectors`
([`taxon_cards.py:108-119`](../../src/dendro_inspector/knowledge/taxon_cards.py)) parses
`bark.pattern_or_leaf` correctly as `bark.pattern OR leaf`. The earlier dead-limb bug is
fixed. The remaining defect is downstream, in trust and confidence policy.

**Consequence for evaluation.** Swapping the model has no effect on this case. Sonnet, Opus
and a dendrologist all produce the same output band, because the band is not derived from the
observation.

### A2 — `PARTIAL` visibility silently voids a decisive-feature requirement

**Status: VERIFIED in code.**

**REPORTED:** in run 002 the extractor recorded `bark.pattern = white_papery_with_black_marks`
with `reliability=high` and `visibility=partial`.

`observation_trust` ([`evidence_hierarchy.py:222-247`](../../src/dendro_inspector/knowledge/evidence_hierarchy.py))
collapses two materially different situations into one bucket:

```python
if observation.visibility is Visibility.PARTIAL or observation.reliability is Reliability.LOW:
    trust = EvidenceTrust.CAPPED_POSITIVE
```

"I can see this clearly, but it occupies part of the frame" and "I am not confident in this
reading" become the same value. `match_card`
([`taxon_cards.py:74-99`](../../src/dendro_inspector/knowledge/taxon_cards.py)) then treats
the two halves of the card asymmetrically:

```text
positive = positive_observations_for(evidence, subject_id)           # includes CAPPED
full_positive = full_positive_observations_for(evidence, subject_id) # excludes CAPPED
missing = tuple(
    requirement
    for requirement in card.required_for_high_confidence
    if not _requirement_satisfied(requirement, full_positive)
)
...
supporting_hits=_matches(card.supporting_features, positive),
```

So the same observation is quoted as support and simultaneously reported as absent. The
user-facing text becomes self-contradictory:

- `bark.pattern = white_papery_with_black_marks (high reliability)` — cited as evidence
- `Decisive feature not visible: bark.pattern_or_leaf` — cited as a limitation

It is visible. It failed a full-trust gate. The message names the wrong cause.

### A3 — Shared fissure morphology read as a taxon contradiction

**Status: PARTLY VERIFIED — smaller than it first appears.**

`deep_longitudinal_fissures` was treated by reviewers as *Quercus*-like counter-evidence. On
these frames that coarse dark bark sits on the lower part of a mature trunk, which is
consistent with *B. pendula* and not a contradiction at all.

However, the machinery could not register it as one. `betula.yaml` declares
`contradictions: []`, and `_validated_contradiction_ids`
([`candidate_validation.py:125-140`](../../src/dendro_inspector/knowledge/candidate_validation.py))
drops any contradiction id whose source observations do not match the card's declared
contradiction expectations. The token exists only on `quercus.yaml:33`.

So the framing never became machine-visible negative evidence. It lived in reviewer prose —
where it still burns escalation budget and still reaches the user as narrative. The real
defect is that reviewer prose is not bound to the card vocabulary that governs the decision.

**Rule worth stating explicitly:** a feature token that appears on one card is not a feature
exclusive to that card's taxon. A reviewer must not convert shared morphology into negative
evidence unless the target card declares it as a contradiction.

### A4 — Follow-up request ignores what the frame already contains

**Status: REPORTED.**

Both runs asked for `bark_macro_mid_trunk` as the next photograph. The submitted frames
already contain near-macro bark plus wider trunk context. The next informationally valuable
image is a leaf plus a thin shoot with confirmed attachment, ideally showing both leaf
surfaces — that is what separates *Populus alba*, whose leaf underside is characteristically
white-tomentose. The project's own comparison card already encodes this: thin papery peeling
favours *Betula*, a white felted leaf underside favours *P. alba*.

The follow-up selector is not information-gain aware. It re-requests the tier that is already
saturated.

---

## Case B — photo 058: an unwinnable run

### The subject

`evals/100 top/20260510_100131.jpg`. A foreground trunk, pale grey-silver, long and relatively
smooth, transitioning to scaly and fissured bark lower down. Conifer foliage occupies the
upper left of the frame but cannot be traced to the foreground trunk.

Assessment offered for this frame:

| Hypothesis | Estimate |
|---|---|
| *Abies* sp. | ~55–65% |
| *Picea* sp. | ~25–35% |
| *Fagus* | <10% |
| other | remainder |

*Abies alba* leads on the age-related bark transition — smooth grey when young, pale
silver-grey, then scaly and cracked on the lower part of a mature trunk — and it occurs
naturally in Ukraine. *Picea abies* stays a real alternative: old spruce bark also breaks
into small irregular plates, and its drooping secondary branchlets resemble the foliage mass
in the frame. To species, no more than ~50–60% on this single photograph.

The frame's central difficulty is the one the run identified correctly: those conifer branches
cannot be reliably traced to the foreground trunk, so using them as *Picea* evidence would be
dishonest.

### B1 — The leading hypothesis does not exist in the knowledge base

**Status: VERIFIED on disk.**

`knowledge/taxa/` contains 25 cards. There is no `abies.yaml`. `knowledge/regions/eastern-europe.yaml`
lists `larix`, `picea`, `pinus` — and no `abies`.

If this trunk is a fir, the run was **unwinnable by construction**. No prompt, no model and no
amount of reasoning budget could have produced the right answer, because the answer was not in
the retrievable set.

### B2 — The extractor's most diagnostic observations are outside the card vocabulary

**Status: VERIFIED on disk. This is the largest finding in this review.**

**REPORTED:** the extractor recorded, among others, `bark.texture = fine_scales`,
`bark.flake_geometry = thin_irregular_edge_lifting`, `bark.surface_marks = round_oval_scars`,
`trunk.form = straight_cylindrical`, plus a conifer branch as a separate subject with
`attachment = unknown`. The last point is correct discipline: it did not glue nearby foliage
to the trunk merely because the foliage was adjacent.

**VERIFIED:** the union of every `feature:` key across all 25 taxon cards contains neither
`bark.flake_geometry` nor `bark.surface_marks`. `bark.flake_geometry` appears exactly once in
the repository outside this run — in `knowledge/comparisons/pinus-picea-larix.yaml:10`, a
comparison card, which does not participate in card matching. `bark.surface_marks` appears
nowhere.

**VERIFIED in the trace:** the packet carried nine observations (`obs-1` … `obs-9`). The final
decision cites exactly one piece of supporting evidence:

```json
"supporting_evidence": ["bark.texture = fine_scales (medium reliability)"]
```

Edge-lifting flake geometry and round-oval scars are among the more *Abies*-suggestive things
in the frame. They were extracted, carried through the packet, shown to every reviewer, and
then discarded at the admission boundary — the precise failure the code's own docstring
describes:

> *"Anything outside it is extracted, carried through the packet, shown to every reviewer, and
> then silently discarded at the admission boundary"* — `card_value_vocabulary`,
> [`taxon_cards.py:171-176`](../../src/dendro_inspector/knowledge/taxon_cards.py)

**Independently VERIFIED from the run log.** The `evidence_quality` node recorded
`potential_gap_features_absent_from_all_cards: ["bark.flake_geometry", "bark.surface_marks"]`
for this case — the system reached the same two features by itself. See B3.

**Implication for the fix list:** adding `abies.yaml` is *not sufficient*. If the new card does
not declare these feature paths and values, coverage does not move at all. The gap is two
layers deep — a missing taxon **and** a missing feature vocabulary.

### B3 — Coverage is measured, named, logged — and consumed by nobody

**Status: VERIFIED. Corrected after first draft — the finding is narrower and worse than
originally written.**

An earlier draft of this review claimed the coverage gap was never measured. That was wrong,
and the correction matters: the system detected this exact gap, named both features, and wrote
it to `stderr` — then carried on for another three minutes and five model calls without it.

`unmatchable_observations` ([`taxon_cards.py:190-209`](../../src/dendro_inspector/knowledge/taxon_cards.py))
computes the gap. `classify_vocabulary_diagnostics`
([`evidence_quality.py:43-63`](../../src/dendro_inspector/nodes/evidence_quality.py)) splits it
into known-weak signals and possible card gaps. The result is stored on
`EvidenceQualityReport.unmatchable_evidence_ids` and logged. The first line of this run's
`stderr.log`:

```json
{"ts": "2026-09-06T02:54:32.786711+00:00", "level": "WARNING",
 "logger": "dendro_inspector.evidence_quality",
 "message": "evidence_outside_card_vocabulary",
 "unmatchable": 3, "observations": 9,
 "intentionally_weak_evidence_ids": ["obs-2"],
 "potential_coverage_gaps": 2,
 "potential_coverage_gap_evidence_ids": ["obs-4", "obs-5"],
 "potential_gap_features_absent_from_all_cards":
   ["bark.flake_geometry", "bark.surface_marks"],
 "potential_gap_features_with_unknown_values": []}
```

Both features from B2, named exactly, correctly classified as coverage gaps rather than weak
signals, at 02:54:32 — with the run finishing at 02:57:52.

What happened in the intervening 3 min 20 s, from the node durations in the trace:

| After the warning | Duration |
|---|---|
| `candidate_generator` | 58.7 s |
| `botanical_reviewer` | 23.3 s |
| `confusion_reviewer` | 95.8 s |
| `confidence_reviewer` | 119.3 s |
| `arbiter` | 21.3 s |

Five model calls were made *after* the system had already established, deterministically, that
two of this subject's bark observations described features no card in the knowledge base can
represent. Not one of them received that fact.

The three places it does not reach:

1. **Downstream nodes.** No candidate generator, reviewer or arbiter prompt is told that part
   of the evidence is outside the vocabulary. They reason as though the card set were
   complete.
2. **The trace artifact.** Grepping the trace `.json` and `stdout.json` for `unmatchable` or
   `coverage` returns nothing. The evidence-quality report is not serialised into the trace.
3. **The user.** The eight `limitations` entries include "Nothing above bark level is
   resolvable" but never "two bark features you photographed are not describable by this
   system's knowledge base".

So the correct statement is not *"the measurement is missing"*. It is: **the measurement is
correct, early, precise, and inert.** That is a routing defect, not a measurement defect —
which makes the fix considerably cheaper than the original draft implied, and its absence
considerably less excusable.

For Golden-100 the consequence is unchanged. A suite run today writes this signal to
`stderr` and nowhere the scorer can read, so a case cannot be attributed between "the model
failed to see it" and "the knowledge base could not accept it" without hand-reading logs.

### B4 — A generic supporting feature admits a candidate and pays for four model calls

**Status: VERIFIED end-to-end, with cost.**

`cards_in_play` ([`candidate_validation.py:65-93`](../../src/dendro_inspector/knowledge/candidate_validation.py))
admits a card on a match against **either** feature list:

```text
_matches_expectation(
    observation, (*card.strong_positive_features, *card.supporting_features)
)
```

`fagus.yaml` lists `trunk.form: straight_cylindrical` under `supporting_features`. "The trunk
is straight and cylindrical" is not a discriminative feature for beech — it describes a large
share of ordinary trees.

The trace shows `taxon_ids: ["picea", "fagus"]` sent to four consecutive model calls:

| Node | Duration |
|---|---|
| `botanical_reviewer` | 23.3 s |
| `confusion_reviewer` | 95.8 s |
| `confidence_reviewer` | 119.3 s |
| `arbiter` | 21.3 s |
| **subtotal** | **~260 s of a 354 s run** |

The system's own limitation text names the mechanism:

> *"Fine scales weakly support Picea; the cylindrical trunk also fits the existing Fagus
> candidate."*

Failure chain: **generic supporting feature → candidate admission → reviewer disagreement →
escalation → arbiter → four model calls.** `escalation_reasons` opens with
`leading_candidates_close` — the two candidates were close because one of them should never
have been admitted.

### B5 — Was `unknown` the right answer?

Yes. Unlike Case A, abstention here is defensively correct: the diagnostic foliage genuinely
cannot be attached to the foreground trunk.

But it is **the right answer for partly wrong reasons**, and the confusion set it reports is
not the real one:

| | Reported | Correct |
|---|---|---|
| Confusion set | *Picea* weak; *Fagus* also possible | *Abies* / *Picea* unresolved; *Fagus* weak |
| Blocking evidence | no attached foliage | no attached foliage |

Scored by dimension:

| Dimension | Verdict |
|---|---|
| Safety / abstention | good |
| Attachment discipline | good |
| Species/genus accuracy | not assessable |
| Candidate coverage | poor |
| Confusion quality | poor |
| Next-photo request | good |

The run's own README is honest about this: the result is `unknown / low / insufficient
evidence`, *Picea* and *Fagus* were candidates only, diagnostic foliage could not be bound to
the foreground trunk, and the exercise is an engineering experiment rather than a botanical
accuracy measurement.

**REPORTED cost:** 7 model calls, 311,028 input tokens, 8,316 output tokens plus 5,705
reasoning tokens, 5 min 54 s — to return `unknown`. This is not evidence that the model is
weak. It is evidence that a large reasoning budget was spent arguing inside a taxonomy that
did not contain one of the most obvious candidates.

### Trace provenance correction

The `2026-09-06-astra-all-055030-058` trace on disk carries
`code_commit_sha = e1a16919264c648cb953cfd92012f1caf8598da9`, not `62681f…`, with
`code_dirty = true`. `62681f…` is the current branch HEAD and applies to the Case A runs.
Neither set of results is a clean reproduction of a published commit.

---

## Cross-cutting conclusion

The two cases sit on opposite sides of the same architecture:

- **Case A:** the knowledge card was right, the core policy overrode it downward.
- **Case B:** the core policy behaved correctly, the knowledge base could not supply the
  option.

In both, the model's perception was better than the system's output. Run today, Golden-100
answers *"how expensively do our agents behave inside the current KB?"* — not *"how well does
the system recognise trees?"*

---

## Recommended change set, in order

Items 1–3 are mechanical and require no domain decision. Item 4 changes core policy and
touches docstrings and contract tests. Item 5 requires dendrological content; every card
currently carries `placeholder_content: true`.

1. **Route the coverage signal that already exists.** — **LANDED 2026-09-08**, two of three
   consumers. `KnowledgeCoverage` (`schemas/evidence.py`) is built once in
   `evidence_quality.summarise_coverage`, carried on `EvidenceQualityReport`, recorded on
   every run in `RunTrace.knowledge_coverage`, and rendered to the reader as the
   `knowledge_coverage_gap` limitation. The structured log now reads the same object instead
   of rebuilding the classification inline. **Not done:** the third consumer — the reviewer
   and arbiter prompts still reason as though the card set were complete. That is deferred
   as its own change because node prompts are hash-sealed and re-sealing is an owner action.
   Gates: `ruff format`, `ruff check`, `mypy`, `pytest` (842 passed, 1 xfailed),
   `dendro eval --suite public` (24 cases, all metrics unchanged).

   *Found while verifying:* the shipped `primary-pass` fixture itself observes
   `bark.flake_geometry` — the Case B feature — so the project's own demonstration run was
   carrying an invisible coverage gap.

   Original text of this item: The measurement is done and correct
   (B3); it terminates in a log line. Give it three consumers: the trace artifact, the
   user-visible limitations, and the downstream node prompts that currently reason as if the
   card set were complete. No new analysis, no risk to the determinism boundary (§4.6 —
   `EvidenceQualityReport` is already deterministic code), and it immediately reclassifies a
   share of future Golden-100 failures from "model error" to "coverage gap".

1b. **Make `knowledge_coverage_gap` a first-class outcome, not a diagnostic field.** —
   **LANDED 2026-09-08.** `DecisionStatus.KNOWLEDGE_COVERAGE_GAP` is a real status, not a
   flag: the verdict line reads "This knowledge base does not cover this subject", the
   features that fell outside the cards are named, and a next photograph is still requested.
   The gate sits in `evidence_quality` and fires only on the conjunction the owner specified
   — a potential coverage gap **and** no card the admission boundary could open — so a run
   that still has something to rank is untouched. Routing needed no change: the existing
   `sufficient=False` edge already reaches `photo_planner` and terminates.

   Two output defects were found and fixed while verifying: the catch-all
   `no_usable_subject` reason was blaming the frame on a coverage gap, and the gap was being
   stated twice once it became the verdict.

   Gates: `ruff format`, `ruff check`, `mypy`, `pytest` (852 passed, 1 xfailed),
   `dendro eval --suite public` (24 cases, all metrics unchanged). The proof of the early
   exit is structural: fixture `knowledge-coverage-gap.json` scripts only the planner and
   the extractor, so the fake provider raises `UnscriptedCallError` on any later model call.
   Observed node path — `input_guard, planner, evidence_extractor, evidence_quality,
   photo_planner, response_composer, tone_layer`; provider calls confined to `planner` and
   `evidence_extractor`.

   *Original text:* Promoted
   from the open item below on owner instruction. When strong visible features match no card,
   the honest verdict is "this system's knowledge base does not cover this subject" — a named
   outcome — rather than degrading the nearest available card into a weak candidate. Case B
   returned *Picea* weak + *Fagus* weak while holding, in hand, the fact that two of the
   subject's bark features were unrepresentable. Item 1 supplies the signal; 1b decides what
   the pipeline does with it.

2. **Original item 2 — strong-only admission: REJECTED BY MEASUREMENT.** It removes
   legitimate bark-tier/corroborative candidates across existing fixtures.
   **Replacement — strong-path self-contradiction veto: ACCEPTED.** Retrieval and admission
   exclude a card when trusted same-subject evidence conflicts with an observed path that
   the card declares strong-positive. See the measurement section below.

   *Original text:* A card enters the candidate set only on a `strong_positive_features`
   match; supporting features may raise or lower a candidate already admitted, but may not
   open one. Removes *Fagus* from Case B and ~260 s from that run.

3. **Separate `PARTIAL` from low reliability.** — **LANDED 2026-09-08** (`0764e9b`).
   `EvidenceTrust.DECISIVE_POSITIVE` sits between capped and full: it settles a
   `required_for_high_confidence` token and keeps its family's own tier, while staying
   distinct from a clean full view so the partial reading remains legible in the trace. The
   reader-facing phrase is now "Decisive feature not established", held in a named constant
   a test pins.

   *Found while implementing — a schema ambiguity nobody had named.* `PARTIAL` is doing two
   jobs the schema cannot separate:

   | meaning | example | should it cap? |
   |---|---|---|
   | unambiguous, but not filling the frame | a birch bark pattern across part of a trunk | no |
   | partly hidden, so the reading is incomplete | a fascicle count behind a crossing branch | yes |

   `partial-visibility-cap-001` is the second kind and its whole purpose is to stay capped;
   the live birch case is the first kind. Nothing in the packet distinguishes them **except
   the reliability the extractor attached**, so that is what decides: a partial view is
   overcome by an explicit `HIGH` reading and capped below it. That keeps the owner's
   quadrant table exactly (it specifies only `HIGH` and `LOW`) and leaves the eval suite
   flat.

   Two assertions changed their expected values rather than their inputs, both having pinned
   the collapsed behaviour; each now states the new rule and gained a counterpart for the
   capped case. Gates: all five green, `pytest` 846 passed.

4. **Card-declared bark exemption.** — **LANDED 2026-09-08** (`5206efa`).
   `TaxonCard.diagnostic_bark_features`, opt-in per feature *and* value. Three conditions,
   all required: bark tier, resolution no narrower than genus, and a card-declared value
   with a decisively-read matching observation. Ceiling rises exactly one band, recorded as
   its own `bark_exemption` confidence step.

   Validated, not trusted: an entry must name a bark-tier feature and must also appear among
   the card's strong positives, so a card cannot exempt evidence it does not otherwise call
   decisive. The bark-family set is duplicated in `schemas.taxon` (which may not import from
   `knowledge`) and a contract test pins the two equal.

   *The discrimination this produces is sharper than either item alone.* Same feature, same
   value, two reliability readings:

   | case | `bark.pattern` reading | outcome |
   |---|---|---|
   | `light-trunk-birch-001` — distant, backlit | `partial` + `low` | requirement unmet, no exemption, stays **low** |
   | the live Case A run | `partial` + `high` | requirement met, exemption applies, **medium** |

   One number separates them, and it is the one number that should. Betula is the only card
   in the pack that declares anything; Fagus's `bark.texture = smooth_grey` is a strong
   positive and earns nothing, and generic deep fissures earn nothing. Gates: all five green,
   `pytest` 862 passed, 24/24 eval cases, every metric unchanged.

5. **Add `abies.yaml`, register it regionally, extend the feature vocabulary.** —
   **LANDED 2026-09-08** (`35e8643`). All three parts, since the first two alone change
   nothing (B2). The card follows the `larix` precedent — the domain prompt does not name
   this genus either — with `source_type: inferred`, unreviewed, placeholder, and a header
   saying why it exists. The prompt-coverage contract test now carries two documented
   absentees and still asserts each is genuinely absent.

   *Abies* and *Picea* deliberately name the same `needles.attachment` path with different
   values (flat round scar against woody peg), so with the self-contradiction veto one
   reading of that feature rules the other genus out rather than leaving two weak candidates
   to argue over. That is what makes the follow-up photograph the live run asked for worth
   taking. Bark characters are supporting features only. Confusions are symmetric, so
   *Picea* and *Pinus* gained *Abies* in return.

6. **Make follow-up requests information-gain aware.** — **LANDED 2026-09-08** (`832bd37`).
   The existing filter asked whether a target's features were already resolved; a bark macro
   with one unanswered bark feature survived it. The new question is whether answering could
   change anything: a target whose every declared feature sits at or below the tier this
   subject already reached **at decisive trust** ranks behind one that reaches higher.

   Measuring at decisive trust is what keeps it an information-gain rule rather than a ban
   on bark requests — `light-trunk-birch-001` holds no decisive evidence, so a better
   photograph of its bark stays first. Saturated targets are reordered, never removed: if
   the redundant target is the only one on offer, a redundant question beats no question.

7. **Both cases as regressions.** — **LANDED**, across items 1b–6 rather than as one commit.
   - *Birch:* reaches *Betula* at genus; confidence asserted as exactly one band above the
     bark ceiling, derived from the ceiling and the ladder rather than hard-coded; the
     decisive-feature limitation is gone; the next photograph is a leaf. Plus an invariant
     over the whole composed answer — no feature may be cited as support and reported
     unestablished — written as a property rather than an expected string so it survives
     changes to wording, requirement grammar or trust bands.
   - *Photo 058:* the coverage-gap early exit is enforced by construction (an unscripted
     model call raises); *Fagus* is not admitted on trunk form alone, in either direction;
     *Abies* ↔ *Picea* is the confusion set on the live evidence; and needle attachment now
     settles that pair either way.

## What Case A now returns

The chain the two live birch runs failed, on the same evidence, after items 3 and 4:

| | live run | now |
|---|---|---|
| verdict | *Betula*, genus | *Betula*, genus |
| confidence | 50–69/100 | 70–84/100 |
| decisive feature | "not visible: `bark.pattern_or_leaf`" | satisfied; no such limitation |
| next photograph | `bark_macro_mid_trunk` | `leaf_upper_macro` |

Still conservative — one band above the floor, genus only, on bark alone — and no longer
pinned there by arithmetic. This assessment put genus *Betula* at ~90–94/100; the system
says 70–84. That remaining distance is the bark ceiling doing its job, not a defect.

## Recommendation on Golden-100

Do not run the full suite yet. Items 1 and 2 are enough to make a run informative — after
them, a failure can be attributed. Waiting for all seven is unnecessary.

Before any full run, perform a **coverage audit of reference labels against the taxonomy**: if
a golden label, or a realistically close confusion taxon, has no card, that case cannot be
used as a model-accuracy test.

## Open-world behaviour — promoted into the change set

Originally filed here as an open item. On owner instruction it is now **item 1b** above:
`unknown_taxon / knowledge_coverage_gap` is to be a first-class outcome, not a diagnostic
field. The reasoning that justified the promotion is B3 — the system already knows when it is
outside its own vocabulary, and currently does nothing with that knowledge.

## Item 2 as specified does not survive measurement — 2026-09-08

**Status: VERIFIED by applying the change and running the gates.**

The specified rule was "only `strong_positive_features` may open a card". Two measurements
were taken before shipping it.

**Measurement 1 — the two boundaries are coupled.** Narrowing only `cards_in_play`
(retrieval) while leaving `validate_candidate_set` (admission) unchanged breaks the
retrieval contract immediately:

```text
AssertionError: arbiter-review/foreground_log_1: admission kept ['pinus'], which the
evidence-side pre-filter would not have shown the model.
assert {'picea', 'pinus'} <= {'picea'}
```

That test exists to guarantee retrieval never hides a card admission would keep. So both
boundaries have to move together — retrieval alone is not a smaller version of the change.

**Measurement 2 — moving both would reject 11 live candidates across 8 fixtures.** Probing
every fixture for candidates that currently survive on supporting features alone:

```text
arbiter-review/foreground_log_1       : pinus
bark-rough-oak-claim/standing_tree    : fraxinus, quercus
deterministic-preemption/standing_tree: pinus
foliage-unattached/standing_tree      : fraxinus
log-pile-pinus/log_pile               : pinus  (bark + wood tone + resin — three features)
primary-conflict/standing_tree        : pinus
strong-label-thin-support/standing_tree_1: alnus
unbound-rerank/standing_tree          : pinus, picea
unrelated-high-tier/standing_tree     : pinus
```

These are not the *Fagus* failure. They are the system's core competence — a hedged genus
claim at low or medium confidence from bark-tier corroboration, which is exactly what the
domain prompt asks for and what `_CONFIDENCE_CEILING` is built to cap rather than forbid.
The specified rule does not distinguish *Fagus* from `log-pile-pinus`; it deletes both.

Nor do the obvious refinements separate them. Counting supporting hits does not:
`unrelated-high-tier` admits *Pinus* on one. Counting how many cards a feature-value opens
does not: `trunk.form = straight_cylindrical` opens exactly one card, *Fagus*.

**What actually distinguishes the *Fagus* case.** The packet carried
`bark.texture = fine_scales`. The `fagus` card declares `bark.texture: smooth_grey` among
its **strong positive features**. The system read the card's own decisive feature, got a
different answer, and admitted the card anyway on an unrelated general feature. Beech bark
is smooth; that bark was not.

**Shipped rule — the strong-path self-contradiction veto.**
`taxon_cards.self_contradiction_hits`. Applied at both boundaries and kept in step, so
retrieval never shows a model a card deterministic admission can never accept — a hidden
contract split there becomes reviewer noise later.

Pinned invariants. The veto fires on exactly this conjunction:

```text
trusted observation on same subject
+ same feature path as card strong-positive
+ observed value outside card's accepted strong-positive values
→ candidate excluded from retrieval AND admission
```

and on none of these, each of which is silence rather than disagreement:

```text
path unobserved
unknown/unresolvable value
different subject
untrusted observation
→ no self-contradiction veto
```

Two of those four were violated by the first implementation and are now fixed and tested:
an `UNREADABLE_VALUES` value such as `not_resolvable` was vetoing, and `cards_in_play`
pooled every subject's observations before testing the veto, so one trunk's bark could
disqualify a card another trunk in the same frame positively supported.

**What this does not claim.** Generic-support admission is *not* eliminated globally. A
supporting feature can still open a card when no contradictory strong path has been
observed — `test_the_generic_feature_alone_would_otherwise_have_admitted_it` pins exactly
that, and it is the correct conservative behaviour for now. What has been eliminated is the
demonstrated pathological case, without damaging legitimate hedged candidates. The broader
architectural question stays open.

Properties, all verified:

| | |
|---|---|
| *Fagus* on the real Case B evidence | excluded (`bark.texture=fine_scales` vs declared `smooth_grey`) |
| *Picea* on the same evidence | retained — its only strong path, `needles.attachment`, was never observed |
| The 11 candidates above | all retained; silence on a decisive path is not disagreement with it |
| The 11 candidates above | all retained |
| Fixture corpus | 0 candidates excluded across all 24 fixtures |
| Public suite | flat — every metric unchanged |
| Card data required | none — the rule is already implied by every card naming a strong positive |

Gates: `ruff format`, `ruff check`, `mypy`, `pytest` (857 passed, 1 xfailed),
`dendro eval --suite public` (24 cases, every metric unchanged). Negative control: removing
the admission-side clause fails `test_fagus_is_not_admitted_from_a_cylindrical_trunk_alone`.

**Owner disposition, 2026-09-08.** Replacement accepted; strong-only rejected as a general
policy on this evidence. The 11 fixtures above are **not** to be revisited to conform to the
rejected rule — that would be tuning the evidence to a proposed policy instead of tuning the
policy to observed system behaviour.

## Owner disposition — 2026-09-07

Reviewed and accepted. Recorded decisions:

- Status board: **1 ✅ · 1b ✅ · 2 strong-only ❌ rejected by measurement · 2 replacement
  (self-contradiction veto) ✅ · 3 ✅ · 4 ✅ · 5 ✅ · 6 ✅ · 7 ✅.** All items dispositioned. Item 4 follows item 3 deliberately:
  the bark exemption has to build on corrected trust semantics, not on the collapsed
  `PARTIAL`/`LOW` bucket.
- Execution order confirmed as **1 → 1b → 2 → 3 → 4 → 5 → 6 → 7**. Structural blockers are
  removed before any model comparison.
- **No model swap is to be treated as a remedy for either case.** Case A is deterministic-
  ceiling-bound and Case B was unwinnable by construction; changing the provider addresses
  neither.
- Item 4 is accepted **only** in its card-declared form. Globally raising the bark ceiling is
  rejected.
- Golden-100 stays parked until items 1 and 2 land, at which point a run becomes attributable.
- Model comparison (Astra / Sonnet / others) happens **after** the structural work, on an
  identical DI core, so the comparison measures the models rather than the pipeline.

Diagnosis, in the form the owner stated it:

| Case | Chain |
|---|---|
| A | model correct → card correct → **policy wrong** → output degraded |
| B | model observations useful → **KB incomplete** → admission boundary discards signal → reviewers reason inside the wrong candidate universe |
