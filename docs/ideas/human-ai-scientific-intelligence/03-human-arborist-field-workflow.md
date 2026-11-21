# 03 — Human arborist field workflow

- **Status:** Draft — research proposal; field practice described here is not expert-verified
- **Owner:** Dendro Inspector maintainers
- **Date:** 2026-11-22
- **Last-verified:** 2026-11-22 (CURRENT statements checked against `36a961c`)

## Agent brief

- **Use:** the reference model of the human side for specs 05, 06 and 08. An agent designing
  an observation request or an expert interface checks it against the steps and
  constraints here.
- **Status of the content:** the workflow below is a **HYPOTHESIS** about field practice
  written by an AI agent. It must be checked by a practising arborist or dendrologist
  before it shapes any interface ([19](19-open-research-questions.md), Q-F1).

## A field identification, step by step (HYPOTHESIS)

| Step | Human action | What the system knows today (CURRENT) | Gap |
|---|---|---|---|
| 1. Orient | Stand back; whole tree, habit, site, neighbours | `declared_object_type`, `location`, `habitat`, `season` as free context (`CaseInput`) | Context is never evidence above the context tier, correctly |
| 2. Isolate the target | Decide which trunk is "the tree"; note crossing crowns | Several subjects: no target designated (`designate_targets`) | No way to say "this one" except photographs or the experimental extractor proposal |
| 3. Trace ownership | Follow a branch from trunk to leaves; pull a twig to see it move | `confirmed_attached` is a model reading; `user_attested` from explicit statements | The single most valuable human act has a narrow input channel ([06](06-organism-binding-and-provenance.md)) |
| 4. Read diagnostic organs | Leaf base, apex, margin, venation, underside, arrangement, buds, fruit | Typed observations, exact card vocabulary | Fine-grained characters often fall outside card vocabulary (`unmatchable_evidence_ids`) |
| 5. Compare look-alikes | "Alder or elm? Check base symmetry and apex" | Single-card admission; comparison cards are placeholders | No explicit pairwise reasoning artifact ([04](04-contrastive-taxonomy-engine.md)) |
| 6. Decide what is missing | "No fruit in November; check buds instead" | One `PhotoRequest`, no seasonal or safety reasoning | [05](05-active-field-investigator.md) |
| 7. Measure if needed | Leaf length, petiole length, bud size, girth | `ImageLimitation.scale` only | [10](10-multimodal-measurement.md) |
| 8. Conclude at a defensible rank | Genus, species, or "Betulaceae, revisit in spring" | Deterministic resolution and confidence | Works; this is the system's strength |
| 9. Record and sign | Notes, photographs, name, who decided | Trace, review package revisions | No investigation record that spans visits |
| 10. Return | Another season for flowers or fruit | Nothing persists | [09](09-longitudinal-tree-intelligence.md) |

## Field constraints any request must respect

These are requirements on the system, derived from the steps above. Each later spec cites
them by ID.

| ID | Constraint |
|---|---|
| FC-1 | **Safety.** No request that implies climbing, ladders, cutting, standing under a hazardous tree, entering traffic, or touching plants of unknown toxicity |
| FC-2 | **Legality.** No request that implies entering private land, collecting from protected trees, or removing material without permission |
| FC-3 | **Non-destructive by default.** Prepared end grain is requested only for timber already cut (logs, firewood), never by cutting a standing tree |
| FC-4 | **Reach.** Most diagnostic organs on mature trees are out of reach. Requests must offer a reachable alternative (fallen material with stated provenance, binoculars or zoom, epicormic shoots, lower branches) and say what each alternative cannot prove |
| FC-5 | **Season.** Organs absent in the current season are not requested; the request names the season when they will be present |
| FC-6 | **Time.** A field visit has a time budget. The system states how many more observations it expects to need, and stops asking when no available observation can change the answer |
| FC-7 | **One target.** Every request names the organism it is about, in words a person can act on ("the trunk with the white paint mark"), not only a subject ID |
| FC-8 | **Provenance stated by the human is recorded as such.** "I pulled this twig off this trunk" is a user attestation, not a visual confirmation ([06](06-organism-binding-and-provenance.md)) |

## Where the human is strictly stronger (and the system must not compete)

- Verifying physical continuity by touch and movement.
- Seeing the organism in three dimensions and at scale.
- Knowing the site history (planted, self-sown, nursery stock, cultivar).
- Returning in another season.
- Taking responsibility for a conclusion.

## Where the system can be stronger (HYPOTHESIS)

- Remembering every look-alike for the candidates in play, including the ones the expert
  did not think of.
- Never skipping the attachment question.
- Keeping an exact record of which observation supported which conclusion.
- Choosing, from the remaining hypotheses, the observation that separates them, every time.
- Refusing to let a confident answer outrun its evidence tier.

Whether these advantages survive contact with real experts is the subject of
[13](13-evaluation-and-benchmark-design.md).
