"""Attachment authority gate.

Deterministic, and deliberately placed *before* the reviewers. It answers one question per
subject: does the verdict rest on detachable evidence whose ownership only a model asserted?

Two design commitments live here.

**Sensitivity is not risk.** Two facts are easy to confuse and mean opposite things:

* *sensitivity* — the verdict changes if this detachable evidence loses its owner;
* *risk* — something in the packet makes that ownership doubtful.

Almost every honest organ-level identification is sensitive: demoting the decisive leaf will
nearly always collapse the claim, because deciding the answer is what the leaf is for. A gate
that withdraws a claim on sensitivity alone is therefore not controlling uncertainty — it is
destroying high-tier evidence for being useful, and it abstains on every tree whose foliage
it can see. The rule is::

    policy_applied = bool(sensitive_ids & risky_ids)

and only that intersection is demoted. Both sides are per observation, so one unplaceable
branch on the left of the frame cannot poison needles correctly traced to the trunk on the
right, and a second subject in the packet cannot poison the first.

Risk comes from :func:`attachment_risk_for`, shared with the photo planner so that the
evidence this gate holds a claim back for is the evidence it then asks a photograph of.

**A component projection is not a second witness.** ``attachment=confirmed_attached`` is a
field a stochastic model filled in. So is ``parent_subject_id``, and so therefore is the
``source_component_id`` the deterministic collapse writes from it. Reading that projection
back as corroboration closes a circle: the model asserts the leaf belongs to the trunk, code
renames the assertion, and the renamed assertion is credited as independent proof. It buys
nothing here, in either direction — the same packet with and without the component reaches
the same authority verdict.

**The gate runs before the reviewers, not after them.** Deciding the evidence world after
three reviewers have already judged the permissive one leaves their findings attached to a
world the verdict no longer describes. Narrowing the candidate sets here means every model
downstream sees exactly the evidence world the answer will be computed from.

The counterfactual never *strengthens* a claim. Promoting untrusted evidence is recorded so
the hinge is visible in the trace; it never becomes the returned answer.

**Known limit.** Code cannot adjudicate visual truth it did not see. If the extractor states
``confirmed_attached`` about a crossing branch and flags no ambiguity, nothing deterministic
in this packet contradicts it. Closing that class needs an independent provenance reviewer, a
continuity photograph, or the user — not another branch in this function.
"""

from __future__ import annotations

from dendro_inspector.graph.executor import NodeContext
from dendro_inspector.graph.state import GraphState
from dendro_inspector.knowledge.candidate_validation import validate_candidate_set
from dendro_inspector.knowledge.evidence_authority import attachment_risk_for
from dendro_inspector.knowledge.evidence_hierarchy import (
    requires_attachment,
    resolve_evidence_observations,
)
from dendro_inspector.nodes.final_decision import decide_subject_base
from dendro_inspector.nodes.photo_planner import DIRECT_DETACHABLE_TYPES, effective_object_type
from dendro_inspector.observability.logging import get_logger
from dendro_inspector.schemas.candidates import Candidate, CandidateSet
from dendro_inspector.schemas.decisions import (
    AuthorityCheckStatus,
    AuthorityCheckTrace,
    AuthorityOutcome,
    FinalDecision,
)
from dendro_inspector.schemas.evidence import AttachmentStatus, EvidencePacket, Observation

NODE = "attachment_authority_gate"


def _outcome(decision: FinalDecision) -> AuthorityOutcome:
    return AuthorityOutcome(
        status=decision.status,
        taxon=decision.selected_taxon,
        resolution=decision.resolution,
        confidence=decision.confidence,
    )


def _signature(decision: FinalDecision) -> tuple[object, ...]:
    """Fields whose change makes attachment authority material to the scientific verdict."""
    return (
        decision.selected_taxon,
        decision.status,
        decision.resolution,
        decision.confidence,
    )


def _with_attachment(
    evidence: EvidencePacket,
    evidence_ids: frozenset[str],
    attachment: AttachmentStatus,
) -> EvidencePacket:
    observations = tuple(
        observation.model_copy(update={"attachment": attachment})
        if observation.observation_id in evidence_ids
        else observation
        for observation in evidence.observations
    )
    return evidence.model_copy(update={"observations": observations})


def _decide_in_world(
    state: GraphState,
    ctx: NodeContext,
    source: CandidateSet,
    evidence: EvidencePacket,
) -> tuple[FinalDecision, CandidateSet]:
    """Admit ``source`` against one evidence world and decide it deterministically."""
    admitted = validate_candidate_set(source, evidence, ctx.knowledge)
    world = state.evolve(evidence=evidence)
    decision = decide_subject_base(world, ctx, admitted, already_reranked=True, record=False)
    return decision, admitted


