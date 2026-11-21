# 21 — Case study: mature rough-barked tree with possibly oak-like foliage

- **Status:** Draft — evidence-binding research case; **not ground truth**
- **Owner:** Dendro Inspector maintainers
- **Date:** 2026-11-22
- **Last-verified:** 2026-11-22 (CURRENT statements checked against `36a961c`)

## Agent brief

- Not independently verified; never a benchmark label; never a reason to change a card.
- Observations are abstracted; no photograph or location is recorded.
- Expected outcome: an evidence-binding and follow-up acquisition protocol.

## Situation (abstracted)

| Item | Recorded as |
|---|---|
| Subject | Large mature standing tree |
| Bark | Thick, rough, fissured |
| Foliage | Lobed leaves visible near the crown |
| Surroundings | Other trees in the frame |
| Attachment | Physical attachment of the foliage to this trunk not established |

## Separation of claims

| Layer | Content | Label |
|---|---|---|
| Directly visible | Fissured bark; lobed leaves somewhere in the crown region; neighbouring trees | Observation (to be extracted) |
| Model interpretation | Possibly `leaf.shape = simple_lobed`, `attachment = confirmed_attached` or `unknown` | Model output |
| Unresolved attribution | Which trunk the lobed leaves belong to | Central question |
| Reference-card compatibility | *Quercus* card: strong `leaf.shape = simple_lobed`; contradiction `palmate_lobed`; bark `deep_longitudinal_fissures` supporting ([`quercus.yaml`](../../../knowledge/taxa/quercus.yaml)) | CURRENT |
| Diagnostic evidence | `simple_lobed` **only if** trusted (attached, clear, medium+ reliability) | Conditional |
| Working hypothesis | *Quercus*, if the leaves are this tree's | HYPOTHESIS |
| Validated identity | None | — |
| Expert-confirmed ground truth | None | UNKNOWN |

## What the CURRENT system does (expected, from code reading)

| If the extractor says | Then |
|---|---|
| `simple_lobed`, `confirmed_attached`, clear | *Quercus* admitted with a strong hit; may be named at genus. The attachment authority gate checks sensitivity × risk; with other trees in frame, risk may be flagged and the leaf demoted |
| `simple_lobed`, `unknown` attachment | Leaf is context; bark-only supporting → not named; next photo asks to prove ownership first (`attachment_request`) |
| `palmate_lobed` (misreading) | *Quercus* contradicted; *Acer* may be supported — the CURRENT known shared-misreading limit |

Weak point (CURRENT, documented in the gate): if the extractor asserts `confirmed_attached`
about a crossing branch and flags nothing, no code contradicts it.

## Research questions and proposed answers

**Can the system establish branch-to-trunk continuity?** Not from the current input
alone. Proposed routes ([06](06-organism-binding-and-provenance.md)): a continuity
photograph (trunk → branch → leaf cluster in one frame), a user attestation with method
("traced the branch", "pulled the twig"), or an expert's field verification. A second model
agreeing is not proof.

**Which observations belong to the target?** Only those with an allowed binding basis.
Bark on the trunk: yes. Leaves: unbound until proven.

**When is `simple_lobed` sufficient for *Quercus* under the reference rules?** Under the
CURRENT card, one trusted `simple_lobed` reading is a strong hit. Whether that is
botanically sufficient in the region depends on which other lobed-leaved trees occur
(planted or native) and how reliably the extractor separates pinnate from palmate lobing
(Q-F3). A reviewed contrast table against those taxa is the proposed route
([04](04-contrastive-taxonomy-engine.md)).

**What would an independent attachment review contribute?** It can *lower* trust when it
disagrees (catching some crossing-branch errors). It cannot raise trust. Its value must be
measured by ablation ([11](11-adversarial-scientific-review.md)).

**Which single follow-up photograph would most improve confidence?** The continuity
photograph, because every leaf-based conclusion depends on it. After ownership: an
attached leaf close-up (base, lobing, petiole) or acorns on the tree.

**How should conclusions change if the leaf belongs to another tree?** The leaf becomes
evidence for that other organism (its own subject), never for this trunk. This tree falls
back to bark-only: genus ceiling, low confidence, and under the discriminating-support rule
no name from bark alone.

## Proposed evidence-binding and acquisition protocol

| Step | Action | Allowed basis gained |
|---|---|---|
| 1 | Designate the target in words (stage 1, [08](08-human-ai-collaboration.md)) | Target fixed |
| 2 | Continuity photograph or traced-branch attestation | `visual_continuity_confirmed` or `user_attested` |
| 3 | Attached leaf close-up: lobing type, base, petiole | Trusted `leaf.shape` if step 2 succeeded |
| 4 | Acorns or cupules on the tree (or on the ground, provenance stated) | Strong (on tree) or capped (ground) |
| 5 | If no foliage can be bound: report bark-only result and a `revisit` for reachable shoots | — |
