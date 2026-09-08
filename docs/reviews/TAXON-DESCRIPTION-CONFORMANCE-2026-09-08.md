# Taxon descriptions against the domain prompt

- **Status:** Point-in-time conformance review; findings and verification limits recorded below
- **Owner:** Dendro Inspector maintainers
- **Date:** 2026-09-08

## Scope and evidence

This review saves the oak and birch comparison and extends the same method to all **26**
current [taxon cards](../../knowledge/taxa/). It compares their descriptions with the
[domain prompt](../../prompts/domain/system-prompt.md), principally section 14, then
checks deterministic rules that consume those descriptions. Sections 2, 6, 13, 15, 16
and 17 supply relevant qualifications.

Inspected source revision: `7d33436a9fe4ecdf3fef4a58a07cc04af8552007`. Tracked source,
knowledge and prompt files were unchanged during the review. The prompt SHA-256 is
`d1abfc373b6a7c715528b80e655085bd83a5ed13c7e1571aa6ed72a1a83edf47` and matches
[the active seal](../../prompts/versions.yaml). This attests bytes, not semantic agreement.

This is specification comparison, not independent botanical validation. No photograph,
reference label, live provider, or external botanical source was used to establish the
findings. All 26 cards are placeholder content with `review_state: unreviewed`. Family
placements and the two prompt-absent taxa have separate provenance that this review does
not botanically validate.

Under [AGENTS.md section 12](../../AGENTS.md), mismatches require conformance review;
they do not authorize rewriting the owner's prompt or automatically widening claim caps.
Section 16 requires independent justification and synthetic regressions for changes
motivated by benchmark failures.

Claim: all 26 cards were compared with applicable prompt descriptions.
Status: VERIFIED for source comparison and explicitly identified offline probes.
Evidence: per-card inventory, source links, token comparison and reproducible probes below.
Not verified: field accuracy, frequency of each extractor encoding, or full-graph behavior
for every described variation.
Next verification step: resolve findings in the decision order below and preserve accepted
requirements as synthetic regressions.

## Token correspondence

A token means one distinct `(feature, value)` pair. Counts compare the strong and
supporting positives on the relevant card. Contradictions, confidence requirements and
bark exemptions are separate policy choices; token presence alone does not validate them.

There are 24 cards with descriptions in section 14 and two documented exceptions,
Abies and Larix. Every annotated section-14 token exists somewhere in the pack. Two
per-card differences need interpretation:

- The generic Populus paragraph annotates `bark.pattern = pale_upper_dark_rough_base`
  while discussing white poplar. It belongs to the Populus alba card, not generic Populus.
  This is distribution across cards, not a missing pack token.
- Pinus adds strong `cones.scale_shape = woody_umbo`, which is neither annotated nor
  described in that paragraph. It inherits section-14 provenance without a separate source.

The existing [annotation contract](../../tests/contract/test_domain_prompt_contract.py)
checks the union of all cards. It does not prove correct taxon placement, faithful
translation of prose qualifications, or mutually exclusive values.

## Shared findings

### F1 — Strong positives become exhaustive allowed-value lists

Status: VERIFIED for the conditional synthetic packets below.

[self_contradiction_hits](../../src/dendro_inspector/knowledge/taxon_cards.py) vetoes
trusted positive evidence when its feature is a strong-positive path but its value differs
from every value declared on that path. Another matching observation does not cancel the
veto. Both [retrieval and admission](../../src/dendro_inspector/knowledge/candidate_validation.py)
apply it.

This cannot distinguish incompatible evidence from different trunk regions or compatible
general/specific descriptions. Reproduced cases include birch's dark base, slightly cracked
beech bark, sycamore-shaped leaves on generic Acer, lobed leaves on Populus, generic drupe
versus apricot, and generic compound-pinnate wording on walnut. Their exact encodings and
outcomes are in P02 and P04–P08/P12 below.

These probes do not establish how often a model emits those packets. Nor do they show that
every veto is wrong: incompatible needle attachment still requires a rejection rule.

Action: reproduce allowed variation and compatible specificity as regressions, then decide
how explicit contradictions, descriptive specificity and regional observations interact.
Do not add every failing token to every card or remove all contradiction checks.

**Resolved after this review.** The seven rejections were two mechanisms, not one, and each
needed its own answer. Both are regressions in
`TestASecondReadingOfTheSamePathIsNotDisagreement` in
[the admission tests](../../tests/unit/test_candidate_validation.py).

In P02, P06, P07 and P12 the same path *also* carries a value the card declares, so the
card's own decisive feature had already matched when the veto fired. A satisfied path no
longer vetoes itself.

P04, P05 and P08 carry no exact hit on the disagreeing path at all; they needed a *declared*
relation between values, which now lives in
[`knowledge/vocabulary.yaml`](../../knowledge/vocabulary.yaml) and reaches every card through
the loader. It is deliberately not derived from the strings — `apricot` shares no substring
with `drupe` — and it is not a matching rule: a related value removes a veto and never
becomes support, so a generic compound pinnate leaf still cannot identify a walnut.

