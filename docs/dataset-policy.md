# Dataset and content policy

- **Status:** Current
- **Owner:** Dendro Inspector maintainers
- **Date:** 2026-11-11
- **Last-verified:** 2026-11-11

This is a **public repository**. Everything committed is world-readable, permanently
archived by third parties, and mirrored within minutes. Deleting a commit does not unpublish
it.

## Knowledge cards record their review status

Draft and demonstration cards carry `placeholder_content: true`. A promoted taxon card
carries `false`, with a named, dated review of its rules and taxonomic identities. The
data-contract gate checks these review records instead of requiring every card to remain
a placeholder forever.

All 27 taxon cards received an **AI-assisted botanical and implementation review** on
2026-11-11, using general botanical knowledge, the domain prompt and the source descriptions
recorded in their files. Their rules, contradictions, broader identities and evidence
requirements were checked with synthetic regression cases. Their provenance confidence is
`medium`; their placeholder flags are `false`. Rules added from general botanical
knowledge say so in their own provenance; retrieved sources are quoted where used. This records a local review, not independent dendrologist certification or
validation of accuracy on real photographs. Comparison and regional cards remain unreviewed.

The flag is load-bearing: the response composer appends a placeholder warning to any answer
backed by a card carrying it. Removing it requires a review with an explicit scope; passing
software tests alone does not establish botanical accuracy.

### Provenance is mandatory

Every card carries a `provenance` block. **It is scoped to the card's feature rules** —
`strong_positive_features`, `supporting_features`, `contradictions` and the thresholds
around them — and any individual feature rule may override it. An unreviewed draft starts
with conservative metadata:

```yaml
provenance:
  source: "Domain prompt section 14 (БАЗОВІ ПОРОДИ)"
  source_type: domain_prompt      # field_guide | literature | expert_review | inferred
  region: Eastern Europe
  life_stage: any                 # young | mature | old
  season: any                     # deciduous characters do not survive January
  confidence: low
  review_state: unreviewed        # reviewed | disputed
  reviewed_by: null
  last_reviewed: null
```

This does not make a rule true. It makes a rule **attributable** — which is the only thing
that scales. The failure it guards against is quiet and expensive: someone adds a plausible,
well-written, wrong feature; every test still passes, because the tests check the code and
not the botany. With provenance, a later reviewer can list every rule nobody has ever
verified. Without it, that list cannot be produced at all.

`provenance.confidence` describes confidence in the attributed knowledge. It is not the
feature's diagnostic strength or an image-identification confidence cap. The reviewed
taxon descriptions warrant `medium` source confidence within this local
review's scope. Shared features such as bark can have that source confidence while remaining
supporting evidence. Decision confidence still comes from observed evidence, attachment,
contradictions and the deterministic ceilings. A regression test varies source confidence
and review metadata while asserting the same identification decision.

Card strength does not override the evidence hierarchy. A paired-spine match still carries
the branch feature's silhouette ceiling, and bud colour still carries the colour cap.
Do not declare an executable positive feature in a family with no defined authority or
attachment contract. The Robinia flower rule is deferred for that reason.

Keep diagnostic readings distinct from compatible ones. Robinia's detailed pod token
explicitly records flatness, oblong shape, a smooth surface and a resolved 3-8 cm length;
the generic shape remains supporting. This is an extractor's observation, not a
measurement independently made by code. Fraxinus's opposite leaves and black buds remain
supporting: the former overlaps the documented Acer negundo gap, and the latter is not
universal across the genus. These distinctions are exercised on synthetic packets in
[`test_robinia_fraxinus_cards.py`](../tests/unit/test_robinia_fraxinus_cards.py).

The contract enforces one rule: `review_state: reviewed` requires both `reviewed_by` and
`last_reviewed`. A card cannot claim review without saying who and when.

### Taxonomic placement is attributed separately

`broader_identities` are not feature rules. They are taxonomic claims the system puts in
front of a user whenever an answer is broadened, and they usually come from somewhere other
than the features do — so each one carries its own `provenance`:

```yaml
broader_identities:
  - resolution: genus
    taxon_id: acer
    display_name: Acer (клен)
    provenance:
      source: "Domain prompt section 14 (БАЗОВІ ПОРОДИ)"
      source_type: domain_prompt
  - resolution: family
    taxon_id: sapindaceae
    display_name: Sapindaceae (сапіндові)
    provenance:
      source: "Standard botanical taxonomy; the domain prompt names no family"
      source_type: inferred
```

The distinction is not pedantry. The domain prompt names genera and species; it names **no
family at all**. Letting a family entry inherit the card's `source_type: domain_prompt`
would cite a section that does not contain it — and *Quercus* being in Fagaceae is exactly
the kind of uncontroversial statement that makes a false citation easy to miss.

The contract enforces three rules: every identity carries provenance, no family-resolution
identity claims `domain_prompt`, and the same broader identity declared by several cards
carries the same provenance. An identity that omits the block inherits the card's, which is
honest only when the card's own source names it.

### Promoting a card

1. review the features, contradictions, taxonomic identities and high-confidence
   requirements against the stated sources and general botanical knowledge; describe the
   review's scope and exercise the resulting rules on synthetic cases;
2. set `review_state: reviewed` with `reviewed_by` and `last_reviewed` on the card and its
   explicit provenance overrides; identify AI-assisted reviews as such;
3. set `placeholder_content: false`;
4. check the comparison cards that mention it and update any conflicting descriptions.

The existing local status for this is `reviewed` / `local_reviewed`. Do not use
`externally_verified` for a local review; that status belongs to authoritative external
retrieval. Review does not raise identification ceilings.

