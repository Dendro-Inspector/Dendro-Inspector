"""Evaluation assertions.

Each assertion is a pure function over the finished graph state. They check *decisions*,
never prose, which is what makes the suite deterministic and what stops a rewrite of the
tone layer from turning the evaluation red.
"""

from __future__ import annotations

from collections.abc import Callable

from dendro_inspector.graph.state import GraphState
from dendro_inspector.schemas.decisions import UserClaimVerdict
from dendro_inspector.schemas.evaluation import AssertionOutcome, AssertionResult, EvalExpectation
from dendro_inspector.schemas.reviews import FindingCategory
from dendro_inspector.schemas.taxon import confidence_rank, resolution_rank

Assertion = Callable[[GraphState, EvalExpectation], AssertionResult | None]


def _passed(name: str, detail: str | None = None) -> AssertionResult:
    return AssertionResult(name=name, outcome=AssertionOutcome.PASS, detail=detail)


def _failed(name: str, detail: str) -> AssertionResult:
    return AssertionResult(name=name, outcome=AssertionOutcome.FAIL, detail=detail)


def _leading(state: GraphState) -> str | None:
    for decision in state.decisions:
        if decision.selected_taxon:
            return decision.selected_taxon
    return None


def check_expected_taxon(state: GraphState, expect: EvalExpectation) -> AssertionResult | None:
    if expect.expected_taxon is None:
        return None
    actual = _leading(state)
    if actual == expect.expected_taxon:
        return _passed("expected_taxon", actual)
    return _failed("expected_taxon", f"expected {expect.expected_taxon!r}, got {actual!r}")


def check_expected_taxon_display_name(
    state: GraphState, expect: EvalExpectation
) -> AssertionResult | None:
    if expect.expected_taxon_display_name is None:
        return None
    actual = {
        decision.selected_taxon_display_name
        for decision in state.decisions
        if decision.selected_taxon_display_name is not None
    }
    if expect.expected_taxon_display_name in actual:
        return _passed("expected_taxon_display_name", expect.expected_taxon_display_name)
    return _failed(
        "expected_taxon_display_name",
        f"expected {expect.expected_taxon_display_name!r}, got {sorted(actual)}",
    )


def check_expected_candidate_taxa(
    state: GraphState, expect: EvalExpectation
) -> AssertionResult | None:
    if expect.expected_candidate_taxa is None:
        return None
    actual = tuple(
        candidate.taxon
        for candidate_set in state.candidate_sets
        for candidate in candidate_set.ordered
    )
    if actual == expect.expected_candidate_taxa:
        return _passed("expected_candidate_taxa", repr(actual))
    return _failed(
        "expected_candidate_taxa",
        f"expected {expect.expected_candidate_taxa!r}, got {actual!r}",
    )


def check_top_3(state: GraphState, expect: EvalExpectation) -> AssertionResult | None:
    if expect.expected_taxon_in_top_3 is None:
        return None
    for candidate_set in state.candidate_sets:
        top_3 = {candidate.taxon for candidate in candidate_set.ordered[:3]}
        if expect.expected_taxon_in_top_3 in top_3:
            return _passed("expected_taxon_in_top_3")
    return _failed("expected_taxon_in_top_3", f"{expect.expected_taxon_in_top_3!r} not in top 3")


def check_resolution(state: GraphState, expect: EvalExpectation) -> AssertionResult | None:
    if expect.expected_resolution is None:
        return None
    actual = {decision.resolution for decision in state.decisions}
    if expect.expected_resolution in actual:
        return _passed("expected_resolution", expect.expected_resolution.value)
    return _failed(
        "expected_resolution",
        f"expected {expect.expected_resolution.value}, got {sorted(r.value for r in actual)}",
    )


def check_max_resolution(state: GraphState, expect: EvalExpectation) -> AssertionResult | None:
    """The overconfidence guard: no decision may be narrower than the stated ceiling."""
    if expect.max_resolution is None:
        return None
    ceiling = resolution_rank(expect.max_resolution)
    offenders = [
        f"{decision.subject_id}={decision.resolution.value}"
        for decision in state.decisions
        if resolution_rank(decision.resolution) > ceiling
    ]
    if offenders:
        return _failed(
            "max_resolution",
            f"claims narrower than {expect.max_resolution.value}: {', '.join(offenders)}",
        )
    return _passed("max_resolution")


def check_max_confidence(state: GraphState, expect: EvalExpectation) -> AssertionResult | None:
    if expect.max_confidence is None:
        return None
    ceiling = confidence_rank(expect.max_confidence)
    offenders = [
        f"{decision.subject_id}={decision.confidence.value}"
        for decision in state.decisions
        if confidence_rank(decision.confidence) > ceiling
    ]
    if offenders:
        return _failed(
            "max_confidence",
            f"confidence above {expect.max_confidence.value}: {', '.join(offenders)}",
        )
    return _passed("max_confidence")