Both narrowings are scoped. Agreement about a leaf cannot cancel disagreement about bark,
which is what keeps live case `20260510_100131` rejected; an undeclared value on a strong
path is still disagreement; and P09 and P11 are unchanged, because neither was ever an F1
case. What remains open under F1 is the *content* of the relation — the four pairs declared
so far are the ones this review's probes proved necessary, not a complete reading of
section 14.

### F2 — Birch's confidence exception is only partly implemented

Status: VERIFIED for code ceilings and the existing regression.

Prompt section 6 includes characteristic white papery birch bark with black marks among
95–100 examples; section 14 permits very high confidence at Betula genus level. General
bark-only caution also exists, so the specific exception requires an explicit interpretation.

The [Betula card](../../knowledge/taxa/betula.yaml) declares diagnostic bark, but
[confidence composition](../../src/dendro_inspector/nodes/final_decision.py) raises its
ceiling only from 50–69 to **70–84**, before any further reductions. The
[existing regression](../../tests/unit/test_live_model_regressions.py) asserts that limit.
A passing implementation test is not proof that the prompt's exception is fully implemented.

Action: agree the exception with the owner, then test it. Merely pale or distant trunks
remain insufficient for a diagnostic-bark claim.

**Resolved by owner decision, 2026-09-08.** The prompt is taken literally: characteristic
white papery bark with black marks earns the 95–100 band at **Betula genus level**, and
nothing else about it changes. Pale bark does not, a distant white trunk does not, generic
peeling bark does not, a species claim does not, and contradicted evidence does not.

Implemented as a declarative primitive rather than a birch case in the decision engine,
because F5 shows birch is not the last of these. `TaxonCard.confidence_exceptions` names
the required feature *and* value, the narrowest claim it may carry, and how far it lifts:

```yaml
confidence_exceptions:
  - requires:
      bark.pattern: white_papery_with_black_marks
    max_resolution: genus
    ceiling: very_high
```

`very_high` is ordinal `HIGH` plus the top display band, so the three-valued confidence
scale is unchanged; `confidence_band` learned a second way to earn 95–100 beside a fruit in
the frame. The engine contributes arithmetic only — every narrowing condition is on the card
or in `confidence_exception_for`, so the policy cannot be read one way here and another way
by anything else that asks. The old `diagnostic_bark_features` field and the fixed one-band
lift are gone; the trace step is `diagnostic_exception`.

### F3 — Sweet cherry has no card-level fruit evidence

Status: VERIFIED for the absent rule and fruit-only admission probe.

The Prunus avium paragraph calls fruit strong evidence. Its
[card](../../knowledge/taxa/prunus_avium.yaml) contains no `fruit.*` expectation, while
requiring `fruit_or_leaf` and requesting `fruit_photo`. P09 shows that a generic drupe
cannot retrieve or admit that candidate.

A generic drupe does not establish sweet cherry species. The remedy is to define the
fruit detail that would discriminate it, not to label every drupe diagnostic of cherry.
Then align the vocabulary, card, derivation and regressions.

### F4 — Annotations compress or add information

Status: VERIFIED by reading prose and adjacent annotations.

- Birch permits oval, triangular and rhombic serrated leaves; annotation and card retain
  only `small_triangular_serrate`.
- Generic Populus permits lobed leaves; its card's shape list omits that option. The
  white-poplar-specific token exists but can veto the genus through F1.
- Pinus prose says needles; its annotation requests counted fascicles of two, three or
  five. Visible needles alone do not establish the count.
- Picea prose says short, dense needles; its annotation names attachment on a woody peg,
  a different character that must actually be observed.
- Age-related bark details, Robinia's flower clusters and several cut-wood characters
  remain prose-only; individual entries below identify them.

Not every omitted sentence needs an admission rule. Colour and context are intentionally
weak, and physical properties must not be invented from photographs. Separate intentional
context from useful evidence the vocabulary cannot express. Prompt edits remain owner-only.

### F5 — Shared confidence policy differs from several examples

Status: VERIFIED for static ceilings and displayed bands; intended interpretation needs review.

[Evidence hierarchy](../../src/dendro_inspector/knowledge/evidence_hierarchy.py) reserves
95–100 for high confidence at fruit/seed tier. Foliage tops out at 85–94, prepared wood at
70–84, and ordinary bark at 50–69. Besides birch:

- Section 6 includes maple with clear palmate leaves among 95–100 examples; foliage alone
  cannot reach that band in code.
- Pinus permits a high assessment of a log pile from combined bark/conifer-cut evidence;
  prepared wood still tops out at 70–84. The intended meaning of "high" needs resolution.
- Apple with fruit is described as 98–100; the highest displayed code interval is 95–100.
  Ordinal bands are an explicit implementation choice, not exact numeric equivalence.