def _attached_support(
    evidence: EvidencePacket,
    candidate: Candidate,
    subject_id: str,
) -> tuple[Observation, ...]:
    """Detachable evidence this candidate rests on that a model called attached."""
    found: list[Observation] = []
    seen: set[str] = set()
    for evidence_id in candidate.supporting_evidence_ids:
        for observation in resolve_evidence_observations(evidence, evidence_id, subject_id):
            if (
                observation.observation_id not in seen
                and requires_attachment(observation.feature)
                and observation.attachment is AttachmentStatus.CONFIRMED_ATTACHED
            ):
                seen.add(observation.observation_id)
                found.append(observation)
    return tuple(found)


def _unknown_support(
    evidence: EvidencePacket,
    proposed: CandidateSet,
) -> tuple[Observation, ...]:
    """Detachable evidence the model proposed to lean on but could not place."""
    found: list[Observation] = []
    seen: set[str] = set()
    for candidate in proposed.ordered:
        for evidence_id in candidate.supporting_evidence_ids:
            for observation in resolve_evidence_observations(
                evidence,
                evidence_id,
                proposed.subject_id,
            ):
                if (
                    observation.observation_id not in seen
                    and requires_attachment(observation.feature)
                    and observation.attachment is AttachmentStatus.UNKNOWN
                ):
                    seen.add(observation.observation_id)
                    found.append(observation)
    return tuple(found)


def _hinge_observations(
    state: GraphState,
    ctx: NodeContext,
    source: CandidateSet,
    observations: tuple[Observation, ...],
    baseline: FinalDecision,
    attachment: AttachmentStatus,
) -> tuple[Observation, ...]:
    """Which of ``observations`` move the outcome when re-stated at ``attachment``.

    One at a time first, so the trace names the single observation that carries the claim.
    A group that only matters together is reported as a group rather than as nothing.
    """
    evidence = state.evidence
    if evidence is None:  # pragma: no cover - the caller proves this
        msg = "authority gate reached without evidence"
        raise RuntimeError(msg)

    hinges = [
        observation
        for observation in observations
        if _signature(
            _decide_in_world(
                state,
                ctx,
                source,
                _with_attachment(evidence, frozenset({observation.observation_id}), attachment),
            )[0]
        )
        != _signature(baseline)
    ]
    if hinges or len(observations) < 2:
        return tuple(hinges)

    combined = _with_attachment(
        evidence,
        frozenset(observation.observation_id for observation in observations),
        attachment,
    )
    if _signature(_decide_in_world(state, ctx, source, combined)[0]) != _signature(baseline):
        return observations
    return ()


