"""The coverage audit reports; it never admits, converts or invents evidence."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from dendro_inspector.cli import app
from dendro_inspector.config import DEFAULT_DOMAIN_PROMPT_PATH, DEFAULT_PROMPT_MANIFEST_PATH
from dendro_inspector.evaluation.runner import load_cases
from dendro_inspector.evaluation.trace_replay import (
    MISSING_FROM_LEGACY_TRACE,
    ReplayUnavailableError,
    StoredRecord,
    fixture_from_trace,
    packet_from_trace,
    proposals_from_trace,
    replay,
    replay_fidelity,
    replay_gaps,
    reviews_from_trace,
    trace_schema_version,
)
from dendro_inspector.knowledge.candidate_validation import validate_candidate_set_with_report
from dendro_inspector.knowledge.coverage_audit import (
    CandidateOutcome,
    CoverageStatus,
    audit_observation,
    audit_packet,
)
from dendro_inspector.observability.trace import TraceRecorder
from dendro_inspector.runner import run_case
from dendro_inspector.schemas.candidates import Candidate, CandidateSet, SupportStrength
from dendro_inspector.schemas.evidence import (
    AttachmentStatus,
    EvidencePacket,
    GeneratedEvidencePacket,
    Observation,
    ObservationSource,
    Reliability,
    Subject,
    SubjectKind,
    Visibility,
    WoodSurface,
)
from dendro_inspector.schemas.reviews import FindingStatus, ReviewResult
from dendro_inspector.schemas.taxon import Resolution
from tests.conftest import make_config

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
        audit = audit_observation(_obs("o", "bark.ridge_form", "rounded"), knowledge)
        assert audit.status is CoverageStatus.VALID_BUT_UNCOVERED
        assert audit.reason.startswith("feature absent")

    def test_moss_and_lichen_are_context_not_a_gap(self, knowledge):
        audit = audit_observation(_obs("o", "bark.epiphytes", "moss_on_base"), knowledge)
        assert audit.status is CoverageStatus.CONTEXT_ONLY

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
    @pytest.mark.parametrize("surface", list(WoodSurface))
    def test_recorded_wood_surfaces_preserve_replay_trust(self, surface):
        packet = _packet(
            _obs("bark", "bark.texture", "scaly_plates"),
            _obs("pores", "pores.arrangement", "ring_porous").model_copy(
                update={"wood_surface": surface}
            ),
        )
        recorder = TraceRecorder("wood-trace")
        recorder.record_evidence(packet)
        trace = recorder.build().model_dump(mode="json")
        record = StoredRecord("wood-trace", "inline", trace, (), False)

        assert trace["evidence_observations"][1]["wood_surface"] == surface.value
        assert packet_from_trace(trace).observations[1].wood_surface is surface
        assert replay_fidelity(record) == ()
        generated = fixture_from_trace(trace)["responses"]["primary:evidence_extractor"]
        assert (
            GeneratedEvidencePacket.model_validate(generated).observations[1].wood_surface
            is surface
        )

    def test_legacy_wood_observations_replay_as_unknown_surface(self):
        trace = {
            **TRACE,
            "evidence_observations": [
                {
                    **TRACE["evidence_observations"][1],
                    "feature": "pores.arrangement",
                    "value": "ring_porous",
                    "trust": "full_positive",
                    "tier": 5,
                }
            ],
        }
        generated = fixture_from_trace(trace)["responses"]["primary:evidence_extractor"]
        observation = GeneratedEvidencePacket.model_validate(generated).observations[0]
        assert observation.wood_surface is WoodSurface.UNKNOWN
        record = StoredRecord("legacy-wood-trace", "inline", trace, (), False)
        assert replay_fidelity(record) == ("obs-2",)

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


def _recorded_finding(finding_id: str, origin: str, **updates: Any) -> dict[str, Any]:
    return {
        "finding_ref": f"internal-1/1-confusion/{finding_id[1:]}",
        "finding_id": finding_id,
        "origin": origin,
        "category": "confidence_miscalibration",
        "severity": "major",
        "status": "accepted",
        "reason_code": "references_visible_evidence",
        "summary": "Bark texture alone is weak support for the genus.",
        "evidence_ids": ["obs-2"],
        "raised_subject_id": None,
        "subject_id": "standing_tree",
        "proposed_taxon": None,
        "required_action": "lower_confidence",
        "impact": "confidence_change",
        **updates,
    }


def _recorded_reviewer(reviewer: str, *findings: dict[str, Any], **fields: Any) -> dict[str, Any]:
    return {
        "reviewer": reviewer,
        "status": "pass_with_findings" if findings else "pass",
        "subject_id": None,
        "recommended_resolution": None,
        "recommended_confidence": None,
        "recommended_candidates": [],
        "findings": list(findings),
        "findings_omitted": 0,
        **fields,
    }


#: `TRACE` as a run would record it today: its reviews are in it. The confusion review
#: carries a finding the code raised and one the model raised; the confidence review holds
#: the claim to family with no finding behind it.
RECORDED_TRACE: dict[str, Any] = {
    **TRACE,
    "trace_schema_version": 2,
    "review_rounds": [
        {
            "stage": "internal",
            "round": 1,
            "reviewers": [
                _recorded_reviewer(
                    "confusion",
                    _recorded_finding("f1", "deterministic", evidence_ids=[]),
                    _recorded_finding("f2", "model"),
                ),
                _recorded_reviewer("confidence", recommended_resolution="family"),
            ],
        }
    ],
}

PUBLIC_CASES = load_cases(Path(__file__).resolve().parents[2] / "evals" / "public")


def _run_public(case, root: Path):
    return asyncio.run(run_case(case.input, config=make_config(case.scenario), root=root))


def _stored(case, live) -> StoredRecord:
    return StoredRecord(
        case.case_id,
        "inline",
        live.trace.model_dump(mode="json"),
        (),
        case.input.user_has_field_context,
    )


def _verdicts(decisions) -> list[tuple[Any, ...]]:
    return [
        (d.subject_id, d.status, d.selected_taxon, d.resolution, d.confidence) for d in decisions
    ]


class TestRecordedReviewReplay:
    """A trace that recorded its reviews can be re-decided with them, offline."""

    @pytest.mark.parametrize("case", PUBLIC_CASES, ids=lambda case: case.case_id)
    def test_a_trace_replays_to_the_verdict_its_run_reached(self, case, config, repo_root: Path):
        """Trace completeness, measured: the record alone reproduces each public verdict."""
        live = _run_public(case, repo_root)

        state = asyncio.run(
            replay(_stored(case, live), config=config, root=repo_root, reviews="recorded")
        )

        assert _verdicts(state.decisions) == _verdicts(live.state.decisions)

    def test_neutral_reviewers_cannot_reproduce_a_verdict_review_decided(
        self, config, repo_root: Path
    ):
        """Why the mode exists: the neutral replay names a tree the run abstained on."""
        (case,) = [case for case in PUBLIC_CASES if case.case_id == "abstention-visible-001"]
        live = _run_public(case, repo_root)
        record = _stored(case, live)

        neutral = asyncio.run(replay(record, config=config, root=repo_root))
        recorded = asyncio.run(replay(record, config=config, root=repo_root, reviews="recorded"))

        assert live.state.decisions[0].abstained
        assert not neutral.decisions[0].abstained
        assert recorded.decisions[0].abstained

    def test_recorded_reviews_bind_the_replayed_verdict(self, config, repo_root: Path):
        record = StoredRecord("replay-fixture", "inline", RECORDED_TRACE, (), False)

        neutral = asyncio.run(replay(record, config=config, root=repo_root))
        recorded = asyncio.run(replay(record, config=config, root=repo_root, reviews="recorded"))

        assert neutral.decisions[0].resolution is Resolution.GENUS
        assert recorded.decisions[0].resolution is Resolution.FAMILY

    def test_only_the_models_own_findings_are_scripted_back(self):
        """The replayed node raises the code's findings again, from the same evidence.

        Scripting them as model output would file each twice, once under the wrong origin.
        """
        response = reviews_from_trace(RECORDED_TRACE)["reviewer:confusion_reviewer"]

        assert [finding["finding_id"] for finding in response["findings"]] == ["f2"]
        # Raised with no subject named: adjudication resolves the scope again by itself.
        assert "subject_id" not in response["findings"][0]
        review = ReviewResult.model_validate(response)
        assert review.findings[0].status is FindingStatus.OPEN

    def test_a_reviewer_the_run_never_called_keeps_a_neutral_pass_and_is_reported(self):
        responses = reviews_from_trace(RECORDED_TRACE)

        assert responses["reviewer:botanical_reviewer"]["findings"] == []
        assert responses["reviewer:confidence_reviewer"]["recommended_resolution"] == "family"
        gaps = replay_gaps(RECORDED_TRACE, "recorded")
        assert any(gap.startswith("review:botanical: not called") for gap in gaps)
        assert any(gap.startswith("review:arbiter: not called") for gap in gaps)
        assert not any(gap.startswith("review:confidence") for gap in gaps)

    def test_a_retried_run_replays_its_last_internal_round_and_says_so(self):
        first = RECORDED_TRACE["review_rounds"][0]
        second = {**first, "round": 2, "reviewers": [_recorded_reviewer("confusion")]}
        trace = {**RECORDED_TRACE, "review_rounds": [first, second]}

        responses = reviews_from_trace(trace)

        assert responses["reviewer:confusion_reviewer"]["findings"] == []
        assert "review_rounds: only the last of 2 internal rounds replays" in replay_gaps(
            trace, "recorded"
        )

    def test_a_trace_without_recorded_reviews_is_refused_not_replayed_neutrally(
        self, config, repo_root: Path
    ):
        """A neutral verdict filed as a recorded one would be read as what review decided."""
        record = StoredRecord("replay-fixture", "inline", TRACE, (), False)

        with pytest.raises(ReplayUnavailableError, match="review_rounds"):
            asyncio.run(replay(record, config=config, root=repo_root, reviews="recorded"))

    def test_a_legacy_trace_reports_what_it_never_recorded(self):
        assert trace_schema_version(TRACE) == 1
        assert trace_schema_version(RECORDED_TRACE) == 2
        assert replay_gaps(TRACE) == tuple(
            f"{what}: {MISSING_FROM_LEGACY_TRACE}"
            for what in ("subject_kinds", "inferences", "packet_flags", "plan")
        )
        assert f"review_rounds: {MISSING_FROM_LEGACY_TRACE}" in replay_gaps(TRACE, "recorded")

    def test_recorded_subject_kinds_are_used_instead_of_guessed_from_the_id(self):
        trace = {
            **RECORDED_TRACE,
            "evidence_context": {
                "revision": 1,
                "subjects": [
                    {"subject_id": "standing_tree", "kind": "log", "image_ids": ["img-1"]},
                    # Nothing was observed on it; it still gets a verdict of its own.
                    {"subject_id": "background", "kind": "standing_tree", "image_ids": ["img-2"]},
                ],
            },
        }

        packet = packet_from_trace(trace)

        assert [(subject.subject_id, subject.kind) for subject in packet.subjects] == [
            ("standing_tree", SubjectKind.LOG),
            ("background", SubjectKind.STANDING_TREE),
        ]


def test_coverage_replay_with_recorded_reviews(tmp_path, monkeypatch):
    """One report can hold records that replay with their reviews and records that cannot."""
    monkeypatch.setenv("DENDRO_REQUIRE_DISCRIMINATING_SUPPORT", "true")
    monkeypatch.setenv("DENDRO_DOMAIN_PROMPT_PATH", str(DEFAULT_DOMAIN_PROMPT_PATH))
    monkeypatch.setenv("DENDRO_PROMPT_MANIFEST_PATH", str(DEFAULT_PROMPT_MANIFEST_PATH))
    for name, trace in (("recorded", RECORDED_TRACE), ("legacy", TRACE)):
        (tmp_path / f"{name}.json").write_text(
            json.dumps({"case_id": name, "result": {"trace": trace}}), encoding="utf-8"
        )
    report = tmp_path / "out" / "report.json"
    report.parent.mkdir()
    labels = tmp_path / "labels.json"
    labels.write_text(json.dumps({"recorded": "pinus", "legacy": "pinus"}), encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "coverage-audit",
            str(tmp_path / "recorded.json"),
            str(tmp_path / "legacy.json"),
            "--replay",
            "--reviews",
            "recorded",
            "--labels",
            str(labels),
            "--json-out",
            str(report),
        ],
    )

    assert result.exit_code == 0, result.exception
    payload = json.loads(report.read_text(encoding="utf-8"))
    by_case = {record["case_id"]: record for record in payload["records"]}
    assert by_case["recorded"]["replay_reviews"] == "recorded"
    assert by_case["recorded"]["replay_unavailable"] is False
    assert by_case["recorded"]["replayed"][0]["resolution"] == "family"
    assert by_case["legacy"]["replay_unavailable"] is True
    assert by_case["legacy"]["replayed"] == []
    assert f"review_rounds: {MISSING_FROM_LEGACY_TRACE}" in by_case["legacy"]["replay_gaps"]
    assert payload["summary"]["replay_unavailable"] == 1
    # The reason a record did not replay is reported, never only the fact.
    reason = f"review_rounds: {MISSING_FROM_LEGACY_TRACE}"
    assert by_case["legacy"]["replay_unavailable_reason"] == reason
    assert by_case["recorded"]["replay_unavailable_reason"] is None
    assert payload["summary"]["replay_unavailable_reasons"] == {reason: 1}
    assert f"replay unavailable because: {reason}" in result.output
    assert by_case["recorded"]["replay_settings"] == "configured"
    assert by_case["recorded"]["replayed_grade"]["outcome"] == "broader_only"
    assert by_case["legacy"]["replayed_grade"] is None
    assert payload["summary"]["labelled_movements"] == [
        {
            "case_id": "recorded",
            "source": str(tmp_path / "recorded.json"),
            "stored": "no_name",
            "replayed": "broader_only",
        }
    ]


def test_coverage_replay_rejects_an_unknown_review_mode(tmp_path):
    result = CliRunner().invoke(
        app, ["coverage-audit", str(tmp_path), "--replay", "--reviews", "guessed"]
    )

    assert result.exit_code != 0


def test_coverage_replay_rejects_an_unknown_settings_source(tmp_path):
    result = CliRunner().invoke(
        app, ["coverage-audit", str(tmp_path), "--replay", "--settings", "guessed"]
    )

    assert result.exit_code != 0


def test_coverage_replay_under_recorded_settings_refuses_a_trace_without_them(
    tmp_path, monkeypatch
):
    """A trace that records no settings is not replayed under the caller's instead."""
    monkeypatch.setenv("DENDRO_DOMAIN_PROMPT_PATH", str(DEFAULT_DOMAIN_PROMPT_PATH))
    monkeypatch.setenv("DENDRO_PROMPT_MANIFEST_PATH", str(DEFAULT_PROMPT_MANIFEST_PATH))
    (tmp_path / "recorded.json").write_text(
        json.dumps({"case_id": "recorded", "result": {"trace": RECORDED_TRACE}}),
        encoding="utf-8",
    )
    report = tmp_path / "report.json"

    result = CliRunner().invoke(
        app,
        [
            "coverage-audit",
            str(tmp_path / "recorded.json"),
            "--replay",
            "--reviews",
            "recorded",
            "--settings",
            "recorded",
            "--json-out",
            str(report),
        ],
    )

    assert result.exit_code == 0, result.exception
    record = json.loads(report.read_text(encoding="utf-8"))["records"][0]
    assert record["replay_settings"] == "recorded"
    assert record["replay_unavailable"] is True
    assert record["replay_unavailable_reason"].startswith("settings: the trace records no")
    assert record["replayed"] == []