- Silver maple/white poplar without underside and arrangement is capped at 80–85 in prose;
  the requirement cap uses 70–84 when it applies. The silver-maple species-level 95+
  caution also names samaras, absent from its own positive rules and Boolean requirement.

These are not instructions to inflate confidence. Resolve ambiguous conjunctions and
specific examples against general caution, and test the chosen interpretation.

### F6 — Tilia's fruit evidence is retained but weakly weighted

Status: VERIFIED for P10; domain weighting remains an owner decision.

[Tilia](../../knowledge/taxa/tilia.yaml) marks serrated heart-shaped leaves strong and
bracted nutlets supporting. Clear attached `nutlet_with_bract` evidence admits Tilia and
satisfies its fruit-or-leaf requirement but receives weak support because no strong
positive matches. This merits review against the prompt's general fruit-first hierarchy.
Section 14 does not explicitly assign a strength to the nutlets, so this is a weighting
question, not a proven botanical misclassification.

### F7 — Prompt provenance does not cover all knowledge

Status: VERIFIED for recorded provenance; external correctness UNKNOWN.

Abies and Larix explicitly have inferred, unreviewed provenance and no prompt description.
They cannot receive a prompt-conformance pass. Pinus's cone-umbo rule needs independent
provenance or specification review. The conifer comparison also includes Larix characters
while citing sections that never describe Larix.

The Betula comment names external references without a feature-specific provenance record.
This review did not verify those references. Their names do not attest the exact one-band
confidence exception. Distinguish direct prompt statements, implementation choices and
independently sourced botanical additions.

## Per-taxon comparison

Counts describe annotated positives, not accuracy. "No additional mismatch reproduced"
means this bounded inspection found none beyond shared limits, not botanical certification.
Prompt line numbers refer to the sealed file identified above.

### Quercus — oak

[Card](../../knowledge/taxa/quercus.yaml); prompt lines 374–395, plus sections 13, 15 and
17. **Annotated positives: 7/7.**

Deep longitudinal fissures, armour-like plates, simple lobed leaves, acorns, ring porosity,
prominent rays and yellowish-brown wood match. Leaves/acorns are strong; bark/wood support.
Identity is genus-only. Leaf, acorn and prepared end-grain follow-ups are appropriate.

Grey-brown colour, weathering and heterogeneous old bark remain prose rather than matching
rules. There is no strong bark path to trigger F1. P01 retains acorn-supported oak with
atypical smooth-grey bark, verifying that packet only. Section 15's large earlywood pores
have no separate oak token. The public rough-bark case protects the user's claim from
rejection but expects "possible", while section 17's old-oak example says "accepted" with
field context; that is a narrower acceptance policy requiring interpretation.

### Betula — birch

[Card](../../knowledge/taxa/betula.yaml); prompt lines 426–440 and sections 6, 13 and 17.
**Annotated positives: 4/4.**

White papery bark with black marks is strong and diagnostic. Thin peeling layers,
horizontal lenticels and the serrated triangular-leaf token support. Genus-only scope,
white-poplar confusion and bark/leaf/twig follow-ups preserve the intended distinction
between recognizing the genus and establishing an exact species.

The old dark cracked base appears only in a comment. P02/P03 reproduce F1: adding a second
compatible trunk-pattern encoding removes birch, while an extra texture encoding does not.
F2 records the 70–84 ceiling despite the high-confidence example. F4 records the reduction
of oval/triangular/rhombic leaves to one triangular token. Neither the diagnostic exemption
nor these findings justify high confidence from a distant pale trunk.

### Pinus — pine

[Card](../../knowledge/taxa/pinus.yaml); prompt lines 345–361 and sections 15 and 17.
**Annotated positives: 7/7; one extra positive.**

Counted fascicles, scaly plates, long straight trunks, honey/light-yellow wood and resin
match. The extra strong `cones.scale_shape = woody_umbo` is absent from the cited paragraph
(F7). Reddish-orange bark, clear rings and nonporous conifer structure have no distinct
positive rules. The generic needle sentence does not establish fascicle counts (F4).

The genus-only card cannot produce the suggested Pinus sylvestris species identity in
section 17. Its high-confidence requirement asks for needles or cones; combined wood-only
evidence has the prepared-wood ceiling (F5). Needle, cone and end-grain requests are
relevant. Abies/Larix confusions are additions beyond this prompt description.

### Picea — spruce

[Card](../../knowledge/taxa/picea.yaml); prompt lines 362–373.
**Annotated positives: 4/4.**

Woody-peg attachment is strong; conical crown, fine bark scales and light wood support.
Short/dense needles are mapped to a different attachment character (F4). Layered/hanging
branches and relative lack of reddish bark remain prose-only.

Genus scope and attachment/cone/twig-surface requests are present. The needles-or-cones
requirement has no Picea-specific `cones.*` positive expectation behind its cone branch.
Requesting a cone photograph does not establish that a discriminating cone character can
already match the card. The strong attachment rule needs an actual reading of attachment.

### Fagus — beech

[Card](../../knowledge/taxa/fagus.yaml); prompt lines 396–413.
**Annotated positives: 5/5.**

