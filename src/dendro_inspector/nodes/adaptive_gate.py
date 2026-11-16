"""Adaptive gate: plan which reviewer models a round needs, from what code can establish.

Reached from the attachment authority gate only when ``GraphConfig.adaptive_routing`` is
not ``off``. It calls no model. It computes every reviewer's code-raised findings,
adjudicates them, composes the verdict those findings alone leave standing, and derives a
plan from that:

* ``full_review`` - anything ambiguous keeps all three reviewer models;
* ``targeted_review`` - only the reviewers an open question names;
* ``fast_path`` - no reviewer model, for an uncontested genus that code alone supports at
  high confidence, and only when ``adaptive_fast_path`` is switched on as well;
* ``request_photos`` - no reviewer model, when nothing was admitted: there is no claim to
  review, and only a new photograph can change the answer.

In ``shadow`` mode the plan is recorded and the full review runs, with every reviewer given
exactly the context it would have had. In ``enabled`` mode the graph follows the plan. The
reviewers it does not call still contribute their code-raised findings, which this node
places in the state, so adjudication, abstention, escalation and the final decision apply
the same deterministic safeguards on every route. Whether the arbiter runs remains the
escalation gate's decision, under its own settings.

Every rule here is a judgement about when a reviewer model is unlikely to change the
answer. None of them is measured yet: see ``docs/model-roles.md`` before switching the
mode to ``enabled``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from dendro_inspector.config import AdaptiveMode
from dendro_inspector.graph.executor import NodeContext
from dendro_inspector.graph.state import GraphState
from dendro_inspector.knowledge.taxon_cards import match_card
from dendro_inspector.nodes import botanical_reviewer, confidence_reviewer, confusion_reviewer
from dendro_inspector.nodes._support import code_only_review
from dendro_inspector.nodes.final_decision import decide_subject_base, discriminating_support
from dendro_inspector.nodes.review_synthesizer import adjudicate
from dendro_inspector.schemas.base import SHORT_TEXT_MAX
from dendro_inspector.schemas.decisions import AuthorityCheckStatus, DecisionStatus
from dendro_inspector.schemas.reviews import (
    FindingCategory,
    RequiredAction,
    Reviewer,
    ReviewFinding,
    ReviewResult,
    ReviewSynthesis,
)
from dendro_inspector.schemas.routing import (
    PLANNED_REVIEWERS,
    ProvisionalOutcome,
    ReviewQuestion,
    ReviewRoute,
    RoutingAssessment,
    RoutingCheck,
)
from dendro_inspector.schemas.taxon import Confidence, Resolution

NODE = "adaptive_gate"

_CODE_FINDINGS: dict[Reviewer, Callable[[GraphState, NodeContext], tuple[ReviewFinding, ...]]] = {
    Reviewer.BOTANICAL: botanical_reviewer.code_findings,
    Reviewer.CONFUSION: confusion_reviewer.code_findings,
    Reviewer.CONFIDENCE: confidence_reviewer.code_findings,
}


@dataclass(frozen=True, slots=True)
class SubjectFacts:
    """What code established about one subject before any reviewer model was asked."""

    subject_id: str
    outcome: ProvisionalOutcome | None = None
    """The verdict code alone composes. None when the subject has no admitted candidate."""
    leader: str | None = None
    runner_up: str | None = None
    discriminating_evidence_ids: tuple[str, ...] = ()
    """The leader's admitted observations its own card calls diagnostic."""
    contradicting_evidence_ids: tuple[str, ...] = ()
    """Evidence recorded against the leader, by the proposal or by its card."""
    leaders_close: bool = False
    species_proposed: bool = False


def code_reviews(state: GraphState, ctx: NodeContext) -> tuple[ReviewResult, ...]:
    """Each internal reviewer's code-raised findings, as a review with no model opinion."""
    return tuple(
        code_only_review(reviewer, _CODE_FINDINGS[reviewer](state, ctx))
        for reviewer in PLANNED_REVIEWERS
    )


