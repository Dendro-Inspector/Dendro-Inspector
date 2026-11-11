"""Regression coverage for defects exposed by live coding-agent provider runs."""

from __future__ import annotations

import asyncio
import io
import json
from dataclasses import replace
from pathlib import Path

import pytest

import dendro_inspector.nodes._support as support
import dendro_inspector.nodes.candidate_generator as candidate_generator
import dendro_inspector.providers.base as provider_base
from dendro_inspector.config import Role
from dendro_inspector.graph.definition import NodeName
from dendro_inspector.graph.projections import build_review_projection
from dendro_inspector.graph.state import EvidenceQualityReport, GraphState
from dendro_inspector.knowledge.candidate_validation import validate_candidate_set
from dendro_inspector.knowledge.evidence_hierarchy import (
    BAND_DECISIVE,
    EvidenceTier,
    confidence_ceiling,
)
from dendro_inspector.knowledge.taxon_cards import (
    card_value_vocabulary,
    requirement_selectors,
    unmatchable_observations,
)
from dendro_inspector.nodes.evidence_quality import assess
from dendro_inspector.nodes.final_decision import MISSING_DECISIVE_PHRASE, decide_subject
from dendro_inspector.nodes.final_decision import (
    MISSING_DECISIVE_PHRASE as _MISSING_DECISIVE_PHRASE,
)
from dendro_inspector.nodes.response_composer import build_result, render_human_readable
from dendro_inspector.observability.events import ProviderCallRecord
from dendro_inspector.observability.trace import TraceRecorder
from dendro_inspector.providers.base import (
    OUTPUT_EVIDENCE_IDS,
    OUTPUT_SUBJECT_IDS,
    ImageInput,
    ImageTooLargeError,
)
from dendro_inspector.schemas.candidates import (
    Candidate,
    CandidateProposal,
    CandidateSet,
    SupportStrength,
)
from dendro_inspector.schemas.decisions import (
    DecisionStatus,
    FinalDecision,
    ResponseFormat,
    ToneMode,
)
from dendro_inspector.schemas.evidence import (
    AttachmentStatus,
    EvidencePacket,
    ImageLimitation,
    Inference,
    KnowledgeCoverage,
    Observation,
    ObservationSource,
    Reliability,
    ScaleQuality,
    Subject,
    SubjectKind,
    Visibility,
)
from dendro_inspector.schemas.input import DeclaredObjectType
from dendro_inspector.schemas.reviews import (
    Reviewer,
    ReviewResult,
    ReviewStatus,
    ReviewSynthesis,
    Severity,
)
from dendro_inspector.schemas.taxon import Confidence, Resolution
from tests.conftest import with_legacy_policy


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


