# Evidence coverage and discrimination review

- **Status:** Point-in-time assessment
- **Owner:** Dendro Inspector maintainers
- **Date:** 2026-11-11

Scope: why five labelled *Pinus sylvestris* photographs and seven labelled *Betula*
photographs produced abstentions and wrong genera, what changed, and what is still open.
Diagnostic material: 56 stored run records with full traces (21 pine, 21 birch, 3 walnut, 10
post-deploy golden runs, 1 open-world run). The partial Sonnet golden-100 archive holds no
traces and could not be replayed.

**Denominators.** The 56 records are 13 distinct labelled photographs (6 pine, 7 birch) run
through up to four model configurations. They are 13 samples, not 42 or 56. Nothing below is
an accuracy claim about tree identification in general.

## 1. Root causes (confirmed from stored traces)

Path for each case: raw observation → card match → admission → candidate → decision.

| Case | What happened | Origin |
|---|---|---|
| Pine needles / cones (8/8 genus) | `needles.fascicles = two` and `cones.scale_shape = woody_umbo` hit the pine card's strong features. Opus's strongest reading, a clear attached cone (`cones.type = small_woody_ovoid_closed`, tier 7), was dropped as `feature_not_declared`, which left confidence capped at low. | Works; cone shape uncovered (see §4: it does not separate pine from larch or spruce) |
| Pine bark → *Acer* (Foundry) | Three readings of one bark surface. `scaly_plates` hit *Pinus* (one supporting feature, weak). `irregular_plates` + `mosaic_patchwork` hit *Acer pseudoplatanus* (two supporting features, counted as moderate). Model rank order picked sycamore. | Correlated readings counted as independent; no notion of discriminating evidence |
| Pine bark → *Picea* (Opus, bridge; replay) | `bark.texture = fine_scales` is a supporting feature of both *Picea* and *Abies*; the genus was named on it alone. Live reviewers caught it; the deterministic layer did not. | Supporting-only evidence named a genus |
| Pine mature / young habitus | Every pine reading was dropped as `feature_not_declared`: `needles.presence`, `needles.arrangement = tufted_at_shoot_tips`, `bark.pattern = orange_upper_dark_rough_base`, `crown.shape = broad_irregular_spreading`. The one decisive feature, fascicles, was honestly read as unresolved at that distance. | Missing knowledge (two-zone bark) plus nondiagnostic readings plus a genuine lack of evidence |
| Betula golden-031 (Sonnet) | Four supporting birch observations in the packet; the proposal for *betula* and *populus_alba* cited **no evidence ids**, so both were rejected. | Proposal defect, not admission: admission only weighs what a proposal cites |
| Birch golden-003 / 024 → *Populus*, *Morus* (replay) | One reading, `bark.texture = coarse_furrowed` or `coarse_old`, a supporting feature, named a genus. | Supporting-only evidence named a genus |
| golden-020 | Open-world hypotheses retained, never selected; replay without reviewers named *Salicaceae* from one supporting bark reading. | Same as above |

Also found: 37 of 545 observations carried failed-read values (`unresolved`, `not_resolved`,
`unreadable`, `not_seen`, `none_seen`) that were not recognised as failures. A trusted one on
a card's strong path could veto the card (the PR #29 defect class).

## 2. What changed

- **Discriminating support (`require_discriminating_support`, on by default).** A leader is
  named only when its admitted support includes a feature its card lists as
  strong-positive, or a card-declared diagnostic exception (white papery birch bark).
  Supporting features remain *compatible* evidence: they still admit a candidate, which
  stays in the trace with its evidence. The answer then says "insufficient evidence", why,
  which genera remain consistent, and which photograph would separate them. No weights and no
  thresholds: the split is the one each card's author already made. `false` restores the
  previous policy for comparison only.
- **Correlated readings count once per organ.** Several readings of one organ count as one
  piece of evidence, both in support strength and in the high-confidence corroboration step
  (section 6: "leaves and bark visible" is two organs).
- **Disclosure.** A verdict named on supporting features (legacy mode) says so and names any
  admitted rival that the evidence does not rule out.
- **Failed reads are silence.** The new tokens are added to `UNREADABLE_VALUES` and to the
  suffix rule.
- **Knowledge (sources below).** On *Pinus*, the two-zone Scots pine trunk is added as
  *supporting*, not strong: a lower-trunk view legitimately reads differently, and a strong
  feature would veto pine. On *Pinus*, *Picea* and *Abies*, explicit contradictions are added
  for the other conifers' separating characters (spur-shoot tufts, disc-base needles, erect
  disintegrating cones).
- **Colour finding wording.** The line names the organ actually read, instead of always
  saying "bark or wood".
- **Tooling.** `dendro coverage-audit` (offline, no provider calls) classifies every
  observation and gives each proposal's exact admission outcome. With `--replay`, it runs the
  recorded evidence through today's deterministic decision with neutral reviewers.

## 3. Before / after (replay, neutral reviewers, 42 labelled records, 13 photographs)

