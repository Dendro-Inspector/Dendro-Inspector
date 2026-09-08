# Dendro Inspector: A Model Proposes. Code Adjudicates.

- **Status:** Draft
- **Owner:** Dendro Inspector maintainers
- **Date:** 2026-08-27
- **Last-verified:** 2026-08-27

![Dendro Inspector](../assets/dendro-inspector-banner.png)

*How to build an AI system that says what a photograph actually proves — and refuses to say more. Part 1 of the series.*

---

A photograph: an old trunk with deep, rope-like furrows in the foreground, and a few compound leaves drifting in at the edge of the frame.

Ask a vision model what tree this is, and you will get a name. Often *Robinia pseudoacacia*. Sometimes that is right.

More dangerously, it can be wrong in a way that sounds exactly like expertise — a plausible species, a fluent explanation, and a confidence number with no clear relationship to what the image proves. Nothing in the answer tells you that the leaves might belong to a completely different tree standing behind this one.

Dendro Inspector starts from a different question:

> What is the narrowest claim this evidence can honestly support?

That question changes the entire workflow. The model is still useful — it proposes observations, candidates, critiques, and corrections. But it is no longer the final authority. Deterministic code decides which proposals are admissible, and how far the resulting claim may go.

The principle is short enough to fit on a sticker:

> **A model proposes. Code adjudicates.**

It is not a slogan pasted onto a prompt. It is an architectural boundary — and the rest of this article is about where that boundary sits.

## The workflow in one line

```text
image + context
    -> evidence
    -> candidates
    -> review
    -> optional arbitration
    -> capped decision
    -> honest answer
```

*[Figure 1 — the same pipeline drawn as two lanes: model steps on one side, deterministic gates on the other, the flow zig-zagging between them. The two exits code owns — abstention, and "send me this photograph" — sit on the gate side.]*

Every arrow matters. A conventional image classifier tries to jump from pixels to a label in one move. Dendro Inspector deliberately makes the intermediate reasoning inspectable.

A taxon name survives only when the evidence, the subject, the knowledge card, the reviews, and the confidence cap all agree that the claim is supportable.

The workflow is not designed to force a species answer. `Unknown` is a valid result. So is a genus-level answer with low confidence and a precise request for the next photograph.

## Start with evidence, not identity

The first model-backed step plans what to examine: bark, buds, foliage, fruit, cones, wood anatomy, attachment, or another useful feature. A second step extracts structured evidence from the photograph.

That evidence is split into two categories:

- An **observation** is something the image appears to show — `bark.texture = deep_rope_like_furrows`.
- An **inference** is an interpretation derived from named observations. It cannot exist without references back to its observable basis.

The distinction stops a conclusion from disguising itself as a visual fact.

"The bark has deep rope-like furrows" can be tested against the image and against the declared feature vocabulary. "This is Robinia" is a hypothesis. The system stores them in different places because they carry different authority.

The evidence contract also preserves two distinctions that ordinary descriptions blur.

**Not visible is not absent.** A shaded bud that cannot be resolved is not evidence that the tree has no buds.

**Visible in the frame is not necessarily attached to the subject.** Leaves at the edge of a photograph may belong to a neighbouring tree.

So every detachable feature — leaf, fruit, cone, seed, bud, needle, branch — carries attachment provenance. Unless it is confirmed attached to the subject under analysis, it stays context. It can explain uncertainty or motivate a follow-up photograph. It cannot strengthen the identification.

This is the first safety boundary: before asking what a feature *means*, establish what it *belongs to*.

## A candidate has to earn admission

Once the evidence packet exists, a model proposes ranked taxonomic candidates. That proposal is not yet part of the answer.

Deterministic validation checks each candidate against the project's taxon cards. A candidate must cite positive evidence from the same subject, and that evidence must match a declared feature and value for that taxon.

Unknown taxa, cross-subject evidence, unrelated context, and unsupported citations are removed. A candidate supported only by colour or tone is rejected outright.

The boundary is intentionally strict. If a model writes a persuasive paragraph about a taxon but cannot point to admitted evidence for it, the paragraph earns nothing.

And when no candidate survives, the system does not quietly rescue the most plausible name. It proceeds toward abstention.

That makes the knowledge layer more than a prompt appendix. It is executable policy — a public, inspectable account of which features may support which claims, at which taxonomic resolution.

## Reviewers advise; they do not rule

Surviving candidates go to three reviewers with different jobs:

- botanical consistency;
- confusion with look-alike taxa;
- confidence and resolution calibration.

Models are valuable here precisely because criticism is open-ended. A reviewer can catch an overlooked contradiction, a plausible alternative, or an overconfident reading that a small ruleset would miss.

But a reviewer finding is still only a proposal.

The review synthesizer checks whether a finding refers to real, subject-scoped evidence, or names a qualifying contract problem. Accepted and rejected findings are both retained, with reason codes. If a reviewer recommends a new ranking, that ranking must be attached to an accepted finding — and it must clear the same admission boundary as the original proposal.

This matters because "review" is not automatically a synonym for "correction." A second model can hallucinate just as confidently as the first.

Dendro Inspector treats review as additional evidence about the reasoning process, not as a privileged channel around the rules.

## Arbitration is conditional, not ceremonial

After review, a deterministic escalation gate decides whether the case needs an arbiter at all. It can escalate for reviewer disagreement, a critical admissible finding, a proposed species-level claim, or evidence suggesting multiple taxa in the frame.

The arbiter receives the case, the evidence, the candidates, the reviews, and the decision that would stand without arbitration. It can challenge that result and propose a rerank.

It still cannot bypass candidate validation, evidence ownership, or the final claim cap.