def check_status(state: GraphState, expect: EvalExpectation) -> AssertionResult | None:
    if expect.expected_status is None:
        return None
    actual = {decision.status for decision in state.decisions}
    if expect.expected_status in actual:
        return _passed("expected_status", expect.expected_status.value)
    return _failed(
        "expected_status",
        f"expected {expect.expected_status.value}, got {sorted(s.value for s in actual)}",
    )


def check_abstained(state: GraphState, expect: EvalExpectation) -> AssertionResult | None:
    if expect.expected_abstained is None:
        return None
    actual = {decision.abstained for decision in state.decisions}
    if actual == {expect.expected_abstained}:
        return _passed("expected_abstained", str(expect.expected_abstained))
    return _failed(
        "expected_abstained",
        f"expected abstained={expect.expected_abstained}, got {sorted(actual)}",
    )


def check_min_subjects(state: GraphState, expect: EvalExpectation) -> AssertionResult | None:
    if expect.min_subjects is None:
        return None
    count = len(state.decisions)
    if count >= expect.min_subjects:
        return _passed("min_subjects", str(count))
    return _failed("min_subjects", f"expected at least {expect.min_subjects} subjects, got {count}")


def check_next_photo(state: GraphState, expect: EvalExpectation) -> AssertionResult | None:
    if not expect.require_next_photo:
        return None
    if any(decision.best_next_photo is not None for decision in state.decisions):
        return _passed("require_next_photo")
    return _failed("require_next_photo", "no targeted photograph request was produced")


def check_escalation(state: GraphState, expect: EvalExpectation) -> AssertionResult | None:
    if expect.require_escalation is None:
        return None
    escalated = state.escalation is not None and state.escalation.required
    if escalated is expect.require_escalation:
        return _passed("require_escalation", str(escalated))
    return _failed(
        "require_escalation",
        f"expected escalation={expect.require_escalation}, got {escalated} "
        f"(reasons={list(state.escalation.reasons) if state.escalation else []}, "
        f"suppressed={list(state.escalation.suppressed_by) if state.escalation else []})",
    )


def check_finding_category(state: GraphState, expect: EvalExpectation) -> AssertionResult | None:
    if expect.require_finding_category is None:
        return None
    categories = {
        finding.category
        for synthesis in (state.synthesis, state.arbiter_synthesis)
        if synthesis is not None
        for finding in synthesis.accepted_findings
    }
    if expect.require_finding_category in categories:
        return _passed("require_finding_category", expect.require_finding_category.value)
    return _failed(
        "require_finding_category",
        f"{expect.require_finding_category.value} not among accepted findings "
        f"{sorted(c.value for c in categories)}",
    )


def check_accepted_finding_origin(
    state: GraphState, expect: EvalExpectation
) -> AssertionResult | None:
    if expect.require_accepted_finding_origin is None:
        return None
    matching = [
        finding
        for synthesis in (state.synthesis, state.arbiter_synthesis)
        if synthesis is not None
        for finding in synthesis.accepted_findings
        if (
            expect.require_finding_category is None
            or finding.category is expect.require_finding_category
        )
    ]
    if any(finding.origin is expect.require_accepted_finding_origin for finding in matching):
        return _passed(
            "require_accepted_finding_origin",
            expect.require_accepted_finding_origin.value,
        )
    return _failed(
        "require_accepted_finding_origin",
        f"no matching accepted finding had origin {expect.require_accepted_finding_origin.value}",
    )


def check_candidate_delta(state: GraphState, expect: EvalExpectation) -> AssertionResult | None:
    if not expect.require_candidate_delta:
        return None
    deltas = [
        delta
        for synthesis in (state.synthesis, state.arbiter_synthesis)
        if synthesis is not None
        for delta in synthesis.candidate_delta
    ]
    if deltas:
        return _passed("require_candidate_delta", f"{len(deltas)} delta(s)")
    return _failed("require_candidate_delta", "no candidate delta was recorded")


def check_admitted_reranks(state: GraphState, expect: EvalExpectation) -> AssertionResult | None:
    expected_id = expect.expected_admitted_rerank_finding_id
    if expected_id is None and not expect.forbid_admitted_reranks:
        return None
    admitted = tuple(
        rerank.finding_id
        for synthesis in (state.synthesis, state.arbiter_synthesis)
        if synthesis is not None
        for rerank in synthesis.admitted_reranks
    )
    if expect.forbid_admitted_reranks:
        if not admitted:
            return _passed("forbid_admitted_reranks")
        return _failed("forbid_admitted_reranks", f"admitted reranks: {admitted!r}")
    if expected_id in admitted:
        return _passed("expected_admitted_rerank_finding_id", expected_id)
    return _failed(
        "expected_admitted_rerank_finding_id",
        f"expected {expected_id!r}, got {admitted!r}",
    )


