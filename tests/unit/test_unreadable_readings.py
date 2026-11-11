"""A token that says the feature could not be read is silence, not disagreement.

Live phone upload mobile-1794338221055 (one walnut, field context): the extractor wrote
``leaflet.count = numerous_not_countable``. The Juglans nigra card names leaflet count as
strong-positive, so the self-contradiction veto read that token as a *different* count and
removed walnut, although nuts, husk and leaflets matched the card. The packets below are
synthetic and use generic tokens.
"""

from __future__ import annotations

import pytest

from dendro_inspector.knowledge.candidate_validation import validate_candidate_set_with_report
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
    is_positive_reading,
)
from dendro_inspector.schemas.taxon import Resolution

TREE = "tree_1"


@pytest.mark.parametrize(
    "value",
    [
        "not_visible",
        "numerous_not_countable",
        "not_determinable",
        "leaflets_not_resolvable",
        "unresolved",
        "not_resolved",
        "young_broadleaf_unresolved",
        "unreadable",
        "none_seen",
    ],
)
def test_failures_to_read_are_not_readings(value):
    assert not is_positive_reading(value)


@pytest.mark.parametrize(
    "value",
    [
        "fifteen_to_twenty_three",
        "notched_tip",
        "deeply_corrugated_ridged",
        "variable",
        # A reading that happens to start with "not": a real negative observation.
        "not_single_pegs",
    ],
)
def test_readings_stay_readings(value):
    assert is_positive_reading(value)


def _obs(oid: str, feature: str, value: str) -> Observation:
    return Observation(
        observation_id=oid,
        feature=feature,
        value=value,
        subject_id=TREE,
        source=ObservationSource.IMAGE,
        image_id="img-1",
        visibility=Visibility.CLEAR,
        reliability=Reliability.HIGH,
        attachment=AttachmentStatus.CONFIRMED_ATTACHED,
    )


def _validate(knowledge, *observations: Observation):
    packet = EvidencePacket(
        subjects=(Subject(subject_id=TREE, kind=SubjectKind.STANDING_TREE),),
        observations=observations,
    )
    proposal = CandidateSet(
        subject_id=TREE,
        candidates=(
            Candidate(
                taxon="juglans_nigra",
                resolution=Resolution.SPECIES,
                supporting_evidence_ids=("o-nut",),
                score=SupportStrength.MODERATE,
                rank=1,
            ),
        ),
    )
    return validate_candidate_set_with_report(proposal, packet, knowledge)


def test_an_uncountable_strong_feature_does_not_veto_the_card(knowledge):
    report = _validate(
        knowledge,
        _obs("o-nut", "nut.shell_surface", "deeply_corrugated_ridged"),
        _obs("o-count", "leaflet.count", "numerous_not_countable"),
    )
    assert "juglans_nigra" not in report.rejected_taxa


def test_a_counted_different_value_still_vetoes(knowledge):
    report = _validate(
        knowledge,
        _obs("o-nut", "nut.shell_surface", "deeply_corrugated_ridged"),
        _obs("o-count", "leaflet.count", "five_to_nine"),
    )
    assert "juglans_nigra" in report.rejected_taxa