This is what separates the design from a panel vote. Dendro Inspector does not count model opinions and pick the most popular taxon. Extra model calls widen the search for mistakes; code still decides which changes are permitted.

## The final decision is a cap, not a confidence performance

The decision engine combines several independent ceilings:

- the authority of the admitted evidence;
- the resolution supported by the taxon card;
- conservative bounds from admitted reviewer findings;
- attachment and subject provenance;
- any validated correction from arbitration.

The narrowest defensible bound wins.

*[Figure 2 — evidence types as horizontal bars of different reach against a resolution axis, with the other ceilings drawn as vertical cut lines. The final claim lands at the leftmost cut.]*

Evidence types are not interchangeable. Clear fruit, seed, or cone evidence can support a much narrower claim than bark alone.

Bark may admit a useful candidate, but it is capped at genus and low confidence. Context can frame a question, but it cannot carry an identification by itself. And unattached foliage cannot borrow authority from the tree it happens to overlap in a two-dimensional image.

The response composer then explains the capped result: the evidence that survived, the nearest alternative, the unresolved limitations, and the best next photograph. It is not asked to improvise a nicer ending after the decision has been made.

## Back to that photograph

Return to the opening frame — furrowed trunk in the foreground, compound leaves at the edge.

A vision model may reasonably propose *Robinia pseudoacacia*. The bark looks compatible. The foliage looks helpful. A direct vision-to-label system stops there.

Dendro Inspector keeps asking:

- Are the leaves visibly connected to *this* trunk?
- Which bark observations exactly support the Robinia card?
- Does another mature rough-barked tree — *Populus*, say — remain plausible?
- Is the proposed species resolution stronger than bark can justify?
- Would the conclusion change if the foliage attachment were treated as unknown?

*[Figure 3 — the frame as a schematic: trunk in front, leaves at the edge, a dashed link between them labelled `attachment: unverified`, and what each element is allowed to contribute.]*

If the leaves cannot be traced to the trunk, they cannot support that trunk's identity. The bark may still admit Robinia as a candidate, but the decision is capped.

A defensible result looks like this: **probable Robinia, genus, low confidence** — paired with a request for one photograph showing a leafy branch running continuously back to the trunk.

The important output is not the name. It is the boundary around the name: why it survived, why it stopped at genus, what alternative remains, and which new evidence would actually change the answer.

## Why not just use a better model?

Better perception helps. Better structured-output reliability helps. A stronger reviewer catches more mistakes. None of that removes the need for an adjudication boundary.

Models change. Providers change. The same model answers differently across runs, and a schema-valid observation can still be visually wrong.

If the rules that inflate or cap a claim live only inside model calls, those rules become hard to test and impossible to guarantee.

Deterministic adjudication moves a specific class of responsibility into code:

- whether cited evidence exists and belongs to the right subject;
- whether a candidate has declared support;
- whether a review finding is actionable;
- whether escalation is required;
- what resolution and confidence may survive;
- whether the system should commit, downgrade, ask for another photograph, or abstain.

Those decisions can be tested without a provider and replayed identically.

The model keeps the work it is genuinely good at: perception, hypothesis generation, comparison, and critique.

## What this architecture does — and does not — prove

This design makes an answer more traceable and makes overclaiming harder. It does not make the underlying visual observations correct.

A model can still misread bark, invent attachment, or miss the decisive organ. A knowledge card can be incomplete. A deterministic rule can encode the wrong domain policy with perfect consistency.

Which is why Dendro Inspector separates two evaluation questions:

1. **Conformance** — does the machinery obey its contracts and caps on constructed cases?
2. **Accuracy** — does it identify independently annotated real photographs correctly?

The public suite answers only the first. It uses deterministic fixtures, so every pull request can prove the machinery still behaves as specified. Real-photo accuracy requires a separate, governed benchmark with independent labels and an honestly reported denominator.

The architecture is a claim about evidence handling, not a declaration of field accuracy. Saying so plainly is part of the design.

## The idea beyond trees

Dendrology makes the problem vivid, because photographs routinely contain partial organs, overlapping subjects, seasonal gaps, and look-alike taxa.

But the workflow generalizes to anywhere a model must reason from incomplete evidence — clinical images, damage assessment, document review, field inspection:

> Let the model explore the open world. Let code enforce the declared boundary of the claim.

That boundary does not eliminate uncertainty. It makes uncertainty part of the result, instead of something polished away at the end.

## Next in the series

Each of the next articles takes one boundary at a time:

1. **Evidence is not interchangeable** — tiers, attachment, subject scope, and why "visible" is not the same as "usable."
2. **Why reviewers do not get the final say** — admissible findings, finding-bound reranks, and conditional arbitration.
3. **How to cap a scientific claim** — resolution, confidence, abstention, and targeted follow-up photographs.
4. **What an evaluation suite can honestly prove** — deterministic conformance versus real-photo accuracy, and benchmark overfitting.

Dendro Inspector is open source. For the canonical technical description rather than the narrative one, start with
[Architecture](https://github.com/Dendro-Inspector/Dendro-Inspector/blob/main/docs/architecture.md#determinism-boundary),
[Agent graph](https://github.com/Dendro-Inspector/Dendro-Inspector/blob/main/docs/agent-graph.md),
[Review pipeline](https://github.com/Dendro-Inspector/Dendro-Inspector/blob/main/docs/review-pipeline.md), and
[Evaluation](https://github.com/Dendro-Inspector/Dendro-Inspector/blob/main/docs/evaluation.md).

*If this way of drawing the line between model and code is useful to you — or if you think the boundary sits in the wrong place — the repository is [Dendro-Inspector/Dendro-Inspector](https://github.com/Dendro-Inspector/Dendro-Inspector). Issues and disagreements welcome.*
