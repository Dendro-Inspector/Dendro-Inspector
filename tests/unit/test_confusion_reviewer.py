"""Deterministic confusion-review findings."""

from __future__ import annotations

from dendro_inspector.graph.state import EvidenceQualityReport, GraphState
from dendro_inspector.nodes.confusion_reviewer import (
    colour_findings,
    unattached_evidence_findings,
)
from dendro_inspector.schemas.evidence import (
    AttachmentStatus,
    EvidencePacket,
    Observation,
    ObservationSource,
    Subject,
    WoodSurface,
)
from dendro_inspector.schemas.reviews import Impact, Severity


def test_tone_feature_is_cited_by_colour_overweighting_finding(simple_case):
    evidence = EvidencePacket(
        subjects=(Subject(subject_id="log_1"),),
        observations=(
            Observation(
                observation_id="tone",
                feature="heartwood.tone",
                value="warm_yellow_orange",
                subject_id="log_1",
                source=ObservationSource.IMAGE,
                image_id="img-1",
                wood_surface=WoodSurface.SPLIT_FACE,
            ),
        ),
    )
    state = GraphState(
        case=simple_case,
        evidence=evidence,
        quality=EvidenceQualityReport(colour_dependence_detected=True),
    )

    findings = colour_findings(state)

    assert len(findings) == 1
    assert findings[0].evidence_ids == ("tone",)


def _leaf(observation_id: str, feature: str, value: str, attachment: AttachmentStatus):
    return Observation(
        observation_id=observation_id,
        feature=feature,
        value=value,
        subject_id="tree_1",
        source=ObservationSource.IMAGE,
        image_id="img-1",
        attachment=attachment,
    )


def _tree_state(simple_case, *observations: Observation) -> GraphState:
    return GraphState(
        case=simple_case,
        evidence=EvidencePacket(
            subjects=(Subject(subject_id="tree_1"),),
            observations=observations,
        ),
    )


def test_loose_foliage_is_a_footnote_when_other_foliage_is_attached(simple_case):
    """A trunk sprout proves attachment; the canopy leaf nobody could trace is a footnote.

    Reporting "foliage could not be traced to this trunk" as the subject's headline
    contradiction while the verdict was computed from foliage that *was* traced contradicts
    the evidence tier it came from, and it punishes the honest `unknown` the extractor brief
    asks for.
    """
    state = _tree_state(
        simple_case,
        _leaf("shoot", "leaf.shape", "palmate_lobed", AttachmentStatus.CONFIRMED_ATTACHED),
        _leaf("canopy", "leaf.underside", "pale_not_tomentose", AttachmentStatus.UNKNOWN),
    )

    findings = unattached_evidence_findings(state)

    assert len(findings) == 1
    assert findings[0].evidence_ids == ("canopy",)
    assert findings[0].severity is Severity.MINOR
    assert findings[0].impact is Impact.NO_MATERIAL_CHANGE
    assert "rests on the foliage that is traceable" in findings[0].summary


def test_loose_foliage_is_major_when_nothing_was_traced(simple_case):
    state = _tree_state(
        simple_case,
        _leaf("canopy", "leaf.shape", "palmate_lobed", AttachmentStatus.UNKNOWN),
        Observation(
            observation_id="bark",
            feature="bark.texture",
            value="coarse_furrowed",
            subject_id="tree_1",
            source=ObservationSource.IMAGE,
            image_id="img-1",
        ),
    )

    findings = unattached_evidence_findings(state)

    assert len(findings) == 1
    assert findings[0].severity is Severity.MAJOR
    assert findings[0].impact is Impact.CONFIDENCE_CHANGE
    assert "could not be traced" in findings[0].summary


def test_detached_material_keeps_its_own_wording(simple_case):
    state = _tree_state(
        simple_case,
        _leaf("ground", "leaf.shape", "palmate_lobed", AttachmentStatus.CONFIRMED_DETACHED),
    )

    findings = unattached_evidence_findings(state)

    assert len(findings) == 1
    assert findings[0].severity is Severity.MAJOR
    assert "belongs to whoever dropped it" in findings[0].summary
