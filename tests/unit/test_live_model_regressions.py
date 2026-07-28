"""Regression coverage for defects exposed by live coding-agent provider runs."""

from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path

import pytest

import dendro_inspector.nodes._support as support
from dendro_inspector.config import Role
from dendro_inspector.graph.state import GraphState
from dendro_inspector.knowledge.taxon_cards import (
    card_value_vocabulary,
    unmatchable_observations,
)
from dendro_inspector.nodes.evidence_quality import assess
from dendro_inspector.nodes.response_composer import build_result
from dendro_inspector.observability.events import ProviderCallRecord
from dendro_inspector.observability.trace import TraceRecorder
from dendro_inspector.providers.base import ImageInput
from dendro_inspector.schemas.candidates import Candidate, CandidateSet, SupportStrength
from dendro_inspector.schemas.decisions import DecisionStatus, FinalDecision
from dendro_inspector.schemas.evidence import (
    EvidencePacket,
    ImageLimitation,
    Observation,
    ObservationSource,
    ScaleQuality,
    Subject,
    SubjectKind,
)
from dendro_inspector.schemas.reviews import Reviewer, ReviewResult, ReviewStatus, ReviewSynthesis
from dendro_inspector.schemas.taxon import Resolution


def _json_block(context: str) -> object:
    body = context.split("```json\n", maxsplit=1)[1].rsplit("\n```", maxsplit=1)[0]
    return json.loads(body)


def _observation(
    observation_id: str,
    *,
    subject_id: str = "tree_1",
    image_id: str = "img-1",
    value: str = "scaly_plates",
) -> Observation:
    return Observation(
        observation_id=observation_id,
        feature="bark.texture",
        value=value,
        subject_id=subject_id,
        source=ObservationSource.IMAGE,
        image_id=image_id,
    )


def test_extractor_vocabulary_uses_exact_card_tokens_without_taxon_names(node_context):
    context = support.evidence_value_vocabulary_context(node_context)
    payload = _json_block(context)

    assert isinstance(payload, dict)
    assert "scaly_plates" in payload["bark.texture"]
    assert "single_on_woody_peg" in payload["needles.attachment"]
    assert "pinus" not in context.lower()
    assert "picea" not in context.lower()
    assert context == support.evidence_value_vocabulary_context(node_context)


def test_arbiter_receives_deterministic_proposed_resolution_and_confidence(
    simple_case,
    node_context,
    monkeypatch,
):
    prompts: list[str] = []

    async def capture_request(**kwargs):
        prompts.append(kwargs["prompt"])
        return ReviewResult(reviewer=Reviewer.ARBITER, status=ReviewStatus.PASS)

    monkeypatch.setattr(support, "request_structured", capture_request)
    candidate = Candidate(
        taxon="pinus",
        resolution=Resolution.GENUS,
        supporting_evidence_ids=("obs-1",),
        score=SupportStrength.MODERATE,
        rank=1,
    )
    state = GraphState(
        case=simple_case,
        evidence=EvidencePacket(
            subjects=(
                Subject(
                    subject_id="tree_1",
                    kind=SubjectKind.STANDING_TREE,
                    image_ids=("img-1",),
                ),
            ),
            observations=(_observation("obs-1"),),
        ),
        candidate_sets=(CandidateSet(subject_id="tree_1", candidates=(candidate,)),),
        synthesis=ReviewSynthesis(),
    )

    asyncio.run(
        support.review_call(
            state,
            node_context,
            node="arbiter",
            reviewer=Reviewer.ARBITER,
            role=Role.ARBITER,
        )
    )

    assert len(prompts) == 1
    prompt = prompts[0]
    assert "Proposed assessment (deterministic pre-arbitration result)" in prompt
    assert '"selected_taxon": "pinus"' in prompt
    assert '"resolution": "genus"' in prompt
    assert '"confidence": "low"' in prompt


def test_weak_result_reports_visible_evidence_and_scoped_limitations(simple_case):
    decision = FinalDecision(
        subject_id="tree_1",
        status=DecisionStatus.INSUFFICIENT_EVIDENCE,
    )
    state = GraphState(
        case=simple_case,
        evidence=EvidencePacket(
            subjects=(
                Subject(subject_id="tree_1", image_ids=("img-1",)),
                Subject(subject_id="other_tree", image_ids=("img-2",)),
            ),
            observations=(
                _observation("obs-1"),
                _observation(
                    "obs-2",
                    subject_id="other_tree",
                    image_id="img-2",
                    value="fine_scales",
                ),
            ),
            image_limitations=(
                ImageLimitation(
                    image_id="img-1",
                    scale=ScaleQuality.ABSENT,
                    notes="crown_not_visible",
                ),
                ImageLimitation(image_id="img-2", notes="other_subject_only"),
            ),
            context_limitations=("location_unknown",),
        ),
    )

    result = build_result(decision, "en", state)

    assert result.supporting_evidence == ("bark.texture = scaly_plates",)
    assert "location_unknown" in result.limitations
    assert "crown_not_visible" in result.limitations
    assert "img-1: scale_absent" in result.limitations
    assert "other_subject_only" not in result.limitations


