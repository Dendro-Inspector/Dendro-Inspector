# Held-out evaluation protocol for v1.0.0

- **Status:** Draft
- **Owner:** Dendro Inspector maintainers
- **Date:** 2026-11-16
- **Last-verified:** 2026-11-16

This file fixes, before any result exists, how the held-out run is carried out, what is
counted, and which result leads to which decision. It exists so that the criteria cannot be
adjusted afterwards to make one arm look like the winner. It is **not signed yet**: no
held-out result may be read before the sign-off table at the end is filled in.

Rules that follow from that:

- The owner signs the table at the end. Until then every number marked **proposed** is a
  proposal and nothing is run.
- From the commit that signs it, this file is frozen. A change to an arm, a metric, a
  threshold or the decision rule after the first held-out answer has been read makes a new
  protocol, and a new protocol needs new units. A result obtained any other way is reported
  as exploratory and decides nothing.
- No card, prompt, threshold or routing rule changes between sign-off and the run
  ([`AGENTS.md` §16](../../AGENTS.md#16-benchmark-governance)).
- Every figure is reported with its denominator, per unit and per physical tree.

## The question

On the development photographs the graph names fewer trees than the model it is built on,
at many times the cost ([README, Status](../../README.md#status)). That set cannot settle
whether the graph is worth its complexity: the reference cards were developed with those
photographs at hand, and every unit in it is a genus a card covers, so it never asks the
graph to do what it was built for.

The held-out run asks one question: **on photographs neither arm has been tuned on, does
the graph show an advantage that justifies its complexity, without an unacceptable loss
elsewhere?** It does not have to name more trees than the model alone. It does have to be
better at something that matters, by a margin fixed here.

## The set

The set is described in [`docs/evaluation.md`](../evaluation.md#the-held-out-set) and
validated by `dendro heldout-check`. For this protocol:

- The unit of independence is the physical tree or wood lot (`group_id`), not the
  photograph and not the unit. No photograph of a held-out tree may be in a development
  directory, including a different photograph of the same tree.
- Only release-ready units are scored: `reviewed` or `resolved` by a specialist.
  `preliminary` and `disputed` units are listed and left out.
- A label says what the specialist could establish. A unit whose genus cannot be
  established from any evidence is labelled so and scored as an abstention case; it is not
  forced into a genus.
- `heldout-check` must pass with no failure before the run, and its report is kept with the
  results.

Minimum composition, **proposed**:

| Requirement | Proposed minimum | Why |
| --- | --- | --- |
| Release-ready units | 100 | Below this a wrong-name rate of a few percent cannot be told from zero |
| Physical trees or lots | 80 | Several units of one tree are not independent |
| Genera with a card | 12 | The development set has seven |
| Units of a genus with no card (`unknown_taxon`) | 20 | The case the graph exists for; absent from development |
| Units marked `confusable` | 20 | Look-alike genera are where a confident wrong name is most likely |
| Units where no genus can be established | 10 | Correct abstention needs cases where abstaining is right |
| Units per category `bark_only`, `wood`, `mixed_subjects` | 10 each | Each is a distinct failure mode |

A unit may count towards several rows.

## The arms

Every arm sees the same units, the same photographs, prepared the same way
(`DENDRO_IMAGE_MAX_EDGE_PX`), with the same model in every role, once.

| Arm | What runs |
| --- | --- |
| **M. Model alone** | One structured call: the domain prompt, all photographs of the unit, and a request for a genus or an explicit "cannot be determined", a family, and a confidence |
| **G0. Graph, defaults** | The graph with every setting at its code default |
| **G1. Graph, candidate** | The graph as proposed for v1.0.0: `DENDRO_REVIEW_AUTHORITY=evidence_bound`, `DENDRO_SELECTIVE_ARBITRATION=true`, everything else at its default |

The decision rule compares **G1 with M**. G0 is reported beside them so that the effect of
the two policies is visible; it decides nothing.

Before the run:

- Arm M's script, prompt and answer schema are committed to this repository, and their
  commit is entered in the sign-off table. They are `scripts/eval/model_alone.py` (the run)
  and `dendro_inspector.evaluation.model_alone` (the request text and the answer schema).
  Every arm M record carries `request_sha256`, `schema_sha256` and the domain prompt's
  SHA-256, so the table can name exactly what was asked. The call goes through the same
  provider adapter and image preparation as the graph.
- `dendro release-manifest` is recorded for G0 and G1, with the image digest.
- Automatic selection of the target among several subjects (`DENDRO_TARGET_SELECTION`)
  stays off. Units marked `mixed_subjects` are scored against the target the manifest
  declares.

A run that fails for a provider reason is repeated once. If it fails again it is counted as
`failed` for that arm and reported; it is not dropped.

## What is counted

Each unit gets one outcome per arm, graded against the specialist's label by
`dendro coverage-audit --labels` for the graph arms and by the same rules for arm M:

| Outcome | Meaning |
| --- | --- |
| `correct` | The labelled genus is named for the target subject |
| `wrong` | Another genus is named for the target subject |
| `broader_only` | Only the correct family is named |
| `wrong_broader` | A wrong family is named |
| `ambiguous_with_label`, `ambiguous_without_label` | Two or more genera are named; never counted as correct |
| `no_name` | Nothing is named |
| `missing_target`, `unscoped` | The answer cannot be tied to the declared target |
| `failed` | No answer |

An unconfirmed hypothesis (`visual_hypothesis`) is never an answer. It is counted on its
own: how often one is present, and how often it names the labelled genus.

For a unit whose genus has no card, the correct outcome is that no carded genus is named.
For a unit where no genus can be established, the correct outcome is `no_name` or
`broader_only`.

## Metrics

Per arm, each with its count and a Wilson interval, per unit and per physical tree:

1. **Wrong-genus rate among named answers**: `wrong` over `correct` plus `wrong`, on units
   whose genus has a card.
2. **Naming coverage**: `correct` over all units whose genus has a card.
3. **False naming of an unknown taxon**: units of a genus with no card for which a carded
   genus is named, over all such units.
4. **False naming where nothing can be established**: the same, over the units labelled as
   not determinable.
5. **Cost and time per unit**: model requests, tokens, the price at the rates in force on
   the day (stated), and wall-clock time at the median and the 95th percentile.

Units with a visible label are reported with and without. The paired table (each unit's
outcome under M against its outcome under G1) is published with the summary.

## Decision rule

Thresholds are **proposed** until signed.

**S, safety.** G1's wrong-genus rate among named answers is at most **5%** and is not
higher than M's.

**A, advantage.** At least one holds:

- **A1, fewer wrong names where it matters.** On the units of a genus with no card and the
  `confusable` units taken together, G1 names a wrong genus on at most **half** as many
  units as M, and M does so on at least **6**. Fewer than 6 means the set could not show
  the difference, and A1 is undecided, not met.
- **A2, the same quality for less.** G1 meets S, its naming coverage is within **5
  percentage points** of M's, and its price per unit is at most M's.

**L, no unacceptable loss.** G1's naming coverage on carded genera is at least **80%** of
M's.

| Result | Decision |
| --- | --- |
| S, A and L hold | v1.0.0 ships with G1's settings as the defaults |
| S fails | No release. The wrong names are analysed by failure class and fixed under `AGENTS.md` §16; a new protocol and new units follow |
| S holds, A or L fails | The graph has not earned its cost in this shape. v1.0.0 does not ship as it is and the architecture is reviewed, starting from where names are lost. Adding review stages is not the response |
| A1 undecided, A2 not met | The set was too small to decide. More units are collected under this protocol, unchanged |

Whichever row applies, the result is published with the table, including when it is
unflattering.

## Budget

Estimates from the development runs, for planning only: arm M used about 14,000 tokens a
unit and the graph about 220,000. For 150 units that is about 2 million tokens for M and
about 33 million for each graph arm, which at a quota of 500,000 tokens a minute is a
little over an hour of the whole quota per graph arm. `DENDRO_MAX_TOKENS` is left unset for
the run: a spend limit changes answers and is not part of either graph arm.

## Sign-off

| Item | Proposed | Signed value |
| --- | --- | --- |
| Minimum composition of the set | The table under "The set" | |
| Who labels, and their qualification | To be named | |
| Model used in every role of every arm | `gpt-5-mini` | |
| Image edge sent to the model | The deployment's value on the day | |
| Commit of arm M's script, prompt and schema | To be committed | |
| S: highest wrong-genus rate among named answers | 5% | |
| A1: ratio of wrong names, and the minimum for M | One half; 6 | |
| A2: coverage margin | 5 percentage points | |
| L: lowest naming coverage relative to M | 80% | |
| Signed by, and date | | |
