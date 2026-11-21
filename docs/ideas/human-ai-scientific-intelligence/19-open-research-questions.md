# 19 — Open research questions

- **Status:** Draft — research questions, none answered
- **Owner:** Dendro Inspector maintainers
- **Date:** 2026-11-22
- **Last-verified:** 2026-11-22

## Agent brief

Each question has an ID that specs and backlog items cite. An agent that answers one records
the evidence and moves the question to the [decision log](decision-log.md); it does not
delete it.

## Botanical and field practice (need a qualified practitioner)

| ID | Question | Blocks |
|---|---|---|
| Q-F1 | Does the field workflow in [03](03-human-arborist-field-workflow.md) match how arborists in the region actually identify trees? | HAI-05, HAI-08 interface |
| Q-F2 | Which vegetative characters reliably separate *Alnus* from *Ulmus* without fruit, across species present in the region and across life stages? | [20](20-case-study-alnus-ulmus.md), HAI-04 data |
| Q-F3 | When is a single lobed-leaf reading sufficient for *Quercus* in the region, and which lobed-leaved trees (planted or native) must be excluded first? | [21](21-case-study-quercus.md) |
| Q-F4 | Which winter characters (buds, scars, twig) separate the 28 carded genera? | HAI-05 winter requests |
| Q-F5 | What are safe, legal, reachable alternatives for each diagnostic organ on a mature tree? | HAI-05 acquisition table |
| Q-F6 | How reliable is an expert's attachment attestation from the ground? | HAI-06 trust of `field_verified_by_expert` |

## Knowledge

| ID | Question | Blocks |
|---|---|---|
| Q-K1 | Which taxonomic backbone and regional flora should be authoritative, and under which licence? | HAI-07 |
| Q-K2 | Can existing multi-access key data for the regional flora be reused under Apache-2.0? | HAI-04 C1 |
| Q-K3 | Who qualifies as a knowledge reviewer for this project? | HAI-07, HAI-12 |

## Machine learning and evaluation

| ID | Question | Blocks |
|---|---|---|
| Q-M1 | How often do two model families make the *same* wrong reading of the same photograph? | HAI-11 |
| Q-M2 | Can any probability over hypotheses be calibrated well enough for expected information gain? | HAI-05 R2 |
| Q-M3 | Does image resolution sent to the model change readings materially? (CURRENT note: `DENDRO_IMAGE_MAX_EDGE_PX` is a confounder) | All comparisons |
| Q-M4 | What share of today's follow-up requests are decision-relevant? | HAI-E1 |
| Q-M5 | Is the graph's review layer net-negative on held-out data? | Option A scope |

## Human factors

| ID | Question | Blocks |
|---|---|---|
| Q-H1 | How much stage-1 friction will practitioners accept? | HAI-08 |
| Q-H2 | Does blind-first measurably reduce correct→wrong changes compared with AI-first? | H7 |
| Q-H3 | Which presentation of disagreement informs without anchoring? | HAI-08 stage 3 |

## Product scope

| ID | Question | Blocks |
|---|---|---|
| Q-P1 | Which user group has the problem this solves (look-alikes, winter, timber, documentation)? | Everything beyond Horizon 1 |
| Q-P2 | Does anyone need persistent tree records? | HAI-09 |
