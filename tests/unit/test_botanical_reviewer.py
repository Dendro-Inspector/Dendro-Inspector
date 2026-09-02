"""The botanical reviewer's deterministic contradiction boundary."""

from __future__ import annotations

from dendro_inspector.graph.state import GraphState
from dendro_inspector.nodes.botanical_reviewer import card_contradiction_findings
from dendro_inspector.schemas.candidates import Candidate, CandidateSet, SupportStrength
from dendro_inspector.schemas.evidence import (
    AttachmentStatus,
    EvidencePacket,
    Observation,
    ObservationSource,
    Subject,
)
from dendro_inspector.schemas.reviews import FindingCategory, RequiredAction, Severity
from dendro_inspector.schemas.taxon import Resolution


def _state(simple_case, observation: Observation) -> GraphState:
    return GraphState(
        case=simple_case,
        evidence=EvidencePacket(
            subjects=(Subject(subject_id="tree_1"),),
            observations=(observation,),
        ),
        candidate_sets=(
            CandidateSet(
                subject_id="tree_1",
                candidates=(
                    Candidate(
                        taxon="picea",
                        resolution=Resolution.GENUS,
                        score=SupportStrength.MODERATE,
                        rank=1,
                    ),
                ),
            ),
        ),
    )


def _fascicles(attachment: AttachmentStatus) -> Observation:
    return Observation(
        observation_id="contradiction",
        feature="needles.fascicles",
        value="two",
        subject_id="tree_1",
        source=ObservationSource.IMAGE,
        image_id="img-1",
        attachment=attachment,
    )


def test_full_authority_contradiction_is_major(simple_case, node_context):
    findings = card_contradiction_findings(
        _state(simple_case, _fascicles(AttachmentStatus.CONFIRMED_ATTACHED)),
        node_context,
    )

    assert len(findings) == 1
    assert findings[0].category is FindingCategory.BOTANICAL_CONTRADICTION
    assert findings[0].severity is Severity.MAJOR
    assert findings[0].required_action is RequiredAction.LOWER_CONFIDENCE


def test_unattached_contradiction_is_minor(simple_case, node_context):
    findings = card_contradiction_findings(
        _state(simple_case, _fascicles(AttachmentStatus.UNKNOWN)),
        node_context,
    )

    assert len(findings) == 1
    assert findings[0].category is FindingCategory.MISSING_DECISIVE_FEATURE
    assert findings[0].severity is Severity.MINOR
    assert findings[0].required_action is RequiredAction.NONE


def test_no_contradiction_adds_no_finding(simple_case, node_context):
    observation = Observation(
        observation_id="support",
        feature="needles.attachment",
        value="single_on_woody_peg",
        subject_id="tree_1",
        source=ObservationSource.IMAGE,
        image_id="img-1",
        attachment=AttachmentStatus.CONFIRMED_ATTACHED,
    )

    assert card_contradiction_findings(_state(simple_case, observation), node_context) == ()
