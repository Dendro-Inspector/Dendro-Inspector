"""The coverage audit reports; it never admits, converts or invents evidence."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from dendro_inspector.evaluation.trace_replay import (
    StoredRecord,
    fixture_from_trace,
    packet_from_trace,
    proposals_from_trace,
    replay,
    replay_fidelity,
)
from dendro_inspector.knowledge.candidate_validation import validate_candidate_set_with_report
from dendro_inspector.knowledge.coverage_audit import (
    CandidateOutcome,
    CoverageStatus,
    audit_observation,
    audit_packet,
)
from dendro_inspector.schemas.candidates import Candidate, CandidateSet, SupportStrength
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
from dendro_inspector.schemas.taxon import Resolution

SUBJECT = "tree_1"


def _obs(
    observation_id: str,
    feature: str,
    value: str,
    *,
    attachment: AttachmentStatus | None = None,
    reliability: Reliability = Reliability.HIGH,
    visibility: Visibility = Visibility.CLEAR,
) -> Observation:
    return Observation(
        observation_id=observation_id,
        feature=feature,
        value=value,
        subject_id=SUBJECT,
        source=ObservationSource.IMAGE,
        image_id="img-1",
        visibility=visibility,
        reliability=reliability,
        attachment=attachment,
    )


def _status(observation: Observation, knowledge) -> CoverageStatus:
    return audit_observation(observation, knowledge).status


class TestObservationStatus:
    def test_a_declared_reading_is_supported(self, knowledge):
        audit = audit_observation(_obs("o", "bark.texture", "scaly_plates"), knowledge)
        assert audit.status is CoverageStatus.VALID_AND_SUPPORTED
        assert "pinus" in audit.supporting_genera

    def test_a_reading_shared_across_genera_is_reported_as_such(self, knowledge):
        audit = audit_observation(_obs("o", "lenticels.orientation", "horizontal"), knowledge)
        assert len(audit.supporting_genera) > 1
        assert audit.diagnostic_for == ()

    def test_an_undeclared_value_is_uncovered_not_converted(self, knowledge):
        audit = audit_observation(_obs("o", "crown.shape", "broad_irregular_spreading"), knowledge)
        assert audit.status is CoverageStatus.VALID_BUT_UNCOVERED
        assert audit.supporting_genera == ()
        assert audit.reason.startswith("value not declared")

    def test_an_undeclared_feature_is_uncovered(self, knowledge):
        audit = audit_observation(_obs("o", "bark.epiphytes", "moss_on_base"), knowledge)
        assert audit.status is CoverageStatus.VALID_BUT_UNCOVERED
        assert audit.reason.startswith("feature absent")

    def test_colour_is_uncovered_by_policy(self, knowledge):
        audit = audit_observation(_obs("o", "bark.colour", "orange_red"), knowledge)
        assert audit.status is CoverageStatus.VALID_BUT_UNCOVERED
        assert audit.reason.startswith("colour")

    def test_an_unknown_organ_is_invalid_vocabulary(self, knowledge):
        assert _status(_obs("o", "aura.mood", "calm"), knowledge) is (
            CoverageStatus.INVALID_VOCABULARY
        )

    def test_a_token_filed_under_a_sibling_path_is_ambiguous(self, knowledge):
        # scaly_plates is declared under bark.texture, not bark.pattern.
        audit = audit_observation(_obs("o", "bark.pattern", "scaly_plates"), knowledge)
        assert audit.status is CoverageStatus.AMBIGUOUS_MAPPING
        assert "bark.texture" in audit.reason

    def test_an_explicit_contradiction_is_contradictory(self, knowledge):
        observation = _obs(
            "o",
            "needles.arrangement",
            "clustered_on_short_shoots",
            attachment=AttachmentStatus.CONFIRMED_ATTACHED,
        )
        audit = audit_observation(observation, knowledge)
        # Larch declares it strong; pine, spruce and fir contradict it. Support wins the
        # status, and the contradictions stay visible in the roles.
        assert audit.status is CoverageStatus.VALID_AND_SUPPORTED
        assert {"pinus", "picea", "abies"} <= {
            role.taxon_id for role in audit.roles if role.role == "contradiction"
        }

    def test_failed_readings_are_not_readings(self, knowledge):
        observation = _obs(
            "o", "needles.fascicles", "unresolved", attachment=AttachmentStatus.CONFIRMED_ATTACHED
        )
        assert _status(observation, knowledge) is CoverageStatus.NOT_A_READING

    def test_unattached_detachable_evidence_is_attachment_uncertain(self, knowledge):
        observation = _obs("o", "leaf.shape", "palmate_lobed", attachment=AttachmentStatus.UNKNOWN)
        assert _status(observation, knowledge) is CoverageStatus.SUBJECT_ATTACHMENT_UNCERTAIN

    def test_a_low_reliability_reading_is_untrusted(self, knowledge):
        observation = _obs(
            "o",
            "bark.texture",
            "scaly_plates",
            reliability=Reliability.LOW,
            visibility=Visibility.OBSCURED,
        )
        assert _status(observation, knowledge) is CoverageStatus.UNTRUSTED_READING

    def test_context_is_context(self, knowledge):
        assert _status(_obs("o", "context.site", "meadow"), knowledge) is (
            CoverageStatus.CONTEXT_ONLY
        )


def _packet(*observations: Observation) -> EvidencePacket:
    return EvidencePacket(
        subjects=(Subject(subject_id=SUBJECT, kind=SubjectKind.STANDING_TREE),),
        observations=observations,
    )


def _proposal(*candidates: Candidate) -> CandidateSet:
    return CandidateSet(subject_id=SUBJECT, candidates=candidates)


def _candidate(taxon: str, *support: str, rank: int = 1) -> Candidate:
    return Candidate(
        taxon=taxon,
        resolution=Resolution.GENUS,
        supporting_evidence_ids=support,
        score=SupportStrength.WEAK,
        rank=rank,
    )


class TestCandidateOutcome:
    def test_reasons_name_the_failed_condition(self, knowledge):
        packet = _packet(
            _obs("bark", "bark.pattern", "white_papery_with_black_marks"),
            _obs("crown", "crown.shape", "broad_irregular_spreading"),
        )
        proposal = _proposal(
            _candidate("betula", "bark"),
            _candidate("populus_alba", rank=2),  # cites nothing
            _candidate("pinus", "crown", rank=3),  # cites only uncovered evidence
            _candidate("pinus_sylvestris", "crown", rank=4),  # no card
        )
        outcomes = {
            audit.taxon: audit for audit in audit_packet(packet, knowledge, (proposal,)).candidates
        }
        assert outcomes["betula"].outcome is CandidateOutcome.ADMITTED
        assert outcomes["populus_alba"].outcome is CandidateOutcome.NO_CITED_SUPPORT
        assert outcomes["pinus"].outcome is CandidateOutcome.ALL_SUPPORT_DROPPED
        assert outcomes["pinus"].dropped == (
            ("crown", "feature_not_declared: crown.shape=broad_irregular_spreading"),
        )
        assert outcomes["pinus_sylvestris"].outcome is CandidateOutcome.NO_CARD

    def test_the_audit_never_changes_admission(self, knowledge):
        packet = _packet(_obs("crown", "crown.shape", "broad_irregular_spreading"))
        proposal = _proposal(_candidate("pinus", "crown"))
        before = validate_candidate_set_with_report(proposal, packet, knowledge)
        audit_packet(packet, knowledge, (proposal,))
        after = validate_candidate_set_with_report(proposal, packet, knowledge)
        assert before == after
        assert after.candidate_set.ordered == ()


#: A trace in the shape the runner records (observations and candidate validations only).
TRACE: dict[str, Any] = {
    "case_id": "replay-fixture",
    "evidence_observations": [
        {
            "observation_id": "obs-1",
            "subject_id": "standing_tree",
            "feature": "needles.fascicles",
            "value": "two",
            "source": "image",
            "image_id": "img-1",
            "visibility": "clear",
            "reliability": "high",
            "attachment": "confirmed_attached",
            "trust": "full_positive",
            "tier": 6,
        },
        {
            "observation_id": "obs-2",
            "subject_id": "standing_tree",
            "feature": "bark.texture",
            "value": "scaly_plates",
            "source": "image",
            "image_id": "img-1",
            "visibility": "clear",
            "reliability": "medium",
            "attachment": None,
            "trust": "full_positive",
            "tier": 3,
        },
    ],
    "candidate_validations": [
        {
            "subject_id": "standing_tree",
            "proposed_taxa": ["pinus", "picea"],
            "admitted": [
                {
                    "taxon": "pinus",
                    "resolution": "genus",
                    "score": "moderate",
                    "supporting_evidence_ids": ["obs-1", "obs-2"],
                    "contradicting_evidence_ids": [],
                }
            ],
            "dropped_support": [
                {"taxon": "picea", "evidence_id": "obs-2", "reason": "value_not_declared"}
            ],
            "demoted_scores": ["pinus:strong->moderate"],
        }
    ],
}


class TestTraceReplay:
    def test_proposals_are_rebuilt_with_the_models_original_score(self):
        (proposal,) = proposals_from_trace(TRACE)
        pinus, picea = proposal["candidates"]
        assert pinus["score"] == "strong"
        assert picea["supporting_evidence_ids"] == ["obs-2"]
        assert picea["resolution"] == "genus"

    def test_rebuilt_trust_matches_the_recorded_trust(self):
        record = StoredRecord("replay-fixture", "inline", TRACE, (), False)
        assert replay_fidelity(record) == ()
        assert packet_from_trace(TRACE).subjects[0].kind is SubjectKind.STANDING_TREE

    def test_attested_evidence_is_replayed_as_the_model_saw_it(self):
        trace = {
            **TRACE,
            "evidence_observations": [
                {**TRACE["evidence_observations"][0], "attachment": "user_attested"}
            ],
        }
        rows = fixture_from_trace(trace)["responses"]["primary:evidence_extractor"]
        assert rows["observations"][0]["attachment"] == "unknown"

    def test_replay_runs_the_real_graph_offline(self, config, repo_root: Path):
        record = StoredRecord("replay-fixture", "inline", TRACE, (), False)
        state = asyncio.run(replay(record, config=config, root=repo_root))
        (decision,) = state.decisions
        assert decision.selected_taxon == "pinus"
        assert decision.resolution is Resolution.GENUS