def subject_facts(
    state: GraphState,
    ctx: NodeContext,
    reviews: tuple[ReviewResult, ...],
    synthesis: ReviewSynthesis,
) -> tuple[SubjectFacts, ...]:
    """Compose each subject's code-only verdict and the facts the plan is derived from.

    The verdict is a probe and is not recorded as a derivation: it is what the decision
    engine would say if no reviewer model added anything, which is the baseline a reviewer
    call has to be worth more than.
    """
    evidence = state.evidence
    if evidence is None:
        return ()
    probe = state.evolve(reviews=reviews, synthesis=synthesis)
    facts: list[SubjectFacts] = []
    for subject_id in state.subject_ids:
        candidate_set = state.candidates_for(subject_id)
        leader = candidate_set.leader if candidate_set is not None else None
        if candidate_set is None or leader is None:
            facts.append(SubjectFacts(subject_id=subject_id))
            continue
        decision = decide_subject_base(probe, ctx, candidate_set, record=False)
        card = ctx.knowledge.try_taxon(leader.taxon)
        card_hits = match_card(card, evidence, subject_id).contradiction_hits if card else ()
        runner_up = candidate_set.runner_up
        facts.append(
            SubjectFacts(
                subject_id=subject_id,
                outcome=ProvisionalOutcome(
                    subject_id=subject_id,
                    status=decision.status,
                    selected_taxon=decision.selected_taxon,
                    resolution=decision.resolution,
                    confidence=decision.confidence,
                ),
                leader=leader.taxon,
                runner_up=runner_up.taxon if runner_up is not None else None,
                discriminating_evidence_ids=(
                    discriminating_support(card, evidence, leader, subject_id) if card else ()
                ),
                contradicting_evidence_ids=tuple(
                    dict.fromkeys((*leader.contradicting_evidence_ids, *card_hits))
                ),
                leaders_close=candidate_set.leaders_are_close(),
                species_proposed=any(
                    candidate.resolution is Resolution.SPECIES
                    for candidate in candidate_set.candidates
                ),
            )
        )
    return tuple(facts)


def _case_checks(state: GraphState, facts: tuple[SubjectFacts, ...]) -> tuple[RoutingCheck, ...]:
    """The rules every plan other than full review needs. One failure keeps all reviewers."""
    evidence, quality, guard, plan = state.evidence, state.quality, state.guard, state.plan
    case = state.case
    unattached = quality.unattached_evidence_ids if quality is not None else ()
    sensitive = any(
        check.status is AuthorityCheckStatus.SENSITIVE for check in state.authority_checks
    )
    return (
        RoutingCheck(
            name="evidence_and_quality_present",
            passed=evidence is not None and quality is not None and guard is not None,
        ),
        # A correction round exists because review found something wrong with the
        # evidence. The second pass is not the place to review less.
        RoutingCheck(name="first_review_round", passed=state.retries == 0),
        RoutingCheck(
            name="no_user_claim_or_challenge",
            passed=not (
                case.user_claim
                or case.user_challenges_previous_result
                or (guard is not None and guard.user_challenges_previous_result)
            ),
        ),
        RoutingCheck(
            name="no_instruction_like_content",
            passed=not (
                (guard is not None and guard.instruction_like_detected)
                or (evidence is not None and evidence.instruction_like_content_detected)
            ),
        ),
        RoutingCheck(name="single_subject", passed=len(state.subject_ids) == 1),
        RoutingCheck(
            name="single_taxon_expected",
            passed=not (
                (evidence is not None and evidence.possible_multiple_taxa)
                or (plan is not None and plan.expect_multiple_subjects)
            ),
        ),
        RoutingCheck(
            name="attachment_settled",
            passed=not unattached and not sensitive,
            evidence_ids=unattached,
        ),
        RoutingCheck(name="no_open_world_candidate", passed=not state.open_world_candidates),
        RoutingCheck(
            name="no_species_level_candidate",
            passed=not any(fact.species_proposed for fact in facts),
        ),
    )


def _actionable(synthesis: ReviewSynthesis, subject_id: str) -> tuple[ReviewFinding, ...]:
    """Accepted code-raised findings that ask for something, for this subject or the case."""
    return tuple(
        finding
        for finding in synthesis.accepted_findings
        if finding.subject_id in (None, subject_id)
        and finding.required_action is not RequiredAction.NONE
    )