Smooth-grey bark and oval entire leaves with wavy margins are strong. Straight cylindrical
trunk, cream/pinkish wood and diffuse porosity support; ring porosity contradicts.
Carpinus confusion and leaf/trunk/end-grain requests match the comparison task.

The prompt expressly allows less-smooth bark on old or damaged trees. P04 shows that
slightly cracked smooth-grey bark removes Fagus despite a matching leaf: F1 loses the
allowance. Smooth bark alone still receives the ordinary bark ceiling and genus limit.
The finding does not establish arbitrary rough bark as positive beech evidence.

### Carpinus — hornbeam

[Card](../../knowledge/taxa/carpinus.yaml); prompt lines 414–425.
**Annotated positives: 4/4.**

Fluted muscular trunk is strong; smooth/slightly cracked grey bark, serrated oval leaves
with prominent veins and very pale wood support. Fagus confusion and full-trunk, leaf
and prepared end-grain requests are relevant.

Hardness, density and splitting resistance have no positive feature rules; the prompt's
opening restrictions prohibit inventing physical measurements from an image. Trunk form
is silhouette-tier, so a strong card label does not yield a trunk-only species or high
confidence. The leaf-or-trunk requirement retains the stated evidence choices, subject
to shared ceilings. No additional Carpinus-specific mismatch was reproduced.

### Populus — poplar

[Card](../../knowledge/taxa/populus.yaml); prompt lines 441–457.
**Annotated positives: 6/7 on this card; the remaining token is on Populus alba.**

Massive trunk, urban/park context, coarse furrowed bark and triangular/rounded/cordate
leaves match. The pale-upper/dark-base pattern is assigned to white poplar, the subject
of that sentence. Mistletoe and the prose's lobed-leaf option are not genus positives.

P07 demonstrates F1: a white-poplar lobed-leaf value vetoes the genus despite another
matching leaf. The paragraph expressly permits lobed leaves. Genus scope, related poplar
and rough-bark confusions, and leaf/attachment/crown requests are present. The leaf-or-fruit
requirement has no Populus-specific fruit expectation; useful fruit characters need review
rather than merely making a globally known selector satisfiable.

### Populus alba — white poplar

[Card](../../knowledge/taxa/populus_alba.yaml); prompt lines 451–457 and 505–514.
**Annotated positives: 4/4 for the specific comparison description.**

White-felted underside is strong. Rounded/triangular lobed leaves, alternate arrangement
and pale-young/dark-old bark support. Opposite arrangement contradicts. The underside-and-
arrangement requirement and corresponding photographs preserve discrimination from silver
maple and birch.

The species card can broaden to Populus. Generic Populus retrieval has F1's separate issue;
this does not prove final broadening from the species card is impossible. The comparison's
80–85 wording and code bands differ (F5). Bark alone cannot carry this species identity.
No additional unique mismatch was reproduced.

### Populus tremula — aspen

[Card](../../knowledge/taxa/populus_tremula.yaml); prompt lines 537–547.
**Annotated positives: 4/4.**

Long flattened petiole is strong. Rounded leaves, young grey-green smooth bark and pale
wood support. Old bark becoming dark/cracked remains prose-only, but young bark is
supporting rather than strong, so its absence is not this card's F1 veto.

Species and broader identities are available. Petiole/leaf/bark requests and the leaf
requirement correspond to the diagnostic organ. `pale_soft` combines visible tone with
physical softness; softness must not be asserted from colour alone. Foliage remains
subject to the shared resolution ceiling. No additional aspen admission failure was
reproduced.

### Acer — maple

[Card](../../knowledge/taxa/acer.yaml); prompt lines 458–469 and sections 6 and 17.
**Annotated positives: 3/3.**

Palmate-lobed leaves and paired samaras are strong; opposite arrangement supports and
alternate arrangement contradicts. Genus scope and leaf/attachment/samara requests match.
Typical lobe count and rough old bark remain prose-only.

P05 reproduces F1: paired samaras do not preserve generic Acer when the leaf carries the
compatible sycamore-specific shape token. Resolve descriptive specificity without ignoring
actually contradictory leaves. The clear-leaf 95–100 example also exceeds the foliage
ceiling (F5).

### Acer saccharinum — silver maple

[Card](../../knowledge/taxa/acer_saccharinum.yaml); prompt lines 470–481 and 498–514.
**Annotated positives: 4/4 across the description and comparison.**

Deep narrow-lobe dissection is strong. Pale nonfelted underside, opposite arrangement and
urban/park context support; alternate arrangement and white felt contradict. Pointed lobes
and serration are compressed into the dissection description. Rough grey-brown bark has
no expectation.

The card requires underside and arrangement and requests their photos plus samaras. It
has no samara-positive rule although the species-level 95+ caution names samaras alongside
the other details (F5). A global fruit-tier ceiling does not supply candidate-specific
fruit support. Resolve the intended conjunction before changing requirements or caps.