def test_candidate_generator_hands_both_identifier_spaces_to_the_provider(
    simple_case, node_context, monkeypatch
):
    """Binding in the adapter is worth nothing if the node never supplies the ids.

    Live HIGH-thinking runs leaked deliberation fragments into `supporting_evidence_ids`
    and killed five of six mini-batch cases at the parser. The adapter could already
    constrain `subject_id` natively, and this node was passing no metadata at all — so the
    one mechanism that could have prevented it was never reached.
    """
    evidence = EvidencePacket(
        subjects=(Subject(subject_id="tree_1"),),
        observations=(
            Observation(
                observation_id="obs-1",
                feature="bark.pattern",
                value="white_papery_with_black_marks",
                subject_id="tree_1",
                source=ObservationSource.IMAGE,
                image_id="img-1",
            ),
        ),
        inferences=(
            Inference(inference_id="inf-1", claim="betula_compatible", derived_from=("obs-1",)),
        ),
    )
    state = GraphState(
        case=simple_case,
        evidence=evidence,
        quality=EvidenceQualityReport(sufficient=True, usable_subject_ids=("tree_1",)),
    )
    seen: dict[str, object] = {}

    async def _capture(**kwargs):
        seen.update(kwargs["metadata"])
        return CandidateProposal()

    monkeypatch.setattr(candidate_generator, "request_structured", _capture)
    asyncio.run(candidate_generator.run(state, node_context))

    assert seen[OUTPUT_SUBJECT_IDS] == ["tree_1"]
    # Both spaces a reference may legitimately name: observations and inferences.
    assert seen[OUTPUT_EVIDENCE_IDS] == ["inf-1", "obs-1"]


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
    call_metadata: list[dict[str, object]] = []

    async def capture_request(**kwargs):
        prompts.append(kwargs["prompt"])
        call_metadata.append(kwargs["metadata"])
        return ReviewResult(
            reviewer=Reviewer.ARBITER,
            status=ReviewStatus.PASS,
            reviewed_evidence_ids=("provider-spoofed-id",),
        )

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
    state = state.evolve(
        provisional_decisions=tuple(
            # One plated-bark reading names nothing under the default; this test is about
            # what the arbiter is shown, so it pins the legacy policy.
            decide_subject(state, with_legacy_policy(node_context), candidate_set)
            for candidate_set in state.candidate_sets
        )
    )

    arbiter_ctx = replace(
        node_context,
        review_projection=build_review_projection(NodeName.ARBITER, state, node_context),
    )

    result = asyncio.run(
        support.review_call(
            arbiter_ctx,
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
    assert result.reviewed_evidence_ids == ("obs-1",)
    assert call_metadata == [{OUTPUT_SUBJECT_IDS: ("tree_1",)}]


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
    # Reason codes reach the reader rendered, never as the identifiers they are internally.
    assert "location unknown" in result.limitations
    assert "location_unknown" not in result.limitations
    assert "crown_not_visible" in result.limitations
    assert "img-1: no scale reference in the frame" in result.limitations
    assert "other_subject_only" not in result.limitations


def test_a_knowledge_coverage_gap_is_told_to_the_reader(simple_case):
    """The reader is told when the limit was the reference data, not their photograph.

    Live photo 058 asked for another conifer shoot while the run already held, in hand, the
    fact that two of that trunk's bark features were describable by no card in the build.
    The reader was told the frame was weak. The frame was not the weak part.
    """
    decision = FinalDecision(
        subject_id="tree_1",
        status=DecisionStatus.INSUFFICIENT_EVIDENCE,
    )
    evidence = EvidencePacket(
        subjects=(Subject(subject_id="tree_1", image_ids=("img-1",)),),
        observations=(_observation("obs-1"),),
    )
    state = GraphState(
        case=simple_case,
        evidence=evidence,
        quality=EvidenceQualityReport(
            sufficient=True,
            usable_subject_ids=("tree_1",),
            unmatchable_evidence_ids=("obs-1",),
            knowledge_coverage=KnowledgeCoverage(
                observations_total=1,
                potential_gap_evidence_ids=("obs-1",),
                features_absent_from_all_cards=("bark.flake_geometry",),
            ),
        ),
    )

    gapped = build_result(decision, "en", state)
    clean = build_result(
        decision,
        "en",
        state.evolve(
            quality=EvidenceQualityReport(sufficient=True, usable_subject_ids=("tree_1",))
        ),
    )

    phrase = "not described by any card in this knowledge base"
    assert any(phrase in item for item in gapped.limitations)
    assert not any(phrase in item for item in clean.limitations)


def test_colour_only_unmatchable_evidence_does_not_claim_a_coverage_gap(simple_case):
    """Colour is unmatchable by design. Reporting it as a gap would cry wolf on every run."""
    decision = FinalDecision(subject_id="tree_1", status=DecisionStatus.PROBABLE)
    state = GraphState(
        case=simple_case,
        evidence=EvidencePacket(
            subjects=(Subject(subject_id="tree_1", image_ids=("img-1",)),),
            observations=(_observation("obs-1"),),
        ),
        quality=EvidenceQualityReport(
            sufficient=True,
            usable_subject_ids=("tree_1",),
            unmatchable_evidence_ids=("obs-1",),
            knowledge_coverage=KnowledgeCoverage(
                observations_total=1,
                intentionally_weak_evidence_ids=("obs-1",),
            ),
        ),
    )

    result = build_result(decision, "en", state)

    assert not any(
        "not described by any card in this knowledge base" in item for item in result.limitations
    )


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


_GPS_MARKER = "Example Camera Co"
_COMMENT_MARKER = b"owner: user@example.com"


def _write_tagged_photo(path: Path, size: tuple[int, int], fmt: str = "JPEG") -> Path:
    """An upright photograph carrying what a phone writes: GPS, a maker, a free-text comment."""
    pil_image = pytest.importorskip("PIL.Image", reason="the 'images' extra is not installed")
    image = pil_image.new("RGB", size, (110, 90, 60))
    exif = image.getexif()
    exif[0x010F] = _GPS_MARKER
    exif[0x0112] = 1
    exif[0x8825] = {1: "N", 2: (50.0, 27.0, 0.0)}
    if fmt == "JPEG":
        image.save(path, format="JPEG", quality=95, exif=exif, comment=_COMMENT_MARKER)
    else:
        from PIL.PngImagePlugin import PngInfo

        text = PngInfo()
        text.add_text("Comment", _COMMENT_MARKER.decode())
        image.save(path, format="PNG", exif=exif, pnginfo=text)
    return path


@pytest.mark.parametrize(
    ("fmt", "media_type", "size"),
    [
        ("JPEG", "image/jpeg", (800, 600)),
        ("JPEG", "image/jpeg", (4000, 3000)),
        ("PNG", "image/png", (800, 600)),
    ],
)
def test_transmitted_image_carries_no_embedded_metadata(tmp_path, fmt, media_type, size):
    """GPS position, device identity and comments are not evidence, and leave with the bytes.

    The small case used to take the untouched shortcut with its EXIF intact; the large case
    was re-encoded, but Pillow writes a JPEG comment back by default.
    """
    pil_image = pytest.importorskip("PIL.Image", reason="the 'images' extra is not installed")
    path = _write_tagged_photo(tmp_path / f"tagged.{fmt.lower()}", size, fmt)

    sent = ImageInput(
        image_id="img-1", path=path, media_type=media_type, max_edge_px=1568
    ).read_bytes()

    assert _GPS_MARKER.encode() not in sent
    assert _COMMENT_MARKER not in sent
    with pil_image.open(io.BytesIO(sent)) as decoded:
        assert not decoded.getexif()
        assert "comment" not in decoded.info
        assert not getattr(decoded, "text", None)
        assert max(decoded.size) == min(1568, max(size))


def test_an_oversized_file_is_refused_before_it_is_read_or_sent(tmp_path, monkeypatch):
    """Applies whether or not Pillow is installed, and whether or not a bound is configured."""
    monkeypatch.setattr(provider_base, "MAX_IMAGE_FILE_BYTES", 1024)
    path = tmp_path / "huge.jpg"
    path.write_bytes(b"\xff\xd8" + b"\0" * 2048)

    for max_edge_px in (None, 1568):
        image = ImageInput(image_id="img-1", path=path, max_edge_px=max_edge_px)
        with pytest.raises(ImageTooLargeError, match=r"huge\.jpg"):
            image.read_bytes()


def _bark_only_packet() -> EvidencePacket:
    """The evidence a white-barked trunk photograph actually produced.

    Three resolvable bark characters, and every organ-level character explicitly not
    visible — which is what makes the requirement question interesting rather than moot.
    """

    def observation(
        observation_id: str,
        feature: str,
        value: str,
        *,
        visibility: Visibility = Visibility.CLEAR,
    ) -> Observation:
        return Observation(
            observation_id=observation_id,
            feature=feature,
            value=value,
            subject_id="main_trunk",
            source=ObservationSource.IMAGE,
            image_id="img-1",
            visibility=visibility,
            reliability=Reliability.HIGH if feature == "bark.pattern" else Reliability.MEDIUM,
            attachment=AttachmentStatus.UNKNOWN if feature.startswith("leaf.") else None,
        )

    return EvidencePacket(
        subjects=(Subject(subject_id="main_trunk", kind=SubjectKind.STANDING_TREE),),
        observations=(
            observation("obs-1", "bark.pattern", "white_papery_with_black_marks"),
            observation("obs-2", "bark.peeling", "thin_layers"),
            observation("obs-3", "lenticels.orientation", "horizontal"),
            observation("obs-10", "leaf.shape", "unknown", visibility=Visibility.NOT_VISIBLE),
            observation("obs-11", "leaf.arrangement", "unknown", visibility=Visibility.NOT_VISIBLE),
        ),
    )


def _betula_state(
    simple_case, evidence: EvidencePacket, knowledge
) -> tuple[GraphState, CandidateSet]:
    """Validated Betula candidate over that packet, exactly as the graph would build it."""
    proposed = CandidateSet(
        subject_id="main_trunk",
        candidates=(
            Candidate(
                taxon="betula",
                resolution=Resolution.GENUS,
                supporting_evidence_ids=("obs-1", "obs-2", "obs-3"),
                score=SupportStrength.MODERATE,
                rank=1,
                # A model's own guess at what is missing. Validation overwrites it, which is
                # the whole reason a malformed card token could not be argued away by any
                # reviewer: the deterministic answer is the one that reaches the user.
                missing_decisive_features=("bark.pattern_or_leaf",),
            ),
        ),
    )
    validated = validate_candidate_set(proposed, evidence, knowledge)
    state = GraphState(case=simple_case, evidence=evidence, candidate_sets=(validated,))
    return state, validated


def test_bark_evidence_satisfies_the_bark_limb_of_a_requirement(
    simple_case, node_context, knowledge
):
    """A requirement cannot be quoted as support and reported as missing in one decision.

    Betula's card required `bark_pattern_or_leaf` while its only strong feature is
    `bark.pattern`. Nothing mapped one onto the other, so every bark-only run printed
    "Decisive feature not visible: bark_pattern_or_leaf" underneath
    "bark.pattern = white_papery_with_black_marks (high reliability)". Three live
    configurations reproduced it, and the reviewers named the contradiction in their own
    findings without being able to change the field.
    """
    evidence = _bark_only_packet()
    state, validated = _betula_state(simple_case, evidence, knowledge)

    decision = decide_subject(state, node_context, validated)

    assert validated.leader is not None
    assert validated.leader.missing_decisive_features == ()
    assert not any(
        "bark.pattern_or_leaf" in question for question in decision.unresolved_questions
    ), decision.unresolved_questions


def test_a_diagnostic_bark_pattern_reaches_the_band_the_prompt_names(
    simple_case, node_context, knowledge
):
    """The whole birch chain, end to end, on the evidence a real photograph produced.

    `white_papery_with_black_marks` read at high reliability satisfies Betula's
    `bark.pattern_or_leaf` requirement and earns the card-declared confidence exception, so
    this trunk is no longer mechanically pinned at the bottom of the scale. This assertion
    read `Confidence.LOW` before any exception existed and `MEDIUM` while the lift was
    hard-coded at one band; both were the same defect from different distances. Section 6
    of the domain prompt lists this exact bark among its 95-100 examples and section 14
    permits very high confidence at genus, so that is what the card now declares.

    The rest of the guard matters more than the lift and is unchanged: genus, never
    species. Trading a false limitation for a false certainty would be the worse outcome,
    and FAILURE 8 is about the certainty.
    """
    evidence = _bark_only_packet()
    state, validated = _betula_state(simple_case, evidence, knowledge)

    decision = decide_subject(state, node_context, validated)

    assert decision.selected_taxon == "betula"
    assert decision.resolution is Resolution.GENUS
    assert decision.evidence_tier == int(EvidenceTier.BARK)
    # The card's own declaration decides this, not a constant in the decision engine: the
    # exception says `very_high` at genus, which is `HIGH` plus the top display band.
    assert decision.confidence is Confidence.HIGH
    assert decision.confidence_band == BAND_DECISIVE
    assert confidence_ceiling(EvidenceTier.BARK) is Confidence.LOW


def test_a_resolved_bark_character_is_not_photographed_again(simple_case, node_context, knowledge):
    """Asking for the same bark twice is how a photo request stops being read.

    The live run answered a white-papery-bark trunk — `bark.pattern`, `bark.peeling` and
    `lenticels.orientation` all resolved — with "photograph the bark mid-trunk", because the
    request was the first entry of a flat list. Section 16 of the domain prompt says the
    opposite: when only bark is visible, ask for a leaf. This is a separate defect from the
    requirement grammar and neither fix implies the other.
    """
    evidence = _bark_only_packet()
    state, validated = _betula_state(simple_case, evidence, knowledge)

    decision = decide_subject(state, node_context, validated)

    assert decision.best_next_photo is not None
    assert decision.best_next_photo.target != "bark_macro_mid_trunk"
    assert decision.best_next_photo.target == "leaf_upper_macro"
    assert decision.best_next_photo.reason == (
        "Would add missing organ-level evidence for the leading candidate."
    )


def test_bark_only_multi_tree_follow_up_proves_leaf_ownership_first(
    simple_case, node_context, knowledge
):
    """A leaf macro cannot be credited until the photographed tree owns the leaf.

    This is the generalized failure class from a live bark-only run: validation retained
    one weak broadleaf candidate, while the frame could contain several taxa. The candidate
    card already offers an attachment photograph, but the flat follow-up order chose leaf
    morphology first and asked the user for evidence the graph would not yet be allowed to
    attach to the trunk.
    """
    evidence = EvidencePacket(
        subjects=(Subject(subject_id="foreground_tree", kind=SubjectKind.STANDING_TREE),),
        observations=(
            Observation(
                observation_id="obs-bark",
                feature="bark.texture",
                value="coarse_furrowed",
                subject_id="foreground_tree",
                source=ObservationSource.IMAGE,
                image_id="img-1",
                visibility=Visibility.CLEAR,
                reliability=Reliability.HIGH,
            ),
        ),
        possible_multiple_taxa=True,
    )
    proposed = CandidateSet(
        subject_id="foreground_tree",
        candidates=(
            Candidate(
                taxon="populus",
                resolution=Resolution.GENUS,
                supporting_evidence_ids=("obs-bark",),
                score=SupportStrength.MODERATE,
                rank=1,
            ),
        ),
    )
    validated = validate_candidate_set(proposed, evidence, knowledge)
    case = simple_case.model_copy(update={"declared_object_type": DeclaredObjectType.STANDING_TREE})
    state = GraphState(case=case, evidence=evidence, candidate_sets=(validated,))

    decision = decide_subject(state, node_context, validated)

    assert decision.best_next_photo is not None
    assert decision.best_next_photo.target == "leaf_attachment_photo"
    assert "continuously" in decision.best_next_photo.reason


def test_unknown_result_omits_an_empty_nearest_alternatives_section(simple_case):
    """An unresolved candidate list belongs under uncertainty, not under "none recorded"."""
    decision = FinalDecision(
        subject_id="foreground_tree",
        supporting_evidence=("bark.texture = coarse_furrowed (high reliability)",),
        unresolved_questions=("Quercus and Tilia remain plausible alternatives.",),
    )
    state = GraphState(case=simple_case, decisions=(decision,))
    result = build_result(decision, "en", state)

    text = render_human_readable(
        (result,),
        (decision,),
        state,
        locale="en",
        response_format=ResponseFormat.WEAK_PHOTO,
        placeholder_knowledge=False,
    )

    assert "Why not the nearest alternatives" not in text
    assert "none recorded" not in text
    assert "Quercus and Tilia remain plausible alternatives." in text


def test_an_unresolved_bark_character_no_longer_outranks_an_organ(
    simple_case, node_context, knowledge
):
    """A photograph that cannot raise the claim does not get asked for first.

    This assertion read `bark_macro_mid_trunk` while the only question was whether a
    target's features were already answered. `bark.peeling` is unresolved here, so by that
    test another bark macro had something to answer — but the pattern is already read at
    decisive trust, so the subject is at bark tier either way, and no bark photograph can
    lift a verdict past the bark ceiling. A leaf can.

    Bark requests are not dropped as a class. See the test below: when the bark in hand is
    capped by doubt rather than decisive, a better bark photograph is the honest first ask
    and still comes first.
    """
    evidence = _bark_only_packet()
    thin = tuple(
        observation
        for observation in evidence.observations
        if observation.feature != "bark.peeling"
    )
    state, validated = _betula_state(
        simple_case, evidence.model_copy(update={"observations": thin}), knowledge
    )

    decision = decide_subject(state, node_context, validated)

    assert decision.best_next_photo is not None
    assert decision.best_next_photo.target == "leaf_upper_macro"


def test_bark_capped_by_doubt_is_still_worth_photographing_again(
    simple_case, node_context, knowledge
):
    """Saturation is measured at decisive trust, so uncertain bark is not saturated.

    The distant, backlit trunk of `light-trunk-birch-001` reads its pattern at low
    reliability. That subject has no decisive evidence at all, so a better photograph of
    the same bark is genuinely informative and must not be deprioritised — which is the
    difference between an information-gain rule and a blanket ban on bark requests.
    """
    evidence = _bark_only_packet()
    uncertain = tuple(
        observation.model_copy(update={"reliability": Reliability.LOW})
        for observation in evidence.observations
    )
    state, validated = _betula_state(
        simple_case, evidence.model_copy(update={"observations": uncertain}), knowledge
    )

    decision = decide_subject(state, node_context, validated)

    assert decision.best_next_photo is not None
    assert decision.best_next_photo.target == "bark_macro_mid_trunk"


def test_an_unknown_value_does_not_resolve_a_visible_discriminator(
    simple_case, node_context, knowledge
):
    """Visibility is not information gain when no relevant card can interpret the value.

    An unrecognised `bark.peeling` value leaves that discriminator unresolved, so the bark
    macro is not dropped as redundant. It is still deprioritised behind the leaf, because
    the pattern is read at decisive trust and bark tier is already reached — the two filters
    ask different questions and this case exercises both.
    """
    evidence = _bark_only_packet()
    observations = tuple(
        observation.model_copy(update={"value": "some_unrecognised_pattern"})
        if observation.feature == "bark.peeling"
        else observation
        for observation in evidence.observations
    )
    evidence = evidence.model_copy(update={"observations": observations})
    state, validated = _betula_state(simple_case, evidence, knowledge)

    decision = decide_subject(state, node_context, validated)

    assert decision.best_next_photo is not None
    assert decision.best_next_photo.target == "leaf_upper_macro"


def test_multi_candidate_photo_reason_names_an_unresolved_discriminator(
    simple_case, node_context, knowledge
):
    """A real comparison request must not use the single-candidate fallback explanation."""
    evidence = EvidencePacket(
        subjects=(Subject(subject_id="main_trunk", kind=SubjectKind.STANDING_TREE),),
        observations=(
            Observation(
                observation_id="obs-betula",
                feature="bark.pattern",
                value="white_papery_with_black_marks",
                subject_id="main_trunk",
                source=ObservationSource.IMAGE,
                image_id="img-1",
            ),
            Observation(
                observation_id="obs-populus",
                feature="leaf.underside",
                value="white_tomentose",
                subject_id="main_trunk",
                source=ObservationSource.IMAGE,
                image_id="img-1",
                attachment=AttachmentStatus.CONFIRMED_ATTACHED,
            ),
        ),
    )
    proposed = CandidateSet(
        subject_id="main_trunk",
        candidates=(
            Candidate(
                taxon="betula",
                resolution=Resolution.GENUS,
                supporting_evidence_ids=("obs-betula",),
                score=SupportStrength.MODERATE,
                rank=1,
            ),
            Candidate(
                taxon="populus_alba",
                resolution=Resolution.SPECIES,
                supporting_evidence_ids=("obs-populus",),
                score=SupportStrength.MODERATE,
                rank=2,
            ),
        ),
    )
    validated = validate_candidate_set(proposed, evidence, knowledge)
    state = GraphState(case=simple_case, evidence=evidence, candidate_sets=(validated,))

    decision = decide_subject(state, node_context, validated)

    assert decision.best_next_photo is not None
    assert decision.best_next_photo.target == "bark_macro_mid_trunk"
    assert decision.best_next_photo.reason == (
        "Would resolve an unresolved discriminator among the leading candidates."
    )


# The lime attachment-counterfactual regressions that lived here moved to
# tests/unit/test_attachment_authority_gate.py, where the same packets are checked against
# the sensitivity/risk split that replaced v0.7.0's component-provenance corroboration.


def test_bark_only_decision_has_no_attachment_sensitivity_or_confidence_boost(
    simple_case, node_context, knowledge
):
    evidence = _bark_only_packet()
    state, validated = _betula_state(simple_case, evidence, knowledge)

    decision = decide_subject(state, node_context, validated)

    assert decision.evidence_authority_sensitive is False
    assert decision.critical_evidence_ids == ()
    assert decision.selected_taxon == "betula"
    # High since Betula's card declared this bark reading a very-high exception. What this
    # test guards is that unattached foliage contributed nothing to it — the band comes from
    # the card's own bark rule, not from leaves that could belong to the neighbouring tree.
    assert decision.confidence is Confidence.HIGH


def test_an_untrusted_reading_is_not_reported_as_an_invisible_feature():
    """`observed but not trusted` and `not visible` are different claims.

    A run once told the reader "Decisive feature not visible: bark.pattern_or_leaf" three
    lines under "bark.pattern = white_papery_with_black_marks (high reliability)". The
    feature was visible. It had failed a trust gate. Saying "not visible" sends the user to
    re-shoot a photograph that already showed the thing.
    """
    assert "not visible" not in _MISSING_DECISIVE_PHRASE
    assert "not established" in _MISSING_DECISIVE_PHRASE


def test_the_answer_never_cites_a_feature_as_support_and_calls_it_unestablished(
    simple_case, node_context, knowledge
):
    """The Case A self-contradiction, asserted on the composed answer rather than a constant.

    The live run printed "Decisive feature not visible: bark.pattern_or_leaf" three lines
    under "bark.pattern = white_papery_with_black_marks (high reliability)" in its evidence
    list. Both statements were about the same observation, and they could not both be true.

    Written as an invariant over the whole answer rather than as an expected string, so it
    keeps holding when the wording, the requirement grammar or the trust bands change.
    """
    evidence = _bark_only_packet()
    state, validated = _betula_state(simple_case, evidence, knowledge)

    decision = decide_subject(state, node_context, validated)

    cited = {item.split(" = ", maxsplit=1)[0] for item in decision.supporting_evidence}
    assert cited, "the fixture must produce cited support for this to mean anything"

    unestablished = [
        question
        for question in decision.unresolved_questions
        if question.startswith(MISSING_DECISIVE_PHRASE)
    ]
    for question in unestablished:
        token = question.split(": ", maxsplit=1)[1]
        for selector in (part for alt in requirement_selectors(token) for part in alt):
            assert not any(
                feature == selector or feature.startswith(f"{selector}.") for feature in cited
            ), (
                f"the answer cites {selector!r} as evidence and reports it as unestablished: "
                f"{question!r}"
            )


def test_the_case_a_birch_answer_is_no_longer_pinned_at_the_floor(
    simple_case, node_context, knowledge
):
    """The whole chain the two live birch runs failed, in one assertion set.

    Live run: genus Betula at 50-69/100, a decisive feature reported as not visible, and a
    request for another bark macro. Every one of those was the pipeline rather than the
    model, and each had a separate cause — the collapsed trust bands, the unconditional
    bark ceiling, and a follow-up list consulted in order.
    """
    evidence = _bark_only_packet()
    state, validated = _betula_state(simple_case, evidence, knowledge)

    decision = decide_subject(state, node_context, validated)

    assert decision.selected_taxon == "betula"
    assert decision.resolution is Resolution.GENUS
    assert decision.confidence is Confidence.HIGH
    assert not [
        question
        for question in decision.unresolved_questions
        if question.startswith(MISSING_DECISIVE_PHRASE)
    ]
    assert decision.best_next_photo is not None
    assert decision.best_next_photo.target == "leaf_upper_macro"


# --- Golden-100 Sonnet 5.5 run, 2026-11-10: birch at 95-100 delivered as "not 95+" -------------
#
# Eight decisions over six photographs (golden-001, 007, 018, 019, 022, 031) were Betula at
# genus, 95-100/100, evidence tier 3, lifted by the card-declared white-papery-bark exception.
# Every one was policy-compliant and every one was delivered with two sentences denying it:
# the cautious opener ("I would not claim 95+ here") and the bark-only finding ("supports a
# candidate but cannot confirm one"). Both were deterministic text that ignored the exception.


def _delivered_birch_answer(simple_case, node_context, knowledge):
    """Decision, composer and tone layer over the archived birch shape. No reviewer calls."""
    import dendro_inspector.nodes.response_composer as response_composer
    import dendro_inspector.nodes.tone_layer as tone_layer

    evidence = _bark_only_packet()
    state, validated = _betula_state(simple_case, evidence, knowledge)
    decision = decide_subject(state, node_context, validated)
    state = state.evolve(decisions=(decision,))
    state = asyncio.run(response_composer.run(state, node_context))
    state = asyncio.run(tone_layer.run(state, node_context))
    assert state.final_response is not None
    return decision, state.final_response


def _bark_only_finding(simple_case, node_context, knowledge, evidence):
    import dendro_inspector.nodes.confusion_reviewer as confusion_reviewer

    state, validated = _betula_state(simple_case, evidence, knowledge)
    [finding] = confusion_reviewer.bark_only_findings(state, node_context)
    return finding, decide_subject(state, node_context, validated)


def test_a_diagnostic_birch_is_not_voiced_as_declining_its_own_band(
    simple_case, node_context, knowledge
):
    decision, response = _delivered_birch_answer(simple_case, node_context, knowledge)

    assert decision.confidence is Confidence.HIGH
    assert decision.confidence_band == "95–100/100"
    assert "not claim 95+" not in response.human_readable
    assert response.tone_mode is not ToneMode.CAUTIOUS
    assert response.consistency_issues == ()


def test_the_bark_only_finding_states_the_exception_instead_of_denying_it(
    simple_case, node_context, knowledge
):
    finding, decision = _bark_only_finding(
        simple_case, node_context, knowledge, _bark_only_packet()
    )

    assert decision.confidence_band == "95–100/100"
    assert "cannot confirm one" not in finding.summary
    assert "bark.pattern = white_papery_with_black_marks" in finding.summary
    assert "genus" in finding.summary
    assert finding.severity is Severity.MINOR


def test_a_bark_only_candidate_without_the_exception_keeps_the_restraint(
    simple_case, node_context, knowledge
):
    """The exception narrows the text only where it narrows the confidence."""
    evidence = _bark_only_packet()
    uncertain = tuple(
        observation.model_copy(update={"reliability": Reliability.LOW})
        if observation.feature == "bark.pattern"
        else observation
        for observation in evidence.observations
    )
    evidence = evidence.model_copy(update={"observations": uncertain})

    finding, decision = _bark_only_finding(simple_case, node_context, knowledge, evidence)

    assert "cannot confirm one" in finding.summary
    assert finding.severity is Severity.MAJOR
    assert decision.confidence_band != "95–100/100"


# --- Section 6 "no serious alternatives": white poplar left standing -----------------------
#
# Five of the eight archived birch decisions at 95-100 named populus as their nearest
# alternative with nothing recorded against it. The Betula card declares populus_alba its
# common confusion and FAILURE 1 names it outright, so a top band beside it overstated what
# the evidence had settled. The claim keeps its confidence; only the 95-100 band is withheld.


def _birch_with_alternative(simple_case, knowledge, alternative: str, evidence=None):
    evidence = evidence or _bark_only_packet()
    # Both readings can describe different heights of one trunk. The poplar
    # hypothesis needs its own admitted support, not the birch-only pattern.
    poplar_bark = Observation(
        observation_id="obs-4",
        feature="bark.pattern",
        value="pale_upper_dark_rough_base",
        subject_id="main_trunk",
        source=ObservationSource.IMAGE,
        image_id="img-1",
        reliability=Reliability.HIGH,
    )
    evidence = evidence.model_copy(update={"observations": (*evidence.observations, poplar_bark)})
    proposed = CandidateSet(
        subject_id="main_trunk",
        candidates=(
            Candidate(
                taxon="betula",
                resolution=Resolution.GENUS,
                supporting_evidence_ids=("obs-1", "obs-2", "obs-3"),
                score=SupportStrength.MODERATE,
                rank=1,
            ),
            Candidate(
                taxon=alternative,
                resolution=Resolution.GENUS,
                supporting_evidence_ids=("obs-4",) if alternative == "populus_alba" else ("obs-3",),
                score=SupportStrength.WEAK,
                rank=2,
            ),
        ),
    )
    validated = validate_candidate_set(proposed, evidence, knowledge)
    assert {candidate.taxon for candidate in validated.candidates} == {"betula", alternative}
    state = GraphState(case=simple_case, evidence=evidence, candidate_sets=(validated,))
    return state, validated


def test_a_declared_look_alike_left_standing_withholds_the_top_band(
    simple_case, node_context, knowledge
):
    from dendro_inspector.nodes.final_decision import TOP_BAND_WITHHELD_PHRASE

    state, validated = _birch_with_alternative(simple_case, knowledge, "populus_alba")
    decision = decide_subject(state, node_context, validated)

    assert decision.selected_taxon == "betula"
    assert decision.confidence is Confidence.HIGH
    assert decision.nearest_alternative == "populus"
    assert decision.confidence_band == "85–94/100"
    assert any(q.startswith(TOP_BAND_WITHHELD_PHRASE) for q in decision.unresolved_questions)


def test_a_look_alike_the_evidence_rules_out_does_not_withhold_it(
    simple_case, node_context, knowledge
):
    packet = _bark_only_packet()
    opposite = Observation(
        observation_id="obs-9",
        feature="leaf.arrangement",
        value="opposite",
        subject_id="main_trunk",
        source=ObservationSource.IMAGE,
        image_id="img-1",
        visibility=Visibility.CLEAR,
        reliability=Reliability.HIGH,
        attachment=AttachmentStatus.CONFIRMED_ATTACHED,
    )
    packet = packet.model_copy(update={"observations": (*packet.observations, opposite)})
    state, validated = _birch_with_alternative(simple_case, knowledge, "populus_alba", packet)
    decision = decide_subject(state, node_context, validated)

    assert decision.selected_taxon == "betula"
    assert decision.confidence_band == "95–100/100"


def test_an_alternative_the_card_does_not_declare_leaves_the_band_alone(
    simple_case, node_context, knowledge
):
    state, validated = _birch_with_alternative(simple_case, knowledge, "prunus")
    decision = decide_subject(state, node_context, validated)

    assert decision.selected_taxon == "betula"
    assert decision.confidence_band == "95–100/100"


@pytest.mark.parametrize(
    ("reliability", "attachment", "source"),
    [
        (Reliability.LOW, AttachmentStatus.CONFIRMED_ATTACHED, ObservationSource.IMAGE),
        (Reliability.HIGH, AttachmentStatus.UNKNOWN, ObservationSource.IMAGE),
        (Reliability.HIGH, AttachmentStatus.CONFIRMED_ATTACHED, ObservationSource.USER),
    ],
)
def test_untrusted_contradiction_cannot_restore_the_top_band(
    simple_case, node_context, knowledge, reliability, attachment, source
):
    packet = _bark_only_packet()
    opposite = Observation(
        observation_id="obs-9",
        feature="leaf.arrangement",
        value="opposite",
        subject_id="main_trunk",
        source=source,
        image_id="img-1" if source is ObservationSource.IMAGE else None,
        reliability=reliability,
        attachment=attachment,
    )
    packet = packet.model_copy(update={"observations": (*packet.observations, opposite)})
    state, validated = _birch_with_alternative(simple_case, knowledge, "populus_alba", packet)
    decision = decide_subject(state, node_context, validated)
    assert decision.selected_taxon == "betula"
    assert decision.confidence is Confidence.HIGH
    assert decision.confidence_band == "85–94/100"


def test_lower_ranked_declared_confusion_still_withholds_the_top_band(
    simple_case, node_context, knowledge
):
    state, validated = _birch_with_alternative(simple_case, knowledge, "populus_alba")
    prunus = Candidate(
        taxon="prunus",
        resolution=Resolution.GENUS,
        supporting_evidence_ids=("obs-3",),
        score=SupportStrength.WEAK,
        rank=2,
    )
    candidates = validated.model_copy(
        update={
            "candidates": (
                validated.ordered[0],
                prunus,
                validated.ordered[1].model_copy(update={"rank": 3}),
            )
        }
    )
    candidates = validate_candidate_set(candidates, state.evidence, knowledge)
    assert len(candidates.candidates) == 3
    decision = decide_subject(state.evolve(candidate_sets=(candidates,)), node_context, candidates)
    assert decision.nearest_alternative == "prunus"
    assert decision.confidence_band == "85–94/100"


def test_birch_without_an_admitted_look_alike_keeps_the_top_band(
    simple_case, node_context, knowledge
):
    state, validated = _betula_state(simple_case, _bark_only_packet(), knowledge)
    assert decide_subject(state, node_context, validated).confidence_band == "95–100/100"