def _call(node: str, response_model: str = "ReviewResult") -> ProviderCallRecord:
    return ProviderCallRecord(
        role="primary",
        adapter="fake",
        node=node,
        response_model=response_model,
    )


def test_concurrent_reviewers_keep_their_own_provider_calls():
    """The reviewer fan-out shares one recorder; calls must not pool into the first node.

    Every trace from the first live run showed ``botanical_reviewer calls=3`` beside
    ``confusion_reviewer calls=0`` and six minutes of wall time, because ``record_node``
    drained every pending call regardless of which node made it.
    """
    recorder = TraceRecorder("case")

    # asyncio.gather order: all three finish before any of them is recorded.
    for node in ("confidence_reviewer", "botanical_reviewer", "confusion_reviewer"):
        recorder.record_provider_call(_call(node))
    for node in ("botanical_reviewer", "confusion_reviewer", "confidence_reviewer"):
        recorder.record_node(node)

    calls_by_node = {event.node: event.provider_calls for event in recorder.build().events}
    assert [record.node for record in calls_by_node["botanical_reviewer"]] == ["botanical_reviewer"]
    assert [record.node for record in calls_by_node["confusion_reviewer"]] == ["confusion_reviewer"]
    assert [record.node for record in calls_by_node["confidence_reviewer"]] == [
        "confidence_reviewer"
    ]


def test_repeated_node_claims_only_the_calls_made_before_it_recorded():
    """The retry path runs evidence_extractor twice; each pass keeps its own call."""
    recorder = TraceRecorder("case")
    recorder.record_provider_call(_call("evidence_extractor", "GeneratedEvidencePacket"))
    recorder.record_node("evidence_extractor")
    recorder.record_provider_call(_call("evidence_extractor", "GeneratedEvidencePacket"))
    recorder.record_node("evidence_extractor")

    events = [event for event in recorder.build().events if event.node == "evidence_extractor"]
    assert [len(event.provider_calls) for event in events] == [1, 1]


def test_call_for_a_node_that_never_records_is_reported_not_silently_dropped(caplog):
    recorder = TraceRecorder("case")
    recorder.record_provider_call(_call("ghost_node"))
    recorder.record_node("planner")

    with caplog.at_level("WARNING"):
        trace = recorder.build()

    assert all(not event.provider_calls for event in trace.events)
    assert "unattributed_provider_calls" in caplog.text


def test_observations_outside_the_card_vocabulary_are_counted_not_silently_dropped(knowledge):
    """46% of the first live run's evidence could never match a card. It went unreported.

    Nothing downstream distinguishes "the model saw nothing useful" from "the cards
    describe nothing the model saw" — both surface as a rejected candidate.
    """
    vocabulary = card_value_vocabulary(knowledge.taxa(knowledge.available_taxon_ids()))
    assert "single_on_woody_peg" in vocabulary["needles.attachment"]
    assert "needles.shape" not in vocabulary

    evidence = EvidencePacket(
        subjects=(Subject(subject_id="tree_1", image_ids=("img-1",)),),
        observations=(
            _observation("obs-1", value="scaly_plates"),
            _observation("obs-2", value="not_a_card_token"),
        ),
    )

    assert [o.observation_id for o in unmatchable_observations(evidence, vocabulary)] == ["obs-2"]

    report = assess(
        evidence,
        min_observations=1,
        require_non_colour=False,
        vocabulary=vocabulary,
    )
    assert report.unmatchable_evidence_ids == ("obs-2",)


def test_quality_report_omits_the_diagnostic_when_no_vocabulary_is_supplied():
    evidence = EvidencePacket(
        subjects=(Subject(subject_id="tree_1", image_ids=("img-1",)),),
        observations=(_observation("obs-1"),),
    )
    report = assess(evidence, min_observations=1, require_non_colour=False)
    assert report.unmatchable_evidence_ids == ()


def _write_photo(path: Path, size: tuple[int, int]) -> Path:
    pil_image = pytest.importorskip("PIL.Image", reason="the 'images' extra is not installed")
    # Noise, not flat colour: a solid image compresses to nothing and would make the
    # size assertions below pass for the wrong reason.
    import random

    image = pil_image.new("RGB", size)
    rng = random.Random(0)
    image.putdata(
        [
            (rng.randrange(256), rng.randrange(256), rng.randrange(256))
            for _ in range(size[0] * size[1])
        ]
    )
    image.save(path, format="JPEG", quality=95)
    return path