### Acer pseudoplatanus — sycamore maple

[Card](../../knowledge/taxa/acer_pseudoplatanus.yaml); prompt lines 482–496.
**Annotated positives: 5/5.**

Broad palmate five-lobed leaves are strong. Opposite arrangement, paired samaras, irregular
plate peeling and mosaic bark support. Alternate arrangement contradicts. Leaf, attachment
and bark requests match; shared rules require attachment before detachable evidence can
support the analyzed tree.

Old bark remains capped and cannot establish this species. Its exact leaf token vetoes
generic Acer in P05; generic palmate wording is not automatically normalized to this
species-specific token either. Only the genus direction was probed here. Preserve useful
genus fallback while resolving compatible descriptive specificity.

### Tilia — lime/linden

[Card](../../knowledge/taxa/tilia.yaml); prompt lines 515–525.
**Annotated positives: 4/4.**

Serrated heart-shaped leaves are strong. Dense crown, grey-brown fissured bark and nutlets
with bracts support. Genus scope, Populus confusion and leaf/fruit/attachment requests
are represented.

P10 verifies F6: a bracted nutlet alone admits Tilia but receives weak support despite
satisfying the fruit-or-leaf requirement. Whether it should be decisive is a domain
weighting decision. Heart-shaped wording at different specificity faces the shared F1
boundary, but no extra Tilia-specific veto was probed. The 4/4 count does not attest
agreement on evidence strength.

### Alnus — alder

[Card](../../knowledge/taxa/alnus.yaml); prompt lines 526–536.
**Annotated positives: 4/4.**

Small persistent woody cones are strong. Rounded leaves with a blunt/notched apex, dark
platy bark and wet-site context support. The oval-leaf alternative and cracked-bark wording
are compressed into the selected values.

The cones-or-leaf requirement, genus scope and cone/leaf/site requests are present. Wet
site alone cannot admit a taxon under shared trust projection, keeping habitat preference
from becoming identification. Oak and poplar are confusions. No additional alder-specific
behavioral failure was reproduced.

### Juglans regia — walnut

[Card](../../knowledge/taxa/juglans_regia.yaml); prompt lines 548–562.
**Annotated positives: 5/5.**

A nut and compound pinnate leaves with large leaflets are strong. Old grey furrowed bark,
spreading garden crown and darker heartwood support. Oval leaflet detail is compressed
into the leaf token. Nut/leaf requirements and leaf-attachment/nut/end-grain requests are
present; bark alone cannot establish the species.

P08 verifies F1: a matching nut does not save the candidate from less-specific
`compound_pinnate` wording. That describes compatible structure without attesting finer
leaflet detail. Missing detail must be distinguished from affirmative contradiction.
Ash, oak and Robinia are retained confusions.

### Robinia pseudoacacia — black locust

[Card](../../knowledge/taxa/robinia_pseudoacacia.yaml); prompt lines 563–576 and section 15.
**Annotated positives: 5/5.**

Pods and deep rope-like bark furrows are strong. Compound pinnate leaves with rounded
leaflets, possible thorns and greenish-gold heartwood support. White flowers in clusters
are prose-only; the yellowish/golden wood variation is compressed into `greenish_gold`.

The pod-or-leaf requirement and its photo requests exist. Thorn absence is not an explicit
contradiction, consistent with the prompt's "possible" qualification. A merely yellow cut
must not identify this species, and colour has reduced trust. The strong bark texture is
subject to F1 if described at another specificity, but that Robinia case was not
independently reproduced. Bark alone cannot carry species resolution or high confidence.

### Fraxinus — ash

[Card](../../knowledge/taxa/fraxinus.yaml); prompt lines 577–590.
**Annotated positives: 5/5.**

Compound pinnate leaves and opposite arrangement are strong. Diamond fissures, pale wood
and ring porosity support; alternate arrangement contradicts. Grey bark is prose-only.
The shared attachment rule implements the prompt's caution against reading unrelated
background foliage as ash evidence.

Genus scope and leaf/attachment/end-grain follow-ups are present. The requirement is
leaf-or-samara, but neither this paragraph nor this card defines an ash samara token. The
selector can be satisfied by a globally known feature without a matching ash fruit
character. Its utility and provenance need clarification; this is not permission to treat
maple samaras as ash support. No additional ash-specific behavioral failure was reproduced.

### Malus — apple

[Card](../../knowledge/taxa/malus.yaml); prompt lines 591–605 and section 17.
**Annotated positives: 5/5.**

Apple fruit is strong. Crooked low branching, grey-brown scaly bark, oval serrated leaves
and garden context support. Pear and Prunus confusions and fruit/leaf/crown requests are
present. Bark and garden context cannot replace a visible apple as decisive evidence.

The fruit-or-leaf requirement preserves a leaf-supported path while fruit remains the only
strong card feature. The explicit 98–100 fruit example becomes the shared 95–100 display
band when high confidence is reached (F5). That is a numeric representation difference,
not evidence that a model received or recognized an apple. No additional card-description
omission was found among the annotated positives.

