# Future backlog — research items

- **Status:** Draft — actionable research items; none approved; no GitHub issues created
- **Owner:** Dendro Inspector maintainers
- **Date:** 2026-11-22
- **Last-verified:** 2026-11-22

## Agent brief

- Pick items only with the owner's approval in the current session.
- Complexity: `S` (hours), `M` (days), `L` (weeks), `XL` (months, needs people). Scientific
  uncertainty: `low`, `medium`, `high`.
- "Paid" = needs paid model inference. Paid items need a stated budget first.
- Items marked **zero-cost** may be proposed to the owner immediately.

| ID | Item | Rationale | Depends on | Acceptance criteria | Evaluation | Complexity | Uncertainty | Paid | Suggested area |
|---|---|---|---|---|---|---|---|---|---|
| B1 | HAI-E1 decision-value audit (**zero-cost**) | Know whether better request ranking is worth building | Recorded traces (local) | Table: decision-relevant share, baseline vs R1, with denominators; script in a run folder; nothing in `src/` changed | Paired comparison | M | low | No | run folder; later `scripts/eval/` |
| B2 | HAI-E2 look-alike coverage audit (**zero-cost**) | Work list for a botanical reviewer | — | Table of declared confusion pairs, separating features, missing cards | Completeness check | S | low | No | run folder |
| B3 | Contrast table schema + one reviewed pair (C0) | Test H5 cheaply | B2, O2 reviewer | Schema with contract test; one pair reviewed by a named person; public suite unchanged | Unit + replay | M | medium | No | `knowledge/comparisons/`, `knowledge/comparison_cards.py` |
| B4 | NBO R1 ranker behind a flag | Test H6 | B1 positive, B3 | Deterministic, replayable ranking; safety filter contract; flag off by default | E3 pilot | M | medium | No (code) | `nodes/photo_planner.py`, `nodes/final_decision.py` |
| B5 | Acquisition, safety and season table | FC-1–FC-5 as data | Practitioner review | Reviewed table; contract test that no unsafe request can be built | Review | S–M | medium | No | `knowledge/` |
| B6 | Provenance basis telemetry | Expose single-witness ownership | — | Additive trace field; no decision change; tests for crossing-branch packets | Count on recorded traces | S | low | No | `schemas/evidence.py`, `observability/` |
| B7 | `PartAttestation.method` | Record how a human verified ownership | B6 | Optional field, default = today's meaning | Pilot attestation error | S | low | No | `schemas/input.py`, `nodes/field_attestation.py` |
| B8 | Blind-first package section | Measure automation bias | — | Sealed stage-1 section; old packages import; code-computed comparison | E4 pilot | M | medium | No | `human_review.py` |
| B9 | HAI-E4 blind-first feasibility pilot | Q-H1, Q-H2 | B8, experts, consent | Completion rate and change classes reported | Descriptive | L | high | No | outside repo |
| B10 | HAI-E3 field pilot (checklist vs current vs R1) | Q-P1, H6 | B4, B5, ground-truth procedure | Signed mini-protocol; results with denominators | [13](13-evaluation-and-benchmark-design.md) stage 3 | XL | high | Yes | outside repo |
| B11 | Sources registry | Citation-level provenance | Q-K1 | Registry + `source_ids` resolution test; no card value change | Coverage count | M | medium | No | `knowledge/` |
| B12 | Correction tiers in revisions | Safe learning loop | B8 | Tier and error class fields; invariants tested | Tier audit | M | medium | No | `human_review.py` |
| B13 | Cross-model error-overlap study | Q-M1, HAI-11 | Labelled units | Same-wrong-reading rate per pair of model families | Labelled replay | M | high | Yes | run folder |
| B14 | Morphology reviewer (downgrade-only) | Catch shared misreadings | B13 shows low overlap | Flagged, trigger-routed, ablation result | Ablation | M | high | Yes | `nodes/` (new, flag) |
| B15 | Two-visit investigation file (Option B seed) | Multi-season value without a registry | B4, B6 | Resume with history; verdict recomputed by engine code only | Pilot over two seasons | L | high | Yes | new module |
| B16 | Human–AI controlled study | H1–H4 | B9, B10, signed protocol | Pre-registered analysis executed | [13](13-evaluation-and-benchmark-design.md) stage 5 | XL | high | Yes | outside repo |