| | correct genus | wrong genus | abstained | photos with any wrong genus |
|---|---|---|---|---|
| Live runs, with model reviewers | 25 | 1 | 16 | 1 |
| Replay, previous code | 28 | 7 | 7 | 3 |
| Replay, this change, legacy policy | 28 | 7 | 7 | 3 |
| Replay, this change, default policy | 27 | 0 | 15 | 0 |

- The single correct answer lost was pine bark named *Pinus* from `scaly_plates` alone. The
  sources below show *Picea*, *Abies* and *Acer pseudoplatanus* share that reading, so it was
  right by rank order, not by evidence.
- The knowledge additions do not show in the replay. Stored traces use the models' earlier
  tokens, and the new card tokens reach the extractor only on a new run. Measuring them needs
  a live re-run, which costs money and was not run.
- Observation coverage over the corpus: 147 of 545 supported (27%), 186 valid but uncovered,
  104 attachment-uncertain, 52 context, 25 untrusted, 21 failed reads, 9 unknown organ,
  1 contradictory. Of 135 proposals, 77 were admitted, 31 had all support dropped, 24 had no
  card, and 3 cited nothing.
- The public suite changed in 8 cases on purpose. Each fixture's leader rests on supporting
  features only, so the expected resolution moved from genus to unknown, and every other
  assertion in those cases is unchanged. All 24 cases pass, and the suite metrics equal the
  v0.9.0 baseline (`evals/baselines/public-v0.10.0.json`).

## 4. Sources (accessed 2026-11-11)

| Claim | Source |
|---|---|
| *P. sylvestris* bark: "lower stem thick, scaly-plated, grey-brown; on upper stem and branches, thin, flaking, orange-red"; needles in twos, glaucous, slightly twisted; cones conic | Gymnosperm Database, https://www.conifers.org/pi/Pinus_sylvestris.php |
| *Pinus*: needles in "fascicles of one to eight needles ... basal sheath"; bark "furrowed and plated, plates and/or ridges layered or scaly"; dorsal mucronate umbo | https://www.conifers.org/pi/Pinus.php |
| *Picea*: leaves borne singly on a peg-like sterigma; cones pendent; bark "thin and scaly (with thin plates)" | https://www.conifers.org/pi/Picea.php |
| *Abies*: leaves borne singly, circular scars; erect cones that disarticulate in situ; old bark "flaking in plates" | https://www.conifers.org/pi/Abies.php |
| *Larix*: deciduous; "Leaves in tufts of 10-60 on short (spur) shoots"; cones erect, globose to ovoid | https://www.conifers.org/pi/Larix.php |
| *Acer pseudoplatanus*: bark "cracked and develops small plates with age"; "flakes off in irregular pieces" | https://www.woodlandtrust.org.uk/trees-woods-and-wildlife/british-trees/a-z-of-british-trees/sycamore/ |

eFloras (Flora of North America, Flora of China) failed with a TLS certificate error and is
not cited.

How the pine evidence is classified:
- **Diagnostic:** fascicle count, umbo cone scales.
- **Supporting:** the two-zone trunk.
- **Nondiagnostic:** plated bark (shared with spruce, fir and sycamore); needles present;
  tufted needles (larch also has tufts); broad crown; cone shape alone.
- **Contradictory:** spur-shoot tufts, disc-base needles, erect disintegrating cones.

## 5. Unresolved

1. **Two-zone trunk.** Whether the two-zone Scots pine trunk should be a declared diagnostic
   exception at genus. It is distinctive among the regional natives, but neither the domain
   prompt nor a dendrologist has said so. Until then, a whole-tree pine photograph without
   readable fascicles abstains.
2. **Needles on broadleaf cards.** No broadleaf card contradicts visible needles. This is
   safe today only because no broadleaf card declares a needle feature.
3. **Proposals with no citations.** A proposal that cites no evidence (golden-031, Sonnet) is
   rejected. Completing support deterministically would widen admission, which AGENTS.md
   forbids without an explicit decision.
4. **Tokens the models choose.** Feature tokens are free-form, and the models describe the
   same organ in many ways. The audit's `ambiguous_mapping` status flags same-organ misfiling
   but cannot merge synonyms; that stays a reviewed vocabulary decision.
5. **Replay limits.** Inferences are not recorded in traces, subject kinds are inferred from
   ids, and the reviewers in a replay are neutral. The replay measures the deterministic
   layer only.

## 6. Knowledge maintenance: the next step

The intended pipeline is:

`sources → claim extraction → term normalisation → taxonomic check → human review → versioned card → regression test`

Most of it exists already, and the gaps are small:
- **Reference records and drafts:** `knowledge/references.py` and `dendro draft-card` write
  unreviewed drafts into `knowledge/review-queue/`, and they never load.
- **Per-rule provenance:** each rule already carries its source and `review_state`, which
  keeps operator-supplied, literature and expert-reviewed rules apart.
- **Gap list:** `dendro coverage-audit` produces the list of what to author next.

Worth adding next:
1. A `source_url` and `accessed` field on `Provenance`, instead of free text.
2. An audit report grouped by organ, so an author reviews "bark.pattern values seen" in one
   pass.
3. A reviewed synonym table in `knowledge/vocabulary.yaml`, extending `refinements`, kept
   separate from matching.

Web claims are never imported automatically. A rule enters a card only through review.