def _fast_path_checks(
    state: GraphState, fact: SubjectFacts, synthesis: ReviewSynthesis
) -> tuple[RoutingCheck, ...]:
    """The rules a case must also meet for no reviewer model to be asked at all.

    A photograph that could not be read is not among them. No reviewer sees it either, so
    calling one recovers nothing; the guard reports it and the answer says so.
    """
    quality, outcome = state.quality, fact.outcome
    subject_id = fact.subject_id
    actionable = _actionable(synthesis, subject_id)
    coverage_gap = quality is not None and (
        bool(quality.unmatchable_evidence_ids) or subject_id in quality.coverage_gap_subject_ids
    )
    return (
        RoutingCheck(
            name="named_at_genus_level",
            passed=outcome is not None
            and outcome.selected_taxon is not None
            and outcome.resolution is Resolution.GENUS
            and outcome.status in (DecisionStatus.IDENTIFIED, DecisionStatus.PROBABLE),
            subject_id=subject_id,
        ),
        RoutingCheck(
            name="high_confidence",
            passed=outcome is not None and outcome.confidence is Confidence.HIGH,
            subject_id=subject_id,
        ),
        RoutingCheck(
            name="discriminating_support",
            passed=bool(fact.discriminating_evidence_ids),
            subject_id=subject_id,
            evidence_ids=fact.discriminating_evidence_ids,
        ),
        RoutingCheck(
            name="no_contradicting_evidence",
            passed=not fact.contradicting_evidence_ids,
            subject_id=subject_id,
            evidence_ids=fact.contradicting_evidence_ids,
        ),
        RoutingCheck(
            name="leaders_separated", passed=not fact.leaders_close, subject_id=subject_id
        ),
        RoutingCheck(
            name="no_code_finding_requires_action",
            passed=not actionable,
            subject_id=subject_id,
            evidence_ids=tuple(
                dict.fromkeys(
                    evidence_id for finding in actionable for evidence_id in finding.evidence_ids
                )
            ),
        ),
        RoutingCheck(
            name="no_knowledge_coverage_gap",
            passed=not coverage_gap,
            subject_id=subject_id,
            evidence_ids=quality.unmatchable_evidence_ids if quality is not None else (),
        ),
        RoutingCheck(
            name="not_colour_dependent",
            passed=quality is not None and not quality.colour_dependence_detected,
            subject_id=subject_id,
        ),
    )


def _questions(fact: SubjectFacts, synthesis: ReviewSynthesis) -> tuple[ReviewQuestion, ...]:
    """The open questions about one subject, each addressed to the reviewer that owns it."""
    subject_id, outcome = fact.subject_id, fact.outcome
    questions: list[ReviewQuestion] = []

    def ask(reviewer: Reviewer, code: str, text: str, evidence_ids: tuple[str, ...] = ()) -> None:
        questions.append(
            ReviewQuestion(
                reviewer=reviewer,
                code=code,
                subject_id=subject_id,
                # Taxon ids are short in practice; clipped so an unusual one cannot turn a
                # routing record into a validation failure that ends the run.
                question=text[:SHORT_TEXT_MAX],
                evidence_ids=evidence_ids,
            )
        )

    def accepted(category: FindingCategory) -> tuple[ReviewFinding, ...]:
        return tuple(
            finding
            for finding in synthesis.accepted_findings
            if finding.subject_id in (None, subject_id) and finding.category is category
        )

    if fact.contradicting_evidence_ids or accepted(FindingCategory.BOTANICAL_CONTRADICTION):
        ask(
            Reviewer.BOTANICAL,
            "contradiction_against_leader",
            f"Evidence is recorded against the leading candidate {fact.leader}. Does it rule "
            "the candidate out, or is it compatible with it?",
            fact.contradicting_evidence_ids,
        )
    if fact.leaders_close:
        ask(
            Reviewer.CONFUSION,
            "leaders_not_separated",
            f"The leading candidates {fact.leader} and {fact.runner_up} are less than one "
            "strength step apart. Which visible feature separates them, and does the evidence "
            "show it?",
        )
    standing = accepted(FindingCategory.OVERLOOKED_ALTERNATIVE)
    if standing:
        named = ", ".join(
            dict.fromkeys(finding.proposed_taxon for finding in standing if finding.proposed_taxon)
        )
        ask(
            Reviewer.CONFUSION,
            "declared_look_alike_open",
            f"A look-alike of {fact.leader} that its card declares"
            f"{f' ({named})' if named else ''} is not ruled out by trusted evidence. Does "
            "anything visible separate them?",
        )
    if outcome is not None and outcome.selected_taxon is None:
        ask(
            Reviewer.CONFUSION,
            "leader_not_established",
            f"Code could not name the leading candidate {fact.leader} from this evidence. "
            "Does another admitted candidate explain the same observations better?",
        )
    if outcome is not None and outcome.selected_taxon is not None:
        ask(
            Reviewer.CONFIDENCE,
            "claim_strength",
            f"Code supports {outcome.selected_taxon} at {outcome.resolution.value} level with "
            f"{outcome.confidence.value} confidence. Is that level and confidence earned by "
            "the cited evidence, or is the claim too strong?",
            fact.discriminating_evidence_ids,
        )
    return tuple(questions)


