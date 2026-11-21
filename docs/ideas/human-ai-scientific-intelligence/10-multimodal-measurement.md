# 10 — Multimodal and instrument augmentation

- **Status:** Draft — research proposal, not implemented
- **Owner:** Dendro Inspector maintainers
- **Date:** 2026-11-22
- **Last-verified:** 2026-11-22 (CURRENT statements checked against `36a961c`)

## Agent brief

| | |
|---|---|
| Spec ID | HAI-10 |
| Priority | P3 research |
| Goal | Decide which extra modalities could add identification evidence, smartphone first, and how measured values differ from AI reconstructions |
| Implementation approved | **No**. No new dependency, sensor integration or model |
| Rule | A modality is worth adding only if it beats a well-taken photograph of an attached leaf or reproductive organ on the cases where that photograph is unavailable |

## Current state

- **CURRENT.** Inputs are photographs plus free-text context (`CaseInput`). Scale is recorded
  per image as `exact | approximate | absent` (`ImageLimitation.scale`). No measurement
  contract exists; a length can appear only inside a value token chosen by the extractor
  (the Robinia pod token, [dataset policy](../../dataset-policy.md#provenance-is-mandatory)).
- **CURRENT.** Wood observations carry the surface they were made on; pores and rays count as
  anatomy only on prepared end grain
  ([architecture](../../architecture.md#wood-surface-provenance)).
- **CURRENT.** Image size is capped by `DENDRO_IMAGE_MAX_EDGE_PX`; resolution is a confounder
  in model comparisons ([evaluation](../../evaluation.md)).

## Principle: measured vs reconstructed

| Class | Example | May support a claim at |
|---|---|---|
| `measured` | Ruler in frame read by a person; calliper value typed in | Its feature's tier, with stated precision |
| `image_estimated` | Length estimated from a scale object by code | Its feature's tier only if the scale object is `exact` and the error bound is stated |
| `model_estimated` | A model says "about 8 cm" | Context only |
| `reconstructed` | Depth or 3-D from multi-view by a model | Context only until validated against `measured` |

A value never carries more precision than its instrument. "8.37 cm" from a phone photograph
without a scale object is rejected by contract, not rounded.

## Modality assessment

Ordinal judgements, not measurements. **HYPOTHESIS** throughout; each row needs a study.

| Modality | Identification value | Hardware | Field access | Reliability risk | Integration | Privacy | Verdict |
|---|---|---|---|---|---|---|---|
| Better smartphone photographs (guided framing, scale object, flat leaf) | High | None | High | Low | Low (NBO guidance) | Low | **First** |
| Macro (clip-on lens or phone macro mode) | Medium–high for buds, leaf margin, hairs, lenticels | Low | High | Medium (focus) | Low | Low | Second |
| Scale object + code measurement | Medium (leaf, petiole, cone, bud size) | None | High | Medium (perspective) | Medium | Low | Second |
| Typed field measurements (girth, leaf length) | Low–medium for genus; higher for species pairs | Tape | High | Low | Low | Low | Second |
| Seasonal time series (revisit) | High where reproductive organs are seasonal | None | Medium | Low | Needs [05](05-active-field-investigator.md) resume, maybe [09](09-longitudinal-tree-intelligence.md) | Medium (location) | Third |
| Region / environment context | Prior only | None | High | Bias risk | CURRENT regional packs | Medium | Keep as prior; never evidence |
| Prepared end-grain macro (timber) | High for wood | Knife, water | Only cut timber (FC-3) | Medium | CURRENT | Low | Already supported |
| Microscopic wood anatomy | High for wood genera | Microscope, lab prep | Low | Low if expert-prepared | High | Low | Lab partner only |
| Depth / LiDAR (phone) | Low for taxonomy; useful for continuity | Some phones | Medium | Medium | High | Medium | Defer; test only for attachment continuity |
| Multi-view reconstruction | Low for taxonomy | None | Medium | High (artefacts) | High | Medium | Reject for identification |
| Spectral / hyperspectral | Unproven at single-tree field level for these genera | High cost | Low | High (illumination) | High | Low | Reject for now |
| Laboratory-confirmed specimens (herbarium, DNA) | Reference-grade ground truth | Lab | Low | Low | For evaluation labels only | Low | Use for **labels**, not runtime |

## Proposal

1. Start with **guided smartphone capture** inside NBO requests: one sentence on framing
   (CURRENT `_CAPTURE_DIRECTIONS` in
   [`photo_planner.py`](../../../src/dendro_inspector/nodes/photo_planner.py) is the seed),
   a scale object, and which organ.
2. Add a `Measurement` contract (sketch in [15](15-proposed-data-contracts.md)) with
   `method`, `value`, `unit`, `precision`, `instrument`, `measured_by`; only `measured` and
   validated `image_estimated` can match a card rule that declares a numeric range.
3. Use laboratory or herbarium confirmation to strengthen **evaluation labels**
   ([13](13-evaluation-and-benchmark-design.md)), not runtime evidence.

## Invariants

1. No modality enters runtime evidence without a study showing it adds information over the
   photograph alternative.
2. Reconstructions are context.
3. Precision is bounded by instrument.

## Evaluation

For a candidate modality M: on units where the decisive photograph is unavailable,
compare resolution and wrong-name rate with and without M, paired, per physical tree.

## Open questions

- Are numeric card ranges (leaf length, petiole length) reliable enough to separate any
  pair in the current catalogue? **UNKNOWN** — needs a reviewed flora.