### Finding the gaps a card review should fill

Candidate validation matches evidence to cards by exact `(feature, value)` equality, so any
observation outside the cards' combined vocabulary is silently unusable no matter how good
the photograph was. Every run now measures this: the evidence-quality report carries
`unmatchable_evidence_ids`, and the quality gate logs `evidence_outside_card_vocabulary`.
The diagnostic splits those ids into existing-policy weak evidence (colour and explicit
insufficient-alone features) and potential card coverage gaps; the latter are further split
between absent feature paths and unknown values for known paths.

Read only the potential-gap bucket as a **card coverage** signal, not a model failure. The
weak bucket is intentionally tracked separately so a frequent honest colour observation does
not make the actionable knowledge gap look larger than it is. The extractor is deliberately
told to prefer an honest out-of-vocabulary observation over a forced in-vocabulary one, so a
recurring feature in that log is a request for a card rule, addressed to someone qualified
to write one. On the first live nine-photograph run it was 30% of all extracted
observations, led by `bark.colour` — a feature the conifer comparison card already names
under `insufficient_features` while no taxon card mentions it at all.

Two cautions before acting on it:

- **A missing feature is not automatically a missing rule.** That run recorded
  `needles.shape = short_linear_flattened` against a `picea` candidate. Adding it to the
  Picea card would have been wrong in the direction that matters — flattened needles point
  away from spruce — and the deterministic rejection was right for a mechanical reason.
- **Context-tier features can never support an identification** whatever the cards say, so
  they are excluded from the count. Adding `context.site` values changes nothing about
  support; they matter only as contradictions. `bark.epiphytes` (moss, lichen) and
  `trunk.life_stage` are counted as non-diagnostic context for the same reason
  (`NON_DIAGNOSTIC_CONTEXT` in `knowledge/comparison_cards.py`).

### Closed bark and trunk vocabulary

[`knowledge/value_sets.yaml`](../knowledge/value_sets.yaml) closes the value lists for
`bark.texture`, `bark.pattern`, `bark.surface_marks`, `bark.peeling`, `trunk.form`,
`trunk.burls` and `trunk.life_stage`. These paths produced most of the unusable evidence in
the stored golden-100 runs. The extractor receives each list with a plain description per
value and must choose from it or report the feature unreadable; descriptions never name a
taxon. A card may only use a listed value on these paths, and a test enforces that. To add
a bark reading, add it to the list first, with a description, then to the card.

### Age-specific rules

Bark changes with age. A rule may set `life_stage: young | mature | old`. Positive matching
is unchanged; the stage only limits disagreement. A strong rule or contradiction tied to one
stage vetoes or contradicts only when the extractor read that `trunk.life_stage`, so old
furrowed bark does not count against a card whose decisive bark is young and smooth. Leave
`life_stage` at its default `any` for a reading that holds at every age (beech bark).

## Photographs

**Do not commit photographs of trees to this repository.**

- Most photographs are someone else's copyright, and "found on the internet" is not a
  licence.
- Image files carry EXIF: GPS coordinates, timestamps, camera serial numbers. A tree photo
  can disclose where a person was on a particular afternoon.
- Binary blobs in git history are permanent and cannot be pruned without rewriting history
  for everyone.

`examples/*.jpg|jpeg|png|webp` is git-ignored. Put your own photographs there locally.

Fake mode does not need a real file — the fixture supplies the evidence, and a missing file
is recorded as a limitation rather than crashing the run.

## Evaluation material

| Location | Committed? | Contains |
| --- | --- | --- |
| `evals/public/` | yes | Case declarations. Synthetic input, no real photographs |
| `evals/fixtures/` | yes | Recorded provider responses. Hand-authored, synthetic |
| `evals/golden/` | **no** (git-ignored) | Your private material with real photographs |

Fixtures are hand-written, not captured from live model calls. Captured output can contain
anything the model happened to say, including reconstructed fragments of its input.
General botanical knowledge and recorded source descriptions can inform synthetic cases;
golden photographs and their expected answers must not inform card authoring.

## Personal data

Never commit:

- real names, email addresses, phone numbers;
- GPS coordinates or addresses precise enough to locate a person or a property;
- user-submitted photographs, text or metadata from any real deployment;
- API keys, tokens, connection strings, `.env` files.

Use synthetic values: `user@example.com`, region-level locations only ("Kyiv Oblast,
Ukraine", not a street), obviously fake identifiers. A contract test checks fixtures for
email addresses outside `example.com`.

If a secret is committed, treat it as compromised the moment it is pushed. **Rotate first,
scrub second.** History rewriting is cleanup, never remediation.

## Location data in the running system

The graph accepts `location` as free text and uses it only as a regional prior. It is
recorded in the trace. If you run this as a service:

- do not persist traces containing user locations longer than you need them;
- strip EXIF from uploads before processing;
- remember that `region_assumption_risk` deliberately fires when a pack is loaded and no
  location was supplied — the system is designed not to guess a location it was not given.

## What the system must not be used for

Nothing here is validated against real photographs at scale, including the locally reviewed
cards. Do not use it to decide:

- whether a tree is structurally safe or should be felled;
- whether timber is the species a seller claims;
- whether a plant or fruit is edible;
- anything with a legal, financial or safety consequence.

The `identified` status means "the evidence supports this claim at the stated level". It
does not mean "verified".

## Implementation references

- [`knowledge/`](../knowledge)
- [`tests/contract/test_data_contract.py`](../tests/contract/test_data_contract.py)
- [`.gitignore`](../.gitignore)
- [`SECURITY.md`](../SECURITY.md)