### Pyrus — pear

[Card](../../knowledge/taxa/pyrus.yaml); prompt lines 606–617.
**Annotated positives: 4/4.**

Pear fruit is strong. Dark small-block bark, oval glossy leaves and vertical branches
support. Garden context is prose-only. The paragraph's leaves are "often" glossy while the
selected token combines oval shape and gloss; because it is supporting, an unglossy reading
does not itself trigger a veto — only strong-positive paths do (F1).

The genus claim, apple/Prunus confusion and fruit/leaf/bark requests preserve the intended
caution without fruit. Fruit-or-leaf is the high-confidence requirement. No Pyrus-specific
behavior difference was reproduced; F1 remains a shared risk for generic versus specific
fruit encodings, not a tested pear finding here.

### Prunus — stone-fruit group

[Card](../../knowledge/taxa/prunus.yaml); prompt lines 618–638.
**Annotated positives: 5/5.**

A drupe is strong. Oval/elongated serrated leaves, horizontal lenticels, warm-toned
heartwood and garden/roadside context support. The prose's broader colour range is
compressed into `warm_yellow_orange`. Genus and species-group resolutions are available;
fruit/leaf/end-grain requests match the stated need for more detail between close taxa.

P06 verifies that the generic drupe rule is treated as exclusive and rejects a packet that
also names an apricot; P12 shows the inverse for the apricot card. A more specific fruit
description needs review as compatible specificity, not assumed mutual exclusion because
the string differs. The paragraph also lists plum, blackthorn and sour cherry without
dedicated cards; that is a resolution limit, not an absence of generic Prunus.

### Prunus cerasifera — cherry plum

[Card](../../knowledge/taxa/prunus_cerasifera.yaml); prompt lines 639–651.
**Annotated positives: 5/5.**

Small round drupes are strong. Small garden trunk form, warm heartwood, narrow pale sapwood
and grey-brown cracked bark support. The courtyard setting and the full colour range are
compressed or left as context. Fruit, end-grain and leaf requests are present.

The prompt says not to claim 100 without fruit. The card makes `fruit.type` a requirement
for high confidence at its own resolution — a broader conservative restriction than the
literal 100-point prohibition. Close Prunus confusions are named. Under F1 the general and
specific drupe tokens need the same review as apricot; the cherry-plum variant was not
separately run. No species claim is justified by warm wood colour alone.

### Prunus avium — sweet cherry

[Card](../../knowledge/taxa/prunus_avium.yaml); prompt lines 652–662.
**Annotated positives: 4/4, but the unannotated fruit sentence is not represented.**

Prominent horizontal lenticels are strong. Grey/reddish banded bark, old bark peeling in
plates and oval serrated leaves support. The card asks for fruit, bark and leaf photographs
and requires fruit-or-leaf.

F3 is the material gap: the prompt calls fruit strong evidence, yet no fruit rule can
support this card. P09 confirms that generic drupe evidence neither retrieves nor admits
it. A species-specific fruit description is also absent from the card's vocabulary. Define
what should distinguish the fruit before adding a rule. Prominent lenticels alone remain
bark-tier and cannot license a species claim despite the card's strong label.

### Prunus armeniaca — apricot

[Card](../../knowledge/taxa/prunus_armeniaca.yaml); prompt lines 663–673.
**Annotated positives: 4/4.**

Apricot fruit is strong. Old dark cracked bark, rounded oval leaves and garden context
support. Prunus and cherry plum are confusions. The card requires fruit and requests fruit,
leaf and bark photographs, preserving caution among similar fruitless trees.

P12 verifies F1: generic drupe wording vetoes the species despite a matching apricot
observation in the same packet. The symmetric genus failure is P06. The prompt calls fruit
strong but does not prescribe this exact Boolean requirement or score band; those remain
derived policy decisions to document and test.

### Morus — mulberry

[Card](../../knowledge/taxa/morus.yaml); prompt lines 674–685.
**Annotated positives: 4/4.**

Elongated berry-like fruit is strong. Entire and lobed leaves on the same tree, coarse old
bark and yellowish wood support. The variability token explicitly preserves mixed leaf
forms, unlike the simpler shape enumerations flagged in F4.

The prompt says yellow wood alone proves nothing; P11 confirms a colour-only packet cannot
admit Morus. Genus scope, maple/Prunus/Robinia confusions and fruit/leaf/end-grain requests
are present. A variability observation requires actually observing the stated variation,
not inferring it from one leaf. No additional unique mismatch was reproduced.

### Larix — larch

[Card](../../knowledge/taxa/larix.yaml). **No domain-prompt description; token comparison
not applicable.**

The card makes clustered needles on short shoots and deciduous needles strong; prominent
short shoots and small upright cones support, and pine fascicles contradict. It permits
genus and requests short shoots, cones and winter twigs. Its header and provenance state
that this is inferred demonstration content outside the prompt.

