"""Deterministic confusion-review findings."""

from __future__ import annotations

from dendro_inspector.graph.state import EvidenceQualityReport, GraphState
from dendro_inspector.nodes.confusion_reviewer import colour_findings
from dendro_inspector.schemas.evidence import (
    EvidencePacket,
    Observation,
    ObservationSource,
    Subject,
    WoodSurface,
)


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
