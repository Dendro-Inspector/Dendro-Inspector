# Field-guide knowledge research for bark, trunk and missing taxa

- **Status:** Point-in-time research record
- **Owner:** Dendro Inspector maintainers
- **Date:** 2026-11-11

Scope: collect field-guide-level ("mid-level") knowledge that the system can actually use,
weighted towards what photographs show most: bark, trunk and crown. Data:
[`KNOWLEDGE-RESEARCH-2026-11-11.json`](KNOWLEDGE-RESEARCH-2026-11-11.json). Nothing here
has been applied to the cards yet.

## 1. Why bark and trunk

- 169 stored golden-100 run records (`dendro-inspector-kubernetes/runs`, two runs):
  151 of 302 subject decisions were `insufficient_evidence`.
- The feature paths read with values no card declares were `trunk.form` (30 cases),
  `bark.texture` (24), `bark.surface_marks` (23) and `bark.pattern` (8). The most frequent
  path no card declares at all was `bark.epiphytes` (11).
- In the owner's 15-photo annotation pilot (`evals/annotations-ab-pilot.json`), 10 photos
  are rated `confident` at genus and 4 of those show bark and trunk only (Betula, Fagus,
  Carpinus). Experts identify these from bark; the cards mostly cannot.
- One labelled photograph is *Aesculus*, which has no card.

## 2. Method

Five Sonnet research agents, one per taxon group, each mapped characters to existing
`feature.path = value_token` pairs where possible and proposed new tokens otherwise. Every
quote was then re-checked against the raw text of its cited page (`quote_check`, 1.0 =
verbatim); `verified` means the quote was found (score ≥ 0.85).

| Group | Taxa | Characters | Quotes checked | Verbatim | Close | Not found |
| --- | --- | --- | --- | --- | --- | --- |
| Conifers, Betulaceae | 7 | 69 | 70 | 65 | 2 | 3 |
| Fagaceae, Juglans, ash, Robinia, lime | 11 | 103 | 103 | 100 | 2 | 1 |
| Rosaceae, Morus | 9 | 67 | 53 | 50 | 2 | 0 (1 page failed) |
| Acer, Salicaceae | 11 | 71 | 69 | 42 | 5 | 22 |
| Missing taxa | 10 | 78 | 61 | 50 | 11 | 0 |

Quote counts include look-alike entries. Of 388 characters, **281 are verified**: 53
decisive, 121 strong, 101 supporting, 6 rated unreliable. All 27 carded taxa have data.

Source quality is uneven: 140 of the 281 verified characters cite Wikipedia. The rest
cite the Woodland Trust (34), Gabriel Hemery's tree pages (31), Virginia Tech Dendrology
(29), the Gymnosperm Database (18), the Wood Database (9) and a few university extension
pages. The Acer/Salicaceae group has 22 quotes that do not appear on their pages; treat
those characters as unsourced.

## 3. Findings for the system

### 3.1 Sourced bark and trunk knowledge exists for most cards

70 verified characters on bark, trunk, crown or branches are rated decisive or strong. They
fall exactly on the gap paths: `bark.texture` (50 verified characters), `trunk.form` (19),
`bark.pattern` (18), `bark.surface_marks` (8). Examples:

| Taxon | Reading | Level |
| --- | --- | --- |
| Carpinus | `trunk.form = fluted_muscular` | decisive |
| Fagus | `bark.texture = smooth_silvery_grey_lifelong` | decisive |
| Abies | `bark.surface_marks = resin_blisters` | decisive |
| Platanus (no card) | `bark.peeling = large_thin_flakes_camouflage` | decisive |
| Quercus rubra | `bark.pattern = ski_track_ridges_shiny_stripes` | strong |
| Acer platanoides | `bark.ridge_pattern = fine_interlacing_ridges` | strong |
| Betula | `bark.pattern = black_rugged_fissured_base_diamonds` | strong |
| Larix | `bark.texture = deeply_fissured_flaking_reddish_inner` | strong |

The agents were also explicit about where bark does **not** separate taxa: young bark of
almost every broadleaf; *Quercus robur* vs *Q. petraea*; *Tilia cordata* vs
*T. platyphyllos*; old *Juglans* vs *Fraxinus*; young *Malus* vs *Pyrus*; mature plum,
cherry plum and apricot; old *Picea* vs old *Abies*. Those pairs should stay
supporting-only.

### 3.2 Token proliferation is the systemic problem

Of the 281 verified characters, 145 needed a **new** token and 136 reused an existing one.
Card matching is exact `(feature, value)` equality, so every new phrasing the extractor
produces is a coverage gap, and every new card token is one the extractor must happen to
choose. Adding these 145 tokens as written would multiply the problem. The fix is in the
system, not the cards: a small closed value set per bark and trunk path (texture, pattern,
fissure depth, surface marks, trunk form), given to the extractor, with
`knowledge/vocabulary.yaml` refinements for the specific-to-general pairs.

### 3.3 Bark is age-dependent; rules are not

Verified bark characters are recorded for young (25), mature (40) and old (17) trees.
Cards carry `life_stage` only at card level, so a rule such as "smooth grey" cannot say it
holds only for young trees, and the old-trunk reading is then read as a contradiction.
The Betula and Pinus two-zone rules already work around this by hand. A per-rule
`life_stage` (and an extractor reading of trunk size or age) would let one card hold young
and old bark without the veto firing.

### 3.4 `bark.epiphytes` is not a knowledge gap

It was the most frequent undeclared path, but no agent recorded it as a diagnostic
character for any taxon: moss and lichen follow site and aspect, not genus. It should be
treated like other context-only evidence, so it stops inflating the coverage-gap count.

### 3.5 Missing taxa

Ranked by likely frequency in Ukrainian photographs and by confusion with existing cards
(general knowledge; no city inventory was fetched, so the order is not measured):

1. *Aesculus hippocastanum*: commonest street and park tree; confused with *Acer
   pseudoplatanus* and *Quercus*. Already seen in the annotation pilot.
2. *Salix alba* / *S. fragilis*: riversides and villages; confused with *Populus*, *Alnus*.
3. *Ulmus*: streets, shelterbelts and floodplains; confused with *Carpinus*, *Tilia*,
   *Fraxinus*.
4. *Acer negundo*: invasive, very frequent; confused with *Fraxinus* and *Acer*.
5. *Platanus × acerifolia*: avenue tree; decisive camouflage bark.
6. *Ailanthus altissima*: invasive; confused with *Fraxinus*, *Juglans*.
7. *Sorbus aucuparia*; 8. *Corylus avellana*; 9. *Gleditsia triacanthos* (confused with
   *Robinia*); 10. *Styphnolobium japonicum* (confused with *Robinia*).

Bark data for these is the weakest in the set (Wikipedia only, many unsourced entries).

## 4. Recommended next steps

1. Define closed value sets for `bark.texture`, `bark.pattern`, `bark.surface_marks`,
   `bark.fissure_depth` and `trunk.form`, and pass them to the extractor (§3.2).
2. Add per-rule `life_stage` to card rules (§3.3).
3. Classify `bark.epiphytes` as context-only evidence (§3.4).
4. Then add the verified decisive/strong bark and trunk characters to the cards, mapped to
   the closed sets, with expert sign-off on the pairs listed in §3.1 as non-separable.
5. Add cards for *Aesculus*, *Salix*, *Ulmus*, *Acer negundo* and *Platanus* after a
   second, better-sourced research pass on their bark.
6. Measure: replay the stored records with `dendro coverage-audit --replay` before and
   after, and track the `insufficient_evidence` share and wrong-genus count on labelled
   photographs.