There is consequently no prompt-derived description to verify against. The conifer
comparison's broader prompt-provenance claim needs the distinction in F7. The header's
"other 24 cards" wording is stale at 26 current cards — a documentation count issue, not a
botanical finding. External correctness and appropriate confidence remain UNKNOWN.

### Abies — fir

[Card](../../knowledge/taxa/abies.yaml). **No domain-prompt description; token comparison
not applicable.**

Disc-base needle attachment and upright disintegrating cones are strong. Surface scars,
resin blisters, smooth/fine-scaled bark, edge-lifting flakes, flat two-ranked needles and
a conical crown support; woody-peg attachment, pine fascicles and deciduous needles
contradict. The card permits genus and requests attachment, cones and twig undersides. Its
inferred, unreviewed provenance acknowledges its absence from the prompt.

This review neither validates those added botanical characters nor treats missing prompt
coverage as evidence they are false. F7 requires their independent provenance and review.
Under [AGENTS.md](../../AGENTS.md) section 16, benchmark-motivated additions must carry
independent justification; a later run cannot retroactively supply that source.

## Offline behavioral evidence

All packets below are synthetic. Each observation is clear, image-sourced, same-subject,
with confirmed attachment where required and a prepared surface where needed. The probes
exercise deterministic retrieval and admission, not a model's ability to observe the
features. The proposal score is moderate, so no probe manufactures a high-confidence
model claim.

| Probe | Taxon and supplied observations | Retrieved / admitted | Interpretation |
| --- | --- | --- | --- |
| P01 | Oak acorn + smooth-grey bark | yes / yes | Atypical bark does not veto this oak packet |
| P02 | Birch diagnostic pattern + pale-upper/dark-base pattern | no / no | F1; conformance expectation fails |
| P03 | Birch diagnostic pattern + deep-fissure texture | yes / yes | Controls for the feature-path dependence |
| P04 | Beech leaf + slightly cracked smooth-grey bark | no / no | F1; allowed age/damage variation needs representation |
| P05 | Acer paired samaras + sycamore leaf shape | no / no | F1; compatible description specificity |
| P06 | Prunus drupe + apricot fruit description | no / no | F1; generic taxon removed |
| P07 | Populus rounded leaf + white-poplar lobed leaf | no / no | F1; generic paragraph permits lobed leaves |
| P08 | Walnut nut + generic compound-pinnate leaf | no / no | F1; missing finer detail treated as disagreement |
| P09 | Sweet cherry with generic drupe only | no / no | No fruit match on its card; not a species-identification oracle |
| P10 | Tilia bracted nutlet only | yes / yes | Weak support; high-confidence-support predicate false |
| P11 | Morus yellowish wood only | no / no | Colour-only restraint retained |
| P12 | Apricot fruit + generic drupe description | no / no | F1; species taxon removed |

The table is the measurement at revision `7d33436`, before anything in this review was
acted on. After F1 was fixed, the seven rows that read `no / no` for an F1 reason read
`yes / yes`: P02, P04–P08 and P12. P09 and P11 are unchanged, and remain the negative
controls that show the fix did not simply stop rejecting things. Running the block below
reproduces the current numbers, not this table.

For P02, P04–P08 and P12 the card's `high_confidence_supported` predicate is true while
the separate self-contradiction rule removes the candidate. That is not a final
high-confidence verdict: it shows that the matching-positive and veto predicates answer
different questions.

### Reproduce the probes

Save the block below and run it from the repository root with the project virtual
environment, with the repository root importable, because it reuses the existing test
helper constructors alongside production matching code:

```text
PYTHONPATH=. .venv/Scripts/python.exe -X utf8 <saved-block>.py
```

It performs no model calls, changes no cards, and contains no photograph or benchmark
output. The `--conformance` option intentionally fails on the currently rejected
compatible cases; without it the block prints observed behavior for inspection.

