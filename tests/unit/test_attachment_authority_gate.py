"""Attachment authority: what the gate must and must not do.

Six cases, each aimed at one way the gate can be wrong. Three of them come from live
photographs — the standing lime whose single leaf label flipped the whole verdict, the same
lime with its attachment honestly unknown, and the branch-to-trunk projection that v0.7.0
read as independent corroboration.

The distinction every case is built around:

* **sensitivity** — the verdict moves if this evidence loses its owner. Ordinary. A leaf
  deciding the answer is what leaves are for;
* **risk** — the packet gives a structural reason to doubt that ownership.

Only their intersection may withdraw a claim. A gate that acts on sensitivity alone abstains
on every tree whose foliage it can see, which is not caution but the destruction of the best
evidence in the frame.
"""

from __future__ import annotations

import pytest

from dendro_inspector.graph.state import GraphState
from dendro_inspector.knowledge.candidate_validation import validate_candidate_set
from dendro_inspector.knowledge.evidence_authority import (
    AuthorityRiskReason,
    attachment_risk_for,
)
from dendro_inspector.nodes.attachment_authority_gate import check_subject
from dendro_inspector.nodes.final_decision import decide_subject
from dendro_inspector.schemas.candidates import Candidate, CandidateSet, SupportStrength
from dendro_inspector.schemas.decisions import AuthorityCheckStatus, DecisionStatus
from dendro_inspector.schemas.evidence import (
    AttachmentStatus,
    EvidencePacket,
    Observation,
    ObservationSource,
    Reliability,
    Subject,
    SubjectKind,
    Visibility,
)
from dendro_inspector.schemas.input import DeclaredObjectType
from dendro_inspector.schemas.taxon import Resolution


def _leaf(
    attachment: AttachmentStatus,
    *,
    observation_id: str = "obs-leaf",
    subject_id: str = "tree_1",
) -> Observation:
    return Observation(
        observation_id=observation_id,
        feature="leaf.shape",
        value="cordate_serrate",
        subject_id=subject_id,
        source=ObservationSource.IMAGE,
        image_id="img-1",
        visibility=Visibility.CLEAR,
        reliability=Reliability.HIGH,
        attachment=attachment,
    )


def _trunk(subject_id: str = "tree_1", observation_id: str = "obs-trunk") -> Observation:
    return Observation(
        observation_id=observation_id,
        feature="trunk.form",
        value="straight",
        subject_id=subject_id,
        source=ObservationSource.IMAGE,
        image_id="img-1",
        visibility=Visibility.CLEAR,
        reliability=Reliability.HIGH,
    )


def _tilia_packet(
    attachment: AttachmentStatus,
    *,
    through_component: bool = False,
) -> EvidencePacket:
    """One standing lime. The leaf either hangs on the trunk or on a named twig of it."""
    subjects = [Subject(subject_id="tree_1", kind=SubjectKind.STANDING_TREE)]
    leaf_subject = "tree_1"
    if through_component:
        subjects.append(
            Subject(
                subject_id="leafy_branch_1",
                kind=SubjectKind.BRANCH,
                parent_subject_id="tree_1",
            )
        )
        leaf_subject = "leafy_branch_1"
    packet = EvidencePacket(
        subjects=tuple(subjects),
        observations=(_leaf(attachment, subject_id=leaf_subject), _trunk()),
    )
    return packet.collapse_subject_components() if through_component else packet


def _tilia_proposal(subject_id: str = "tree_1") -> CandidateSet:
    return CandidateSet(
        subject_id=subject_id,
        candidates=(
            Candidate(
                taxon="tilia",
                resolution=Resolution.GENUS,
                supporting_evidence_ids=("obs-leaf",),
                score=SupportStrength.MODERATE,
                rank=1,
            ),
        ),
    )


def _state(simple_case, knowledge, evidence: EvidencePacket, *proposals: CandidateSet):
    case = simple_case.model_copy(update={"declared_object_type": DeclaredObjectType.STANDING_TREE})
    return GraphState(
        case=case,
        evidence=evidence,
        proposed_candidate_sets=proposals,
        candidate_sets=tuple(
            validate_candidate_set(proposal, evidence, knowledge) for proposal in proposals
        ),
    )


def _gate(state: GraphState, ctx, subject_id: str = "tree_1"):
    candidate_set = state.candidates_for(subject_id)
    assert candidate_set is not None
    return check_subject(state, ctx, candidate_set)