def check_rejected_finding(state: GraphState, expect: EvalExpectation) -> AssertionResult | None:
    expected_id = expect.expected_rejected_finding_id
    if expected_id is None:
        return None
    rejected = {
        finding.finding_id: finding.reason_code.value if finding.reason_code else None
        for synthesis in (state.synthesis, state.arbiter_synthesis)
        if synthesis is not None
        for finding in synthesis.rejected_findings
    }
    if expected_id in rejected:
        return _passed("expected_rejected_finding_id", f"{expected_id}: {rejected[expected_id]}")
    return _failed(
        "expected_rejected_finding_id",
        f"expected {expected_id!r}, got {sorted(rejected)}",
    )


def check_no_evidence_leak(state: GraphState, expect: EvalExpectation) -> AssertionResult | None:
    """Every cited evidence id must belong to the subject that cites it."""
    if not expect.forbid_evidence_leak_between_subjects:
        return None
    evidence = state.evidence
    if evidence is None:
        return _passed("forbid_evidence_leak_between_subjects", "no evidence")

    offenders: list[str] = []
    for candidate_set in state.candidate_sets:
        own = {o.observation_id for o in evidence.observations_for(candidate_set.subject_id)}
        own |= {
            inference.inference_id
            for inference in evidence.inferences
            if set(inference.derived_from) <= own and inference.derived_from
        }
        for candidate in candidate_set.ordered:
            foreign = (
                set(candidate.supporting_evidence_ids + candidate.contradicting_evidence_ids) - own
            )
            offenders.extend(f"{candidate_set.subject_id}<-{item}" for item in sorted(foreign))
    if offenders:
        return _failed("forbid_evidence_leak_between_subjects", ", ".join(offenders))
    return _passed("forbid_evidence_leak_between_subjects")


def check_max_retries(state: GraphState, expect: EvalExpectation) -> AssertionResult | None:
    if expect.max_retries is None:
        return None
    if state.retries <= expect.max_retries:
        return _passed("max_retries", str(state.retries))
    return _failed("max_retries", f"{state.retries} retries exceeds {expect.max_retries}")


def check_user_claim_verdict(state: GraphState, expect: EvalExpectation) -> AssertionResult | None:
    if expect.expected_user_claim_verdict is None:
        return None
    actual = {decision.user_claim_verdict for decision in state.decisions}
    if expect.expected_user_claim_verdict in actual:
        return _passed("expected_user_claim_verdict", expect.expected_user_claim_verdict.value)
    return _failed(
        "expected_user_claim_verdict",
        f"expected {expect.expected_user_claim_verdict.value}, got "
        f"{sorted(verdict.value for verdict in actual)}",
    )


def check_no_claim_rejection(state: GraphState, expect: EvalExpectation) -> AssertionResult | None:
    """The restraint rule: a version is not thrown out from a photograph that cannot judge it."""
    if not expect.forbid_user_claim_rejection:
        return None
    rejected = [
        decision.subject_id
        for decision in state.decisions
        if decision.user_claim_verdict is UserClaimVerdict.REJECTED
    ]
    if rejected:
        return _failed(
            "forbid_user_claim_rejection",
            f"user version was rejected for {', '.join(rejected)}",
        )
    return _passed("forbid_user_claim_rejection")


def check_evidence_tier(state: GraphState, expect: EvalExpectation) -> AssertionResult | None:
    if expect.expected_evidence_tier is None:
        return None
    actual = {decision.evidence_tier for decision in state.decisions}
    if expect.expected_evidence_tier in actual:
        return _passed("expected_evidence_tier", str(expect.expected_evidence_tier))
    return _failed(
        "expected_evidence_tier",
        f"expected tier {expect.expected_evidence_tier}, got {sorted(actual)}",
    )


def check_alternative_named(state: GraphState, expect: EvalExpectation) -> AssertionResult | None:
    """A look-alike the model did not propose must still reach the user."""
    if not expect.require_alternative_named:
        return None
    named = any(
        finding.category is FindingCategory.OVERLOOKED_ALTERNATIVE
        for synthesis in (state.synthesis, state.arbiter_synthesis)
        if synthesis is not None
        for finding in synthesis.accepted_findings
    )
    if named:
        return _passed("require_alternative_named")
    return _failed("require_alternative_named", "no overlooked alternative was surfaced")


ALL_ASSERTIONS: tuple[Assertion, ...] = (
    check_expected_taxon,
    check_expected_taxon_display_name,
    check_expected_candidate_taxa,
    check_top_3,
    check_resolution,
    check_max_resolution,
    check_max_confidence,
    check_status,
    check_abstained,
    check_min_subjects,
    check_next_photo,
    check_escalation,
    check_finding_category,
    check_accepted_finding_origin,
    check_candidate_delta,
    check_admitted_reranks,
    check_rejected_finding,
    check_no_evidence_leak,
    check_max_retries,
    check_user_claim_verdict,
    check_no_claim_rejection,
    check_evidence_tier,
    check_alternative_named,
)


def evaluate(state: GraphState, expect: EvalExpectation) -> tuple[AssertionResult, ...]:
    """Run every applicable assertion. Assertions that do not apply are omitted."""
    results = [assertion(state, expect) for assertion in ALL_ASSERTIONS]
    return tuple(result for result in results if result is not None)