```python
from pathlib import Path
import sys

from dendro_inspector.config import KnowledgeConfig
from dendro_inspector.knowledge.loader import KnowledgeBase
from dendro_inspector.knowledge.candidate_validation import (
    cards_in_play,
    validate_candidate_set_with_report,
)
from dendro_inspector.knowledge.taxon_cards import match_card
from dendro_inspector.schemas.candidates import CandidateSet
from tests.unit.test_candidate_validation import _obs, _packet, _candidate

kb = KnowledgeBase(KnowledgeConfig(), root=Path.cwd())
probes = [
    ("P01", "quercus", [("acorn.presence", "present"), ("bark.texture", "smooth_grey")]),
    (
        "P02",
        "betula",
        [
            ("bark.pattern", "white_papery_with_black_marks"),
            ("bark.pattern", "pale_upper_dark_rough_base"),
        ],
    ),
    (
        "P03",
        "betula",
        [
            ("bark.pattern", "white_papery_with_black_marks"),
            ("bark.texture", "deep_longitudinal_fissures"),
        ],
    ),
    (
        "P04",
        "fagus",
        [
            ("leaf.shape", "oval_entire_wavy_margin"),
            ("bark.texture", "smooth_grey_slightly_cracked"),
        ],
    ),
    ("P05", "acer", [("samara.presence", "paired"), ("leaf.shape", "broad_palmate_five_lobed")]),
    ("P06", "prunus", [("fruit.type", "drupe"), ("fruit.type", "apricot")]),
    ("P07", "populus", [("leaf.shape", "rounded"), ("leaf.shape", "rounded_or_triangular_lobed")]),
    ("P08", "juglans_regia", [("nut.presence", "present"), ("leaf.type", "compound_pinnate")]),
    ("P09", "prunus_avium", [("fruit.type", "drupe")]),
    ("P10", "tilia", [("fruit.type", "nutlet_with_bract")]),
    ("P11", "morus", [("wood.tone", "yellowish")]),
    ("P12", "prunus_armeniaca", [("fruit.type", "apricot"), ("fruit.type", "drupe")]),
]
compatible = {"P01", "P02", "P03", "P04", "P05", "P06", "P07", "P08", "P12"}
failures = []
for label, taxon, pairs in probes:
    observations = [_obs(f"o{i}", feature, value) for i, (feature, value) in enumerate(pairs)]
    packet = _packet(*observations)
    proposed = CandidateSet(
        subject_id="log_1",
        candidates=(_candidate(taxon, 1, *[o.observation_id for o in observations]),),
    )
    result = validate_candidate_set_with_report(proposed, packet, kb)
    match = match_card(kb.taxon(taxon), packet, "log_1")
    admitted = bool(result.candidate_set.candidates)
    print(
        label,
        taxon,
        "admitted=",
        admitted,
        "in_play=",
        taxon in cards_in_play(packet, kb, ("log_1",)),
        "self_conflict=",
        match.self_contradiction_hits,
        "high_confidence_supported=",
        match.high_confidence_supported,
        "score=",
        result.candidate_set.leader.score if admitted else None,
    )
    if label in compatible and not admitted:
        failures.append(label)
if "--conformance" in sys.argv:
    assert not failures, f"Compatible descriptions rejected: {failures}"
```

### Validation record

The following checks were executed for this review:

- The prompt's current bytes hash to the SHA-256 recorded above, which is the value
  sealed in [`prompts/versions.yaml`](../../prompts/versions.yaml).
- A per-card token comparison over the 24 section-14 paragraphs reproduced every count
  printed in the per-taxon entries, found no annotated token absent from the pack, and
  identified exactly two differences: Pinus's extra strong `cones.scale_shape` and the
  Populus token carried by the Populus alba card.
- Twelve deterministic probes produced the table above, matching it row for row.
- The section-14 token-union contract and the diagnostic-birch one-band regression both
  pass: `2 passed`.
- The broader selection across domain-prompt, vocabulary, data and retrieval contracts,
  candidate validation, evidence hierarchy, live-model regressions and final decisions
  reported **284 passed, 1 xfailed**:

```text
.venv/Scripts/python.exe -X utf8 -m pytest -o addopts= --strict-markers -q -p no:cacheprovider --tb=short tests/contract/test_domain_prompt_contract.py tests/contract/test_vocabulary_contract.py tests/contract/test_data_contract.py tests/contract/test_retrieval_contract.py tests/unit/test_candidate_validation.py tests/unit/test_evidence_hierarchy.py tests/unit/test_live_model_regressions.py tests/unit/test_final_decision.py
```

Status: VERIFIED for the source comparisons, the token counts, the probes and the tests
named above. A passing implementation test cannot overrule a demonstrated specification
mismatch, and passing tests here mean the current rules behave as written — not that the
rules match the prompt. UNKNOWN remains the status of field accuracy, of how often a model
emits each encoding, and of the external botanical correctness of Abies and Larix. No
photograph, reference label or live provider was used, so this document makes no accuracy
claim.

## Decision order

1. **Done.** F1 was resolved before any individual card was tuned, because regional and age
   variation and compatible general/specific descriptions were one shared semantic problem
   — nine of the twelve probes moved without a single card changing. What is left is
   content, not semantics: extend `knowledge/vocabulary.yaml` when a further pair of
   readings is shown to describe one organ, with the prompt line that says so.
2. Define sweet-cherry fruit evidence (F3) and review the other requested-but-unrepresented
   organs — ash samaras, Picea cones, Populus fruit — without assuming that a requested
   photograph already has a matching card rule.
3. Agree the confidence exceptions and numeric-band interpretations (F2, F5), including
   birch, maple, apple and wood-only pine. Keep the conservative limits until an explicit
   decision exists.
4. Audit annotation fidelity, positive/negative strength and separate provenance (F4, F6,
   F7). Keep intentional context-only omissions distinct from missing diagnostic evidence.
5. Apply accepted changes at their canonical source, update the contracts and regressions
   with them, and run the required gates. Prompt changes remain owner-only; this review
   changed no prompt, card, implementation, threshold, dependency or benchmark asset.