def assess(
    state: GraphState,
    facts: tuple[SubjectFacts, ...],
    synthesis: ReviewSynthesis,
    *,
    applied: bool = False,
    fast_path: bool = False,
) -> RoutingAssessment:
    """Derive one round's review plan. Pure: the same state and facts give the same plan.

    ``synthesis`` is the adjudication of the code-raised findings alone. ``applied`` says
    whether the graph will follow the plan (``enabled``) or only record it (``shadow``).
    ``fast_path`` is the separate switch for calling no reviewer on a supported, uncontested
    genus; eligibility is evaluated and recorded either way.
    """
    provisional = tuple(fact.outcome for fact in facts if fact.outcome is not None)

    def plan(
        route: ReviewRoute,
        reasons: tuple[str, ...],
        checks: tuple[RoutingCheck, ...],
        *,
        questions: tuple[ReviewQuestion, ...] = (),
        eligible: bool = False,
    ) -> RoutingAssessment:
        called = (
            PLANNED_REVIEWERS
            if route is ReviewRoute.FULL_REVIEW
            else tuple(
                reviewer
                for reviewer in PLANNED_REVIEWERS
                if any(question.reviewer is reviewer for question in questions)
            )
        )
        return RoutingAssessment(
            round_index=state.retries + 1,
            route=route,
            applied=applied,
            fast_path_eligible=eligible,
            reasons=reasons,
            checks=checks,
            reviewers=called,
            skipped_reviewers=tuple(r for r in PLANNED_REVIEWERS if r not in called),
            questions=questions,
            provisional=provisional,
        )

    case_checks = _case_checks(state, facts)
    if not all(check.passed for check in case_checks):
        return plan(ReviewRoute.FULL_REVIEW, ("case_rule_failed",), case_checks)

    # The case rules guarantee exactly one subject from here on.
    fact = facts[0]
    if fact.outcome is None:
        return plan(ReviewRoute.REQUEST_PHOTOS, ("nothing_admitted",), case_checks)

    fast_checks = _fast_path_checks(state, fact, synthesis)
    checks = (*case_checks, *fast_checks)
    eligible = all(check.passed for check in fast_checks)
    if eligible and fast_path:
        return plan(ReviewRoute.FAST_PATH, ("supported_uncontested_genus",), checks, eligible=True)

    questions = _questions(fact, synthesis)
    asked = {question.reviewer for question in questions}
    if asked == set(PLANNED_REVIEWERS):
        # Nothing to skip. Full review carries no questions, so it is exactly the review
        # that runs without adaptive routing.
        return plan(
            ReviewRoute.FULL_REVIEW, ("every_reviewer_has_a_question",), checks, eligible=eligible
        )
    return plan(
        ReviewRoute.TARGETED_REVIEW,
        tuple(dict.fromkeys(question.code for question in questions)),
        checks,
        questions=questions,
        eligible=eligible,
    )


async def run(state: GraphState, ctx: NodeContext) -> GraphState:
    reviews = code_reviews(state, ctx)
    synthesis = adjudicate(
        reviews,
        evidence=state.evidence,
        knowledge=ctx.knowledge,
        without_opinion=frozenset(PLANNED_REVIEWERS),
    )
    plan = assess(
        state,
        subject_facts(state, ctx, reviews, synthesis),
        synthesis,
        applied=ctx.config.graph.adaptive_routing is AdaptiveMode.ENABLED,
        fast_path=ctx.config.graph.adaptive_fast_path,
    )
    ctx.recorder.record_routing(plan)
    # The reviewers the plan does not call still owe what their code raises. Reviewers it
    # does call merge their own code findings into their model review, as they always do.
    owed = tuple(review for review in reviews if not plan.calls(review.reviewer))
    return state.evolve(routing_assessment=plan, reviews=(*state.reviews, *owed))