def check_subject(
    state: GraphState,
    ctx: NodeContext,
    candidate_set: CandidateSet,
) -> tuple[AuthorityCheckTrace, CandidateSet]:
    """Return this subject's authority record and the candidate world the graph may use."""
    evidence = state.evidence
    if evidence is None:  # pragma: no cover - run() proves this
        msg = "authority gate reached without evidence"
        raise RuntimeError(msg)

    subject_id = candidate_set.subject_id
    source = state.proposed_candidates_for(subject_id) or candidate_set
    actual, admitted = _decide_in_world(state, ctx, source, evidence)
    outcome = _outcome(actual)

    def record(
        status: AuthorityCheckStatus,
        *,
        critical_evidence_ids: tuple[str, ...] = (),
        risk_evidence_ids: tuple[str, ...] = (),
        policy_applied: bool = False,
        counterfactual_outcome: AuthorityOutcome | None = None,
        counterfactual_attachment: AttachmentStatus | None = None,
    ) -> AuthorityCheckTrace:
        return AuthorityCheckTrace(
            subject_id=subject_id,
            status=status,
            actual_outcome=outcome,
            critical_evidence_ids=critical_evidence_ids,
            risk_evidence_ids=risk_evidence_ids,
            policy_applied=policy_applied,
            counterfactual_outcome=counterfactual_outcome,
            counterfactual_attachment=counterfactual_attachment,
        )

    # The subject *is* the detached organ. There is no trunk it could fail to belong to.
    if effective_object_type(state, subject_id) in DIRECT_DETACHABLE_TYPES:
        return record(AuthorityCheckStatus.NOT_APPLICABLE), candidate_set
    if not any(
        requires_attachment(observation.feature)
        for observation in evidence.observations_for(subject_id)
    ):
        return record(AuthorityCheckStatus.NOT_APPLICABLE), candidate_set

    leader = admitted.leader
    attached = _attached_support(evidence, leader, subject_id) if leader is not None else ()
    unknown = _unknown_support(evidence, source)

    # Detachable evidence exists, but no candidate rests on it: there is no counterfactual
    # to run, which is a different fact from having run one and found nothing.
    if not attached and not unknown:
        return record(AuthorityCheckStatus.NOT_TESTABLE), candidate_set

    hinges = _hinge_observations(state, ctx, source, attached, actual, AttachmentStatus.UNKNOWN)
    if hinges:
        sensitive_ids = tuple(observation.observation_id for observation in hinges)
        risky_ids = attachment_risk_for(evidence, subject_id).risky_evidence_ids
        critical_risky_ids = tuple(
            evidence_id for evidence_id in sensitive_ids if evidence_id in risky_ids
        )
        # Demote the intersection only. A claim standing on evidence whose ownership nobody
        # questions is a claim that should stand, even when other evidence beside it is
        # doubtful.
        conservative, conservative_set = _decide_in_world(
            state,
            ctx,
            source,
            _with_attachment(
                evidence,
                frozenset(critical_risky_ids or sensitive_ids),
                AttachmentStatus.UNKNOWN,
            ),
        )
        applied = bool(critical_risky_ids) and _signature(conservative) != _signature(actual)
        return (
            record(
                AuthorityCheckStatus.SENSITIVE,
                critical_evidence_ids=sensitive_ids,
                risk_evidence_ids=critical_risky_ids,
                policy_applied=applied,
                counterfactual_outcome=_outcome(conservative),
                counterfactual_attachment=AttachmentStatus.UNKNOWN,
            ),
            conservative_set if applied else candidate_set,
        )

    # The other direction. Confirming what is currently unplaceable would change the answer;
    # recording that keeps the hinge visible without ever letting it strengthen the claim.
    promoted = _hinge_observations(
        state, ctx, source, unknown, actual, AttachmentStatus.CONFIRMED_ATTACHED
    )
    if promoted:
        critical_ids = tuple(observation.observation_id for observation in promoted)
        stronger, _ = _decide_in_world(
            state,
            ctx,
            source,
            _with_attachment(
                evidence, frozenset(critical_ids), AttachmentStatus.CONFIRMED_ATTACHED
            ),
        )
        # Unresolved ownership is the risk signal in its purest form: the extractor itself
        # could not place this evidence, so the conservative world is already the real one
        # and the record exists to say what confirming it would buy.
        return (
            record(
                AuthorityCheckStatus.SENSITIVE,
                critical_evidence_ids=critical_ids,
                risk_evidence_ids=critical_ids,
                policy_applied=True,
                counterfactual_outcome=_outcome(stronger),
                counterfactual_attachment=AttachmentStatus.CONFIRMED_ATTACHED,
            ),
            candidate_set,
        )

    return record(AuthorityCheckStatus.NOT_SENSITIVE), candidate_set


def demoted_evidence_ids(checks: tuple[AuthorityCheckTrace, ...]) -> frozenset[str]:
    """Observation ids whose model-asserted attachment the gate refused to spend."""
    return frozenset(
        evidence_id
        for check in checks
        if check.policy_applied and check.counterfactual_attachment is AttachmentStatus.UNKNOWN
        for evidence_id in check.risk_evidence_ids
    )


async def run(state: GraphState, ctx: NodeContext) -> GraphState:
    evidence = state.evidence
    if evidence is None or not state.candidate_sets:
        return state.evolve(authority_checks=())

    logger = get_logger(NODE)
    checks: list[AuthorityCheckTrace] = []
    gated: list[CandidateSet] = []
    for candidate_set in state.candidate_sets:
        check, admitted = check_subject(state, ctx, candidate_set)
        checks.append(check)
        gated.append(admitted)
        if check.policy_applied:
            logger.warning(
                "attachment_authority_applied",
                extra={
                    "case_id": state.case.case_id,
                    "subject_id": check.subject_id,
                    "critical_evidence_ids": list(check.critical_evidence_ids),
                    "counterfactual_attachment": check.counterfactual_attachment,
                },
            )

    # The demotion travels with the candidate sets. Leaving `confirmed_attached` in the
    # packet the reviewers read would hand them the very assertion the gate just refused,
    # and every later recomputation would silently re-admit it.
    withheld = demoted_evidence_ids(tuple(checks))
    if withheld:
        evidence = _with_attachment(evidence, withheld, AttachmentStatus.UNKNOWN)
    return state.evolve(
        evidence=evidence,
        candidate_sets=tuple(gated),
        authority_checks=tuple(checks),
    )
