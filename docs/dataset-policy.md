# Dataset and content policy

- **Status:** Current
- **Owner:** Dendro Inspector maintainers
- **Date:** 2026-07-26
- **Last-verified:** 2026-07-26

This is a **public repository**. Everything committed is world-readable, permanently
archived by third parties, and mirrored within minutes. Deleting a commit does not unpublish
it.

## Knowledge cards are placeholder content

Every card under `knowledge/` carries `placeholder_content: true`, and a contract test
enforces it.

They were authored for this project as a wiring demonstration. **No dendrologist has
reviewed them.** They are not derived from a third-party dataset, a field guide, or a
taxonomic database. The three-genus conifer pack (Pinus, Picea, Larix) exists to exercise
the graph, not to identify trees.

The flag is load-bearing: the response composer appends a placeholder warning to any answer
backed by a card carrying it. Removing the flag without a real review would silently upgrade
demonstration content into apparent authority.

### Provenance is mandatory

Every card carries a `provenance` block. **It is scoped to the card's feature rules** —
`strong_positive_features`, `supporting_features`, `contradictions` and the thresholds
around them — and any individual feature rule may override it:

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

1. have someone who actually knows the taxon review the features, contradictions and
   high-confidence requirements;
2. set `review_state: reviewed` with `reviewed_by` and `last_reviewed`;
3. set `placeholder_content: false`;
4. update the comparison cards that mention it.

Do not promote a card because it "looks right". The failure mode this project is built
around is confident content that nobody checked.

### Finding the gaps a card review should fill

Candidate validation matches evidence to cards by exact `(feature, value)` equality, so any
observation outside the cards' combined vocabulary is silently unusable no matter how good
the photograph was. Every run now measures this: the evidence-quality report carries
`unmatchable_evidence_ids`, and the quality gate logs `evidence_outside_card_vocabulary`
with the features absent from every card.

Read it as a **card coverage** signal, not a model failure. The extractor is deliberately
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
  support; they matter only as contradictions.

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

Nothing here is validated against real photographs at scale, and the knowledge is
placeholder content. Do not use it to decide:

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
