"""Adversarial checks of provenance, subject scope and concurrent execution."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest

from dendro_inspector.config import Role
from dendro_inspector.graph.definition import REVIEW_FANOUT
from dendro_inspector.graph.executor import _run_fanout
from dendro_inspector.graph.state import EvidenceQualityReport, GraphState
from dendro_inspector.knowledge.candidate_validation import validate_candidate_set
from dendro_inspector.nodes import abstain, candidate_generator, evidence_extractor, final_decision
from dendro_inspector.nodes._support import merge_findings
from dendro_inspector.nodes.botanical_reviewer import card_contradiction_findings
from dendro_inspector.nodes.review_synthesizer import adjudicate
from dendro_inspector.providers.base import StructuredOutputError
from dendro_inspector.providers.fake import ScriptedProvider
from dendro_inspector.providers.registry import ProviderRegistry
from dendro_inspector.schemas.candidates import (
    Candidate,
    CandidateProposal,
    CandidateSet,
    SupportStrength,
)
from dendro_inspector.schemas.evidence import (
    AttachmentStatus,
    EvidencePacket,
    GeneratedEvidencePacket,
    Observation,
    ObservationSource,
    Subject,
)
from dendro_inspector.schemas.input import CaseInput, ImageRef
from dendro_inspector.schemas.reviews import (
    Reviewer,
    ReviewFinding,
    ReviewResult,
    ReviewStatus,
    ReviewSynthesis,
)
from dendro_inspector.schemas.taxon import Confidence, Resolution


def _state(case, knowledge):
    subjects = ("tree_a", "tree_b")
    observations = tuple(
        Observation(
            observation_id=f"{subject}-cone",
            subject_id=subject,
            feature="cones.scale_shape",
            value="woody_umbo",
            source=ObservationSource.IMAGE,
            image_id="img-1",
            attachment=AttachmentStatus.CONFIRMED_ATTACHED,
        )
        for subject in subjects
    )
    evidence = EvidencePacket(
        subjects=tuple(Subject(subject_id=subject) for subject in subjects),
        observations=observations,
    )
    sets = tuple(
        validate_candidate_set(
            CandidateSet(
                subject_id=subject,
                candidates=(
                    Candidate(
                        taxon="pinus",
                        resolution=Resolution.GENUS,
                        supporting_evidence_ids=(f"{subject}-cone",),
                        score=SupportStrength.STRONG,
                        rank=1,
                    ),
                ),
            ),
            evidence,
            knowledge,
        )
        for subject in subjects
    )
    return GraphState(
        case=case,
        evidence=evidence,
        candidate_sets=sets,
        quality=EvidenceQualityReport(sufficient=True, usable_subject_ids=subjects),
    )


def _synthesized(state, ctx, *reviews):
    return state.evolve(
        synthesis=adjudicate(reviews, evidence=state.evidence, knowledge=ctx.knowledge)
    )


def _verdict(state, ctx, subject="tree_a"):
    return final_decision.decide_subject(state, ctx, state.candidates_for(subject))


def _lower_finding(**updates):
    return ReviewFinding.model_validate(
        {
            "finding_id": "lower-a",
            "category": "confidence_miscalibration",
            "severity": "major",
            "subject_id": "tree_a",
            "evidence_ids": ("tree_a-cone",),
            "summary": "This observation warrants a more conservative confidence.",
            "required_action": "lower_confidence",
            "impact": "confidence_change",
            **updates,
        }
    )


@pytest.mark.parametrize("reference", ["observation", "subject", "limitation"])
def test_extraction_rejects_unsupplied_image_references(
    reference, simple_case, node_context, tmp_path
):
    image = tmp_path / "supplied.jpg"
    image.write_bytes(b"synthetic provider input")
    case = simple_case.model_copy(update={"images": (ImageRef(image_id="img-1", path=image),)})
    state = _state(case, node_context.knowledge)
    payload = state.evidence.model_dump()
    if reference == "observation":
        payload["observations"][0]["image_id"] = "invented-image"
    elif reference == "subject":
        payload["subjects"][0]["image_ids"] = ("invented-image",)
    else:
        payload["image_limitations"] = ({"image_id": "invented-image"},)
    provider = ScriptedProvider(
        {"primary:evidence_extractor": GeneratedEvidencePacket.model_validate(payload)}
    )
    ctx = replace(node_context, providers=ProviderRegistry({Role.PRIMARY: provider}))

    with pytest.raises(StructuredOutputError, match="image"):
        asyncio.run(evidence_extractor.run(GraphState(case=case), ctx))


@pytest.mark.parametrize("declared", [False, True])
def test_extraction_cannot_invent_visual_evidence_without_transmitted_images(
    declared, simple_case, node_context, tmp_path
):
    case = simple_case.model_copy(
        update={
            "images": (ImageRef(image_id="img-1", path=tmp_path / "missing.jpg"),)
            if declared
            else (),
        }
    )
    state = _state(case, node_context.knowledge)
    provider = ScriptedProvider(
        {
            "primary:evidence_extractor": GeneratedEvidencePacket.model_validate(
                state.evidence.model_dump()
            )
        }
    )
    ctx = replace(node_context, providers=ProviderRegistry({Role.PRIMARY: provider}))

    with pytest.raises(StructuredOutputError, match="image"):
        asyncio.run(evidence_extractor.run(GraphState(case=case), ctx))


def test_model_subject_cannot_suppress_another_subjects_deterministic_finding(
    simple_case, node_context
):
    state = _state(simple_case, node_context.knowledge)
    contradiction = Observation(
        observation_id="b-broadleaf",
        subject_id="tree_b",
        feature="leaf.type",
        value="broadleaf_simple",
        source=ObservationSource.IMAGE,
        image_id="img-1",
        attachment=AttachmentStatus.CONFIRMED_ATTACHED,
    )
    state = state.evolve(
        evidence=state.evidence.model_copy(
            update={"observations": (*state.evidence.observations, contradiction)}
        )
    )
    review = merge_findings(
        ReviewResult(reviewer=Reviewer.BOTANICAL, status=ReviewStatus.PASS, subject_id="tree_a"),
        card_contradiction_findings(state, node_context),
    )
    result = _synthesized(state, node_context, review)

    assert [f.subject_id for f in result.synthesis.accepted_findings] == ["tree_b"]
    assert _verdict(result, node_context, "tree_b").confidence is Confidence.MEDIUM


def test_recommendations_affect_only_their_subject(simple_case, node_context):
    state = _state(simple_case, node_context.knowledge)
    review = ReviewResult(
        reviewer=Reviewer.CONFIDENCE,
        status=ReviewStatus.PASS,
        subject_id="tree_a",
        recommended_resolution=Resolution.FAMILY,
        recommended_confidence=Confidence.LOW,
    )
    result = _synthesized(state, node_context, review)

    assert _verdict(result, node_context).resolution is Resolution.FAMILY
    assert _verdict(result, node_context).confidence is Confidence.LOW
    assert _verdict(result, node_context, "tree_b").resolution is Resolution.GENUS
    assert _verdict(result, node_context, "tree_b").confidence is Confidence.HIGH


@pytest.mark.parametrize("rejected_finding", [False, True])
def test_another_review_cannot_waive_an_accepted_downgrade(
    rejected_finding, simple_case, node_context
):
    state = _state(simple_case, node_context.knowledge)
    valid = ReviewResult(
        reviewer=Reviewer.BOTANICAL,
        status=ReviewStatus.PASS_WITH_FINDINGS,
        findings=(_lower_finding(),),
    )
    other = ReviewResult(
        reviewer=Reviewer.CONFIDENCE,
        status=ReviewStatus.PASS_WITH_FINDINGS if rejected_finding else ReviewStatus.PASS,
        subject_id="tree_a",
        recommended_confidence=Confidence.HIGH,
        findings=(_lower_finding(finding_id="invalid", evidence_ids=("invented-id",)),)
        if rejected_finding
        else (),
    )

    result = _synthesized(state, node_context, valid, other)

    assert _verdict(result, node_context).confidence is Confidence.MEDIUM


@pytest.mark.parametrize("usable", [("tree_a", "tree_b"), ("tree_a",)])
def test_every_detected_subject_receives_a_terminal_decision(usable, simple_case, node_context):
    state = _state(simple_case, node_context.knowledge)
    state = state.evolve(quality=EvidenceQualityReport(sufficient=True, usable_subject_ids=usable))
    provider = ScriptedProvider(
        {"primary:candidate_generator": CandidateProposal(sets=(state.candidate_sets[0],))}
    )
    ctx = replace(node_context, providers=ProviderRegistry({Role.PRIMARY: provider}))

    async def run():
        generated = await candidate_generator.run(state, ctx)
        return await final_decision.run(generated, ctx)

    result = asyncio.run(run())

    assert [d.subject_id for d in result.decisions] == ["tree_a", "tree_b"]
    assert result.decisions[1].selected_taxon is None
    assert result.decisions[1].best_next_photo is not None


def test_candidate_prompt_excludes_cards_without_admissible_support(
    simple_case, node_context, monkeypatch
):
    state = _state(simple_case, node_context.knowledge)
    prompts = []

    async def capture(**kwargs):
        prompts.append(kwargs["prompt"])
        return CandidateProposal(sets=state.candidate_sets)

    monkeypatch.setattr(candidate_generator, "request_structured", capture)
    asyncio.run(candidate_generator.run(state, node_context))
    section = prompts[0].split("## Knowledge cards (project data)", 1)[1]
    payload = json.loads(section.split("```json\n", 1)[1].split("\n```", 1)[0])

    assert {card["taxon_id"] for card in payload["taxon_cards"]} == {"pinus"}


def test_fanout_failure_cancels_and_joins_siblings_and_records_every_member(
    simple_case, node_context
):
    state = _state(simple_case, node_context.knowledge)

    async def run():
        ready = asyncio.Event()
        waiting = asyncio.Event()
        started = []
        cancelled = []

        async def fail(st, ctx):
            await ready.wait()
            raise RuntimeError("synthetic reviewer failure")

        async def sibling(st, ctx):
            started.append(ctx.review_projection.reviewer)
            if len(started) == 2:
                ready.set()
            try:
                await waiting.wait()
            except asyncio.CancelledError:
                cancelled.append(ctx.review_projection.reviewer)
                raise
            return st

        registry = dict(zip(REVIEW_FANOUT, (fail, sibling, sibling), strict=True))
        try:
            with pytest.raises(RuntimeError, match="synthetic reviewer failure"):
                await _run_fanout(REVIEW_FANOUT, registry, state, node_context)
            assert len(cancelled) == 2
            events = node_context.recorder.build().events
            assert tuple(event.node for event in events) == tuple(n.value for n in REVIEW_FANOUT)
            assert all(event.status.value != "ok" for event in events)
            assert all(event.reviewer_projection is not None for event in events)
        finally:
            waiting.set()

    asyncio.run(run())


def test_a_reused_finding_id_cannot_borrow_another_findings_floor(simple_case, node_context):
    state = _state(simple_case, node_context.knowledge)
    valid = ReviewResult.model_validate(
        {"reviewer": "botanical", "status": "pass_with_findings", "findings": (_lower_finding(),)}
    )
    other = ReviewResult.model_validate(
        {
            "reviewer": "confidence",
            "status": "pass_with_findings",
            "subject_id": "tree_a",
            "recommended_confidence": "high",
            "findings": (_lower_finding(category="missing_decisive_feature"),),
        }
    )
    result = _synthesized(state, node_context, valid, other)

    assert len(result.synthesis.accepted_findings) == 2
    assert _verdict(result, node_context).confidence is Confidence.MEDIUM


def test_ambiguous_bare_recommendation_does_not_become_a_case_wide_cap(simple_case, node_context):
    state = _state(simple_case, node_context.knowledge)
    review = ReviewResult.model_validate(
        {"reviewer": "confidence", "status": "pass", "recommended_confidence": "low"}
    )
    result = _synthesized(state, node_context, review)

    assert not result.synthesis.recommendations
    assert _verdict(result, node_context).confidence is Confidence.HIGH
    assert _verdict(result, node_context, "tree_b").confidence is Confidence.HIGH


def test_image_reference_failure_is_repaired_within_the_structured_retry_budget(
    simple_case, node_context, tmp_path, monkeypatch
):
    image = tmp_path / "supplied.jpg"
    image.write_bytes(b"synthetic provider input")
    case = simple_case.model_copy(update={"images": (ImageRef(image_id="img-1", path=image),)})
    good = GeneratedEvidencePacket.model_validate(
        _state(case, node_context.knowledge).evidence.model_dump()
    )
    payload = good.model_dump()
    payload["observations"][0]["image_id"] = "invented-image"
    bad = GeneratedEvidencePacket.model_validate(payload)
    provider = ScriptedProvider({})
    prompts = []

    async def generate(**kwargs):
        prompts.append(kwargs["prompt"])
        return bad if len(prompts) == 1 else good

    monkeypatch.setattr(provider, "generate_structured", generate)
    ctx = replace(node_context, providers=ProviderRegistry({Role.PRIMARY: provider}))

    result = asyncio.run(evidence_extractor.run(GraphState(case=case), ctx))

    assert result.evidence == good.to_evidence_packet()
    assert len(prompts) == 2
    assert "unavailable image ids" in prompts[1]
    ctx.recorder.record_node("evidence_extractor")
    call = ctx.recorder.build().events[0].provider_calls[0]
    assert (call.attempts, call.validation_failures) == (2, 1)


def test_fake_replay_still_cannot_invent_undeclared_image_ids(simple_case, node_context):
    case = simple_case.model_copy(update={"images": ()})

    with pytest.raises(StructuredOutputError, match="image"):
        asyncio.run(evidence_extractor.run(GraphState(case=case), node_context))


def test_cancelling_the_fanout_joins_all_reviewers(simple_case, node_context):
    state = _state(simple_case, node_context.knowledge)

    async def run():
        ready = asyncio.Event()
        waiting = asyncio.Event()
        started = []
        cancelled = []

        async def sibling(st, ctx):
            started.append(ctx.review_projection.reviewer)
            if len(started) == len(REVIEW_FANOUT):
                ready.set()
            try:
                await waiting.wait()
            except asyncio.CancelledError:
                cancelled.append(ctx.review_projection.reviewer)
                raise
            return st

        task = asyncio.create_task(
            _run_fanout(REVIEW_FANOUT, dict.fromkeys(REVIEW_FANOUT, sibling), state, node_context)
        )
        await ready.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(cancelled) == len(REVIEW_FANOUT)
        events = node_context.recorder.build().events
        assert len(events) == len(REVIEW_FANOUT)
        assert all(event.detail == "cancelled" for event in events)

    asyncio.run(run())


def test_input_cannot_assign_the_same_id_to_different_images(simple_case):
    payload = simple_case.model_dump()
    payload["images"] = (*payload["images"], *payload["images"])

    with pytest.raises(ValueError, match="duplicate image_id"):
        CaseInput.model_validate(payload)


def test_recommendation_cannot_survive_removal_of_its_accepted_finding(simple_case, node_context):
    state = _state(simple_case, node_context.knowledge)
    review = ReviewResult.model_validate(
        {
            "reviewer": "confidence",
            "status": "pass_with_findings",
            "findings": (_lower_finding(),),
            "recommended_confidence": "medium",
        }
    )
    synthesis = _synthesized(state, node_context, review).synthesis
    assert synthesis.recommendations[0].finding is not None
    payload = synthesis.model_dump()
    payload["accepted_findings"] = ()

    with pytest.raises(ValueError, match="finding must belong to this synthesis"):
        ReviewSynthesis.model_validate(payload)


@pytest.mark.parametrize("action", ["abstain", "re_extract_evidence"])
def test_abstention_preserves_other_subjects_bounds(action, simple_case, node_context):
    state = _state(simple_case, node_context.knowledge)
    review = ReviewResult.model_validate(
        {
            "reviewer": "confidence",
            "status": "fail_unresolvable" if action == "abstain" else "fail_correctable",
            "subject_id": "tree_a",
            "findings": (_lower_finding(required_action=action, severity="critical"),),
            "recommended_resolution": "family",
        }
    )
    state = _synthesized(state, node_context, review).evolve(retries=1)
    result = asyncio.run(abstain.run(state, node_context))

    assert _verdict(result, node_context).abstained
    assert _verdict(result, node_context).resolution is Resolution.UNKNOWN
    other = _verdict(result, node_context, "tree_b")
    assert not other.abstained
    assert other.resolution is Resolution.GENUS
    assert other.confidence is Confidence.HIGH


def test_case_wide_abstention_still_covers_every_subject(simple_case, node_context):
    state = _state(simple_case, node_context.knowledge)
    review = ReviewResult.model_validate(
        {
            "reviewer": "confidence",
            "status": "fail_unresolvable",
            "findings": (
                _lower_finding(
                    subject_id=None, evidence_ids=(), required_action="abstain", severity="critical"
                ),
            ),
        }
    )
    result = asyncio.run(abstain.run(_synthesized(state, node_context, review), node_context))

    for subject in state.subject_ids:
        decision = _verdict(result, node_context, subject)
        assert decision.abstained
        assert decision.resolution is Resolution.FAMILY
        assert decision.confidence is Confidence.LOW