# --------------------------------------------------------------------------------------
# A. The decisive leaf is attached and nothing in the packet disputes it.
# --------------------------------------------------------------------------------------


def test_a_sole_attached_hinge_is_sensitive_but_not_risky_and_the_claim_survives(
    simple_case, node_context, knowledge
):
    """Sensitivity is not a reason to withdraw a claim.

    One tree, one identity root, a leaf the extractor placed on it. The verdict does depend
    on that leaf — demote it and the answer is gone — but no part of the packet suggests it
    grew anywhere else. Holding the claim back here would mean abstaining on every tree whose
    foliage is visible, which is the failure this test exists to prevent.
    """
    evidence = _tilia_packet(AttachmentStatus.CONFIRMED_ATTACHED)
    state = _state(simple_case, knowledge, evidence, _tilia_proposal())

    check, admitted = _gate(state, node_context)

    assert check.status is AuthorityCheckStatus.SENSITIVE
    assert check.critical_evidence_ids == ("obs-leaf",)
    assert check.risk_evidence_ids == ()
    assert check.policy_applied is False
    # The hinge is recorded, so the trace can still be asked how the answer was reached.
    assert check.actual_outcome is not None
    assert check.actual_outcome.taxon == "tilia"
    assert check.counterfactual_outcome is not None
    assert check.counterfactual_outcome.taxon is None

    # The candidate world handed to the reviewers is untouched.
    assert admitted.leader is not None
    assert admitted.leader.taxon == "tilia"

    decision = decide_subject(state.evolve(authority_checks=(check,)), node_context, admitted)
    assert decision.selected_taxon == "tilia"
    assert decision.status is DecisionStatus.PROBABLE
    assert decision.authority_check_status is AuthorityCheckStatus.SENSITIVE
    assert decision.authority_policy_applied is False


# --------------------------------------------------------------------------------------
# B. The same morphology, with ownership the extractor could not resolve.
# --------------------------------------------------------------------------------------


def test_b_unknown_attachment_is_risky_and_never_strengthens_the_returned_claim(
    simple_case, node_context, knowledge
):
    """The counterfactual records what confirmation would buy. It never buys it in advance."""
    evidence = _tilia_packet(AttachmentStatus.UNKNOWN)
    state = _state(simple_case, knowledge, evidence, _tilia_proposal())

    assert attachment_risk_for(evidence, "tree_1").reasons == (
        AuthorityRiskReason.OWNERSHIP_UNRESOLVED,
    )

    check, admitted = _gate(state, node_context)

    assert check.status is AuthorityCheckStatus.SENSITIVE
    assert check.risk_evidence_ids == ("obs-leaf",)
    assert check.policy_applied is True
    assert check.counterfactual_attachment is AttachmentStatus.CONFIRMED_ATTACHED
    assert check.counterfactual_outcome is not None
    assert check.counterfactual_outcome.taxon == "tilia"
    # The returned world stays the conservative one: no leader, no claim.
    assert admitted.leader is None

    decision = decide_subject(state.evolve(authority_checks=(check,)), node_context, admitted)
    assert decision.selected_taxon is None
    assert decision.status is DecisionStatus.INSUFFICIENT_EVIDENCE
    assert decision.counterfactual_taxon == "tilia"
    assert decision.best_next_photo is not None
    assert decision.best_next_photo.target == "leaf_attachment_photo"


# --------------------------------------------------------------------------------------
# C. The component projection buys nothing, in either direction.
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "attachment",
    [AttachmentStatus.CONFIRMED_ATTACHED, AttachmentStatus.UNKNOWN],
)
def test_c_a_model_proposed_component_changes_no_authority_verdict(
    simple_case, node_context, knowledge, attachment
):
    """``source_component_id`` is a rename of a model assertion, not a second witness.

    v0.7.0 read a branch-to-trunk projection as independent, code-owned provenance and let it
    waive the gate. It is not independent: the model proposed ``parent_subject_id``, code
    renamed it during collapse, and the rename was then credited as corroboration. Here the
    identical packet is run with and without that hop, and the authority verdict is the same
    both times — the projection neither clears risk nor creates it.
    """
    direct = _state(simple_case, knowledge, _tilia_packet(attachment), _tilia_proposal())
    projected_evidence = _tilia_packet(attachment, through_component=True)
    projected = _state(simple_case, knowledge, projected_evidence, _tilia_proposal())

    assert projected_evidence.observations_for("tree_1")[0].source_component_id == "leafy_branch_1"

    direct_check, _ = _gate(direct, node_context)
    projected_check, _ = _gate(projected, node_context)

    assert projected_check.status is direct_check.status
    assert projected_check.risk_evidence_ids == direct_check.risk_evidence_ids
    assert projected_check.policy_applied is direct_check.policy_applied
    assert projected_check.critical_evidence_ids == direct_check.critical_evidence_ids