def test_transmitted_image_is_bounded_to_the_configured_edge(tmp_path):
    pil_image = pytest.importorskip("PIL.Image", reason="the 'images' extra is not installed")
    path = _write_photo(tmp_path / "big.jpg", (4000, 3000))

    original = ImageInput(image_id="img-1", path=path).read_bytes()
    bounded = ImageInput(image_id="img-1", path=path, max_edge_px=1568).read_bytes()

    assert original == path.read_bytes()
    assert len(bounded) < len(original)
    with pil_image.open(io.BytesIO(bounded)) as decoded:
        assert max(decoded.size) == 1568
        assert decoded.size == (1568, 1176)  # aspect ratio preserved


def test_image_already_within_the_bound_is_sent_untouched(tmp_path):
    """No second generation of JPEG loss on evidence the model is asked to read closely."""
    path = _write_photo(tmp_path / "small.jpg", (800, 600))
    assert ImageInput(image_id="img-1", path=path, max_edge_px=1568).read_bytes() == (
        path.read_bytes()
    )


def test_unresizable_media_type_is_passed_through_rather_than_guessed_at(tmp_path):
    path = tmp_path / "scan.tif"
    path.write_bytes(b"not really a tiff")
    image = ImageInput(image_id="img-1", path=path, media_type="image/tiff", max_edge_px=1568)
    assert image.read_bytes() == b"not really a tiff"


def test_bounded_bytes_are_recomputed_when_the_file_changes(tmp_path):
    """The cache is keyed on content identity, not just the path."""
    path = tmp_path / "photo.jpg"
    _write_photo(path, (4000, 3000))
    first = ImageInput(image_id="img-1", path=path, max_edge_px=1568).read_bytes()
    _write_photo(path, (4000, 1000))
    second = ImageInput(image_id="img-1", path=path, max_edge_px=1568).read_bytes()
    assert first != second


def test_context_tier_observations_do_not_inflate_the_coverage_gap(knowledge):
    """A site note can never support a candidate, so an unlisted one is not a card gap."""
    vocabulary = card_value_vocabulary(knowledge.taxa(knowledge.available_taxon_ids()))
    assert "mixed_woodland" not in vocabulary.get("context.site", frozenset())

    evidence = EvidencePacket(
        subjects=(Subject(subject_id="tree_1", image_ids=("img-1",)),),
        observations=(
            Observation(
                observation_id="obs-1",
                feature="context.site",
                value="mixed_woodland",
                subject_id="tree_1",
                source=ObservationSource.IMAGE,
                image_id="img-1",
            ),
            _observation("obs-2", value="not_a_card_token"),
        ),
    )

    assert [o.observation_id for o in unmatchable_observations(evidence, vocabulary)] == ["obs-2"]


def _write_rotated_photo(path: Path, size: tuple[int, int], orientation: int) -> Path:
    """A landscape-stored photograph tagged to display rotated, as phone cameras write it."""
    pil_image = pytest.importorskip("PIL.Image", reason="the 'images' extra is not installed")
    import random

    image = pil_image.new("RGB", size)
    rng = random.Random(0)
    image.putdata(
        [
            (rng.randrange(256), rng.randrange(256), rng.randrange(256))
            for _ in range(size[0] * size[1])
        ]
    )
    exif = image.getexif()
    exif[274] = orientation
    image.save(path, format="JPEG", quality=95, exif=exif)
    return path


def test_bounded_image_bakes_in_exif_rotation_instead_of_dropping_it(tmp_path):
    """Re-encoding strips EXIF, so the orientation tag must be applied, not discarded.

    Every photograph in the golden set is stored 4000x3000 landscape with orientation 6.
    Bounding them without transposing handed the model a tree lying on its side: the live
    re-run read dark scaly plates where the same photograph had previously read as white
    papery bark, and the verdict fell from a family-level result to `unknown`.
    """
    pil_image = pytest.importorskip("PIL.Image", reason="the 'images' extra is not installed")
    path = _write_rotated_photo(tmp_path / "portrait.jpg", (4000, 3000), orientation=6)

    bounded = ImageInput(image_id="img-1", path=path, max_edge_px=1568).read_bytes()

    with pil_image.open(io.BytesIO(bounded)) as decoded:
        assert decoded.size == (1176, 1568), "orientation 6 means the photo displays portrait"
        assert decoded.getexif().get(274) in (None, 1), "no stale tag on already-upright pixels"


def test_small_but_rotated_image_is_still_transposed(tmp_path):
    """The already-small shortcut must not skip a photograph that is merely sideways."""
    pil_image = pytest.importorskip("PIL.Image", reason="the 'images' extra is not installed")
    path = _write_rotated_photo(tmp_path / "small_rotated.jpg", (800, 600), orientation=6)

    bounded = ImageInput(image_id="img-1", path=path, max_edge_px=1568).read_bytes()

    assert bounded != path.read_bytes()
    with pil_image.open(io.BytesIO(bounded)) as decoded:
        assert decoded.size == (600, 800)


def test_untagged_small_image_still_takes_the_untouched_shortcut(tmp_path):
    path = _write_photo(tmp_path / "plain.jpg", (800, 600))
    assert ImageInput(image_id="img-1", path=path, max_edge_px=1568).read_bytes() == (
        path.read_bytes()
    )
