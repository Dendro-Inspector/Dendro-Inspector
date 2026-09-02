# Specifications

- **Status:** Draft
- **Owner:** Dendro Inspector maintainers
- **Date:** 2026-09-02
- **Last-verified:** 2026-09-02

Three draft specifications, originally written against the working tree at `d6f8247`.
Phase 0's tests and base telemetry are implemented, as are hardening C2 and the remainder of
latency L1. The remaining phases stay proposals. Each specification carries its own verified
findings, contracts, tests and evidence. This page is the one place that says in which order
they land and which decisions gate them.

| Specification | Owns | Quality effect |
| --- | --- | --- |
| [`core-logic-hardening.md`](core-logic-hardening.md) | What the deterministic layer decides | Moves frozen verdicts, downward only |
| [`latency-and-cost.md`](latency-and-cost.md) | Speed and cost of a live run | Neutral by measurement |
| [`core-modernisation.md`](core-modernisation.md) | Shape of the data and the public API | Neutral by proof |

## Order of work

Dependencies, not preferences. An arrow means "cannot be measured or accepted without".

```text
PR 1  tests + telemetry [landed] hardening Phase 0 (10 strict xfails)
                                 modernisation Phase 0 (contract tests, flake_geometry as xfail)
                                 latency L1 minus the provisional verdict: token fields,
                                   critical_path_ms, scripts/bench/
                                          |
PR 2  provisional verdict [landed] hardening C2 + latency L1 remainder
                                   (provisional_decisions and arbiter_changed_* in the trace)
                                          |
          +-------------------------------+-------------------------------+
          |                               |                               |
PR 3  hardening C3, C6, C5      PR 4  modernisation N1, N2       corpus re-run -> L5 data
      (contradiction authority,       (registry, retrieval)             |
       derivation, abstention)                |                          |
          |                                   |                    PR 6  latency L5
PR 5  hardening C1               PR 7  latency L2, L3                   (escalation policy)
      (adjudicated strength)           (input diet, deadlines)
          |
PR 8  latency L4 (output diet, re-seal, conformance review)
PR 9  modernisation N3, N4, N5;  hardening C4, C7;  latency L6, L7 experiments
```

PR 1 and PR 2 now provide the measurement boundary for the remaining work. PR 2 closes the
worst verified quality gap (a `high` verdict the gate never looked at) and makes the arbiter's
marginal value measurable for the first time.

## Decision register

Every open decision across the three documents, with the recommendation each document
makes and the first pull request it blocks. Nothing blocks PR 1 or PR 2.

| # | Decision | Recommendation | Blocks |
| --- | --- | --- | --- |
| H1 | Species trigger fires on the proposal or on the provisional verdict | Keep on the proposal; measure `unnecessary_arbiter_call_rate` after PR 2 | nothing until data |
| H2 | Abstention returns a flagged, broader claim or `insufficient_evidence` | Flag plus one step broader from the composed bound | PR 3 (C5) |
| H3 | Model confidence downgrades with no reviewer floor: unbounded or one per reviewer | One step per reviewer per subject | PR 9 (C7) |
| H4 | Negated user claims: unrecognised or a testable negative statement | Unrecognised for now | PR 9 (C4) |
| L1 | Arbiter-changed-verdict thresholds for hard and soft triggers | Hard ≥ 10 %, soft ≥ 5 %, set **before** the corpus re-run | PR 6 |
| L2 | `FindingSummary` bound | 240 characters | PR 8 |
| L3 | Compact JSON on the wire | Only if tokens-per-character shows whitespace | PR 8 |
| L4 | Run the planner ablation at all | Yes, as a blind A/B on the repeatability corpus | PR 9 |
| M1 | `bark.flake_geometry`: register with card values, or remove from prompts and comparison card | Dendrology judgement, §12 conformance process | the xfail in PR 1 turning green |
| M2 | Feature registry as package data or under the knowledge root | Package data; tiers are policy | PR 4 (N1) |
| M3 | Deprecation window for the string requirement grammar | One minor release | PR 9 (N3) |
| M4 | Public API shape | `inspect` / `ainspect` returning `InspectionResult` | PR 9 (N5) |

## Rules that apply to every pull request here

From `AGENTS.md`, restated only so that a reader of this page does not miss them:

- a change that moves any frozen public-suite decision bumps `policy_revision`, re-freezes
  `evals/baselines/`, and lists each moved case in `CHANGELOG.md` with its direction;
- no case's resolution narrows and no case's confidence rises; `overconfidence_rate` stays
  `0.0`;
- a prompt-byte change re-seals with `dendro prompt-seal --write` and owes the §12
  conformance review;
- a change motivated by a corpus measurement carries the §16 justification block naming the
  measurement as its independent source;
- documents describing the changed behaviour change in the same commit.

## When a specification is done

Move it to `docs/reviews/<NAME>-<date>.md` with its verification record, per §7. This page
then loses its row. A specification whose remaining items are all in the Deferred or
Non-goals sections is done.