# --------------------------------------------------------------------------------------
# D. Nothing detachable is in play at all.
# --------------------------------------------------------------------------------------


def test_d_bark_only_evidence_is_not_applicable(simple_case, node_context, knowledge):
    """There is no attachment question to answer about bark on its own trunk."""
    evidence = EvidencePacket(
        subjects=(Subject(subject_id="tree_1", kind=SubjectKind.STANDING_TREE),),
        observations=(
            Observation(
                observation_id="obs-bark",
                feature="bark.pattern",
                value="diamond_fissured",
                subject_id="tree_1",
                source=ObservationSource.IMAGE,
                image_id="img-1",
                visibility=Visibility.CLEAR,
                reliability=Reliability.MEDIUM,
            ),
            _trunk(),
        ),
    )
    proposal = CandidateSet(
        subject_id="tree_1",
        candidates=(
            Candidate(
                taxon="betula",
                resolution=Resolution.GENUS,
                supporting_evidence_ids=("obs-bark",),
                score=SupportStrength.WEAK,
                rank=1,
            ),
        ),
    )
    state = _state(simple_case, knowledge, evidence, proposal)

    check, _ = _gate(state, node_context)

    assert check.status is AuthorityCheckStatus.NOT_APPLICABLE
    assert check.risk_evidence_ids == ()
    assert check.policy_applied is False


def test_d_a_directly_photographed_leaf_is_not_applicable(simple_case, node_context, knowledge):
    """When the subject *is* the organ there is no trunk it could fail to belong to."""
    evidence = EvidencePacket(
        subjects=(Subject(subject_id="tree_1", kind=SubjectKind.DETACHED_PART),),
        observations=(_leaf(AttachmentStatus.CONFIRMED_DETACHED),),
    )
    case = simple_case.model_copy(update={"declared_object_type": DeclaredObjectType.LEAF})
    state = GraphState(
        case=case,
        evidence=evidence,
        proposed_candidate_sets=(_tilia_proposal(),),
        candidate_sets=(validate_candidate_set(_tilia_proposal(), evidence, knowledge),),
    )

    check, _ = _gate(state, node_context)

    assert check.status is AuthorityCheckStatus.NOT_APPLICABLE


# --------------------------------------------------------------------------------------
# E. A competing owner for the foliage, in a frame that admits it.
# --------------------------------------------------------------------------------------


def test_e_competing_ownership_drives_the_photo_request_without_leaking_into_the_claim(
    simple_case, node_context, knowledge
):
    """Foliage on its own root cannot support this trunk, and is not silently borrowed.

    The frame holds a standing tree and a leafy branch the extractor could not attach to it,
    with ``possible_multiple_taxa`` set. Two guarantees meet here: the same-subject rule keeps
    that foliage out of the trunk's candidate support, and the shared risk predicate still
    names it — so the system asks for the photograph that would settle ownership rather than
    quietly reasoning from evidence it has not earned.
    """
    evidence = EvidencePacket(
        subjects=(
            Subject(subject_id="tree_1", kind=SubjectKind.STANDING_TREE),
            Subject(subject_id="loose_branch_1", kind=SubjectKind.BRANCH),
        ),
        observations=(
            _trunk(),
            _leaf(AttachmentStatus.CONFIRMED_ATTACHED, subject_id="loose_branch_1"),
        ),
        possible_multiple_taxa=True,
    )
    risk = attachment_risk_for(evidence, "tree_1")
    assert risk.reasons == (AuthorityRiskReason.COMPETING_OWNERSHIP,)
    assert risk.risky_evidence_ids == frozenset({"obs-leaf"})

    state = _state(simple_case, knowledge, evidence, _tilia_proposal())
    check, admitted = _gate(state, node_context)

    # The trunk never gets to stand on the loose branch's leaf.
    assert admitted.leader is None
    assert check.status is AuthorityCheckStatus.NOT_APPLICABLE

    decision = decide_subject(state.evolve(authority_checks=(check,)), node_context, admitted)
    assert decision.selected_taxon is None
    assert decision.best_next_photo is not None
    assert decision.best_next_photo.target == "leaf_attachment_photo"


