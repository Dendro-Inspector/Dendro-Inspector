# 17 — Evil Duck: reasons this architecture might be wrong

- **Status:** Draft — adversarial review of this research package
- **Owner:** Dendro Inspector maintainers
- **Date:** 2026-11-22
- **Last-verified:** 2026-11-22 (CURRENT statements checked against `36a961c`)

## Agent brief

Read this before building anything from this directory. If a proposal cannot answer the
question here that applies to it, do not implement it.

## The uncomfortable baseline

On the only real-photograph data available, the graph names the right genus 18 times out
of 41 units; the model it is built on, asked once, names it 38 times — at about one fifteenth
of the input tokens and a third of the time ([`README.md`](../../../README.md#status)). The
graph's advantage so far is that it is wrong less often in a way that was not yet measurable
(1 wrong vs 2, in-sample). This package proposes **more** machinery. The burden of proof is
on every addition.

## The fifteen questions

**1. Are we solving a genuine arborist problem or inventing a research project?**
Partly inventing. An experienced arborist identifies common Eastern European genera quickly
and rarely needs a system. The genuine problems are narrower: look-alikes at the edge of
their experience, winter trees, bark-only timber, uncommon or planted exotics, and
documenting *why* a determination was made. A system that helps there is useful; a system
that re-identifies oaks for oak experts is not. **Action:** recruit target users and ask
which cases cost them time before building HAI-05 beyond the offline audit.

**2. Which capabilities already exist in botanical applications?**
Photo identification with candidate lists (Pl@ntNet), community verification and
rank-limited identification (iNaturalist), multi-organ fusion (research), interactive
multi-access keys (DELTA/Lucid-type tools, from general knowledge). See
[`NOVELTY-AND-VALUE-ASSESSMENT-2026-08-23.md`](../../reviews/NOVELTY-AND-VALUE-ASSESSMENT-2026-08-23.md).
Interactive keys already "choose the next character". NBO without contrast data is a
worse key.

**3. What is truly new or differentiating?**
Possibly only the combination: deterministic evidence caps + explicit organism binding +
look-alike contrast + safety/season-aware next observation + blind-first expert record +
replayable provenance. None is new alone. Uniqueness is **UNKNOWN**.

**4. Which improvements could come from better photography guidance instead of AI?**
Probably most of the early gains. A one-page capture protocol (whole tree, bark at breast
height, attached shoot with leaves flat, leaf base and underside, buds in winter, fruit if
present, scale object) may beat any ranking algorithm. **Action:** test a static capture
checklist as the baseline arm in HAI-E3 — if the NBO cannot beat a checklist, do not build
it.

**5. Are we adding complexity without independent information?**
Yes, wherever a second model reviews the first model's reading of the same photograph. A
reviewer adds information only if it sees something new (another family's perception, a
new photograph, a human). Same-model checks add tokens, not evidence (the oak→*Acer*
limit).

**6. How do we demonstrate each reviewer's incremental value?**
Per-reviewer ablation on labelled held-out units: wrong names prevented and correct names
lost per 100 calls, with intervals. CURRENT data already shows review withdrawing correct
names. Any reviewer that cannot show a net benefit is removed, not tuned.

**7. Too slow or expensive for fieldwork?**
The CURRENT graph takes about 141 s and ~196,000 input tokens per unit, offline-capable
only with a local model. In the field, connectivity and patience are limited. Proposals in
this package add zero model calls (Option A), which is the right direction; Option B's
multi-round investigations multiply graph runs. A field tool may need arm-V-like cost
(model + extraction + card check) rather than the full graph per round.

**8. Can experts understand and contest the evidence?**
Not today without reading JSON. The evidence view in [06](06-organism-binding-and-provenance.md)
is a requirement, not a nicety. If experts cannot contest a reading in seconds, the record
is decoration.

**9. Does a persistent tree database justify its cost?**
Not yet. Multi-season value can be tested with a two-visit investigation file. A registry
brings privacy, consent, hosting and identity-collision problems. Deferred
([09](09-longitudinal-tree-intelligence.md)).

**10. What happens when model knowledge and authoritative references disagree?**
The reference wins as a *rule*, after human review; the model's reading of the photograph
remains an observation. Today the system has no authoritative live source at all; operator
records can only dispute (CURRENT). Until a reviewed backbone exists, disagreements are
recorded and the claim stays bounded.

**11. Would a dichotomous key outperform the agent graph on some tasks?**
Probably yes for a trained user with a good photograph of the right organ: a key is
transparent, offline, free and validated by botanists. The defensible role for Dendro is
*reading* the photograph into characters and *recording* provenance, then letting
key-like contrast tables decide. **Action:** include a key-only arm (expert + printed key)
in the pilot if a suitable regional key is available.

**12. Could AI make a good arborist less accurate?**
Yes. Automation-bias literature (README references) shows reliance on aid can reduce
vigilance. This is the most serious risk to H1 and is why blind-first is non-negotiable in
every study.

**13. What should not be built at all?**
Spectral/hyperspectral and multi-view reconstruction for identification; vector-index RAG
feeding literature into prompts; automatic card edits from corrections; fine-tuning on
expert corrections; voting ensembles of same-family models; a consumer app on top of
unvalidated cards; anything that touches tree safety or risk.

**14. Which ideas wait for independent scientific evidence?**
R2 expected information gain (needs calibration); expert attestation earning full tier;
segmentation-based attachment; persistent registry; additional model reviewers; any claim
about panel-level performance (H4).

**15. If Human + Dendro performs worse than Human Solo, what do we question first?**
In order: (a) the presentation — did stage 3 anchor experts (automation bias) rather than
inform them; (b) the knowledge — did contrast data or cards mislead on the failing pairs;
(c) the binding — did evidence from another tree enter; (d) the request — did NBO consume
the observation budget on low-value photographs. Not first: "add another reviewer".

## Verdict

Build nothing until the zero-cost audits (HAI-E1, HAI-E2) and a 30–40 tree pilot with a
checklist baseline exist. The most likely outcome is that **capture guidance + attachment
proof + reviewed look-alike tables** deliver most of the value, and that the graph's
review layer should shrink rather than grow. That outcome would be a success for this
research, not a failure.