@pytest.mark.parametrize(
    ("environment", "flag", "expected_taxon"),
    [
        ("true", "--no-require-discriminating-support", "pinus"),
        ("false", "--require-discriminating-support", None),
        ("true", None, None),
        ("false", None, "pinus"),
    ],
)
def test_coverage_replay_policy_override(tmp_path, monkeypatch, environment, flag, expected_taxon):
    """Compare supporting-only evidence without changing deployment configuration."""
    monkeypatch.setenv("DENDRO_REQUIRE_DISCRIMINATING_SUPPORT", environment)
    monkeypatch.setenv("DENDRO_DOMAIN_PROMPT_PATH", str(DEFAULT_DOMAIN_PROMPT_PATH))
    monkeypatch.setenv("DENDRO_PROMPT_MANIFEST_PATH", str(DEFAULT_PROMPT_MANIFEST_PATH))
    trace = {
        **TRACE,
        "evidence_observations": [
            TRACE["evidence_observations"][1],
            {
                **TRACE["evidence_observations"][1],
                "observation_id": "obs-3",
                "feature": "crown.shape",
                "value": "broad_irregular_spreading",
                "tier": 1,
            },
        ],
    }
    record = tmp_path / "record.json"
    record.write_text(json.dumps({"result": {"trace": trace}}), encoding="utf-8")
    report = tmp_path / "report.json"
    args = ["coverage-audit", str(record), "--replay", "--json-out", str(report)]
    if flag is not None:
        args.append(flag)
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.exception
    decisions = json.loads(report.read_text(encoding="utf-8"))["records"][0]["replayed"]
    assert decisions[0]["taxon"] == expected_taxon
    assert decisions[0]["confidence"] == "low"