# --------------------------------------------------------------------------------------
# F. Two subjects. One doubtful, one not.
# --------------------------------------------------------------------------------------


def test_f_one_subjects_ambiguity_does_not_poison_the_other(simple_case, node_context, knowledge):
    """Risk is per observation, so a doubtful leaf on the left cannot cost the right its claim.

    Both subjects are sensitive — each verdict rests on its own leaf. Only the second one is
    risky. Poisoning by ``possible_multiple_taxa``, by subject count, or by any other packet-
    wide signal would show up here as the first tree losing an answer it earned.
    """
    evidence = EvidencePacket(
        subjects=(
            Subject(subject_id="tree_1", kind=SubjectKind.STANDING_TREE),
            Subject(subject_id="tree_2", kind=SubjectKind.STANDING_TREE),
        ),
        observations=(
            _leaf(AttachmentStatus.CONFIRMED_ATTACHED),
            _trunk(),
            _leaf(
                AttachmentStatus.UNKNOWN,
                observation_id="obs-leaf-2",
                subject_id="tree_2",
            ),
            _trunk(subject_id="tree_2", observation_id="obs-trunk-2"),
        ),
        possible_multiple_taxa=True,
    )
    second = _tilia_proposal("tree_2").model_copy(
        update={
            "candidates": (
                Candidate(
                    taxon="tilia",
                    resolution=Resolution.GENUS,
                    supporting_evidence_ids=("obs-leaf-2",),
                    score=SupportStrength.MODERATE,
                    rank=1,
                ),
            )
        }
    )
    state = _state(simple_case, knowledge, evidence, _tilia_proposal(), second)

    first_check, first_set = _gate(state, node_context, "tree_1")
    second_check, second_set = _gate(state, node_context, "tree_2")

    assert first_check.subject_id == "tree_1"
    assert first_check.risk_evidence_ids == ()
    assert first_check.policy_applied is False
    assert first_set.leader is not None
    assert first_set.leader.taxon == "tilia"

    assert second_check.subject_id == "tree_2"
    assert second_check.risk_evidence_ids == ("obs-leaf-2",)
    assert second_check.policy_applied is True
    assert second_set.leader is None

    # Two records, each describing a world that actually existed for its own subject.
    assert {first_check.subject_id, second_check.subject_id} == {"tree_1", "tree_2"}


# --------------------------------------------------------------------------------------
# The fourth status: detachable evidence nothing rests on.
# --------------------------------------------------------------------------------------


def test_untested_ownership_is_reported_as_not_testable_not_as_a_clean_check(
    simple_case, node_context, knowledge
):
    """ "We ran the check and found nothing" is a different fact from "there was nothing to run".

    A live standing-tree photograph carried foliage of unresolved ownership that no proposed
    candidate leaned on. A boolean reported that as ``false`` — indistinguishable from a
    counterfactual that ran and moved nothing.
    """
    evidence = EvidencePacket(
        subjects=(Subject(subject_id="tree_1", kind=SubjectKind.STANDING_TREE),),
        observations=(
            _leaf(AttachmentStatus.UNKNOWN),
            Observation(
                observation_id="obs-bark",
                feature="bark.pattern",
                value="diamond_fissured",
                subject_id="tree_1",
                source=ObservationSource.IMAGE,
                image_id="img-1",
                visibility=Visibility.CLEAR,
                reliability=Reliability.MEDIUM,
            ),
        ),
    )
    bark_only = CandidateSet(
        subject_id="tree_1",
        candidates=(
            Candidate(
                taxon="betula",
                resolution=Resolution.GENUS,
                supporting_evidence_ids=("obs-bark",),
                score=SupportStrength.WEAK,
                rank=1,
            ),
        ),
    )
    state = _state(simple_case, knowledge, evidence, bark_only)

    check, _ = _gate(state, node_context)

    assert check.status is AuthorityCheckStatus.NOT_TESTABLE
    assert check.risk_evidence_ids == ()
    assert check.policy_applied is False
