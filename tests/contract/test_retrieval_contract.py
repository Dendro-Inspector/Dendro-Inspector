"""Admission can never keep a candidate whose card the evidence does not touch.

The candidate generator uses `candidate_validation.cards_in_play` to select the
cards that admission could accept, instead of the whole catalogue. That is safe exactly
because of the invariant asserted here: a candidate survives `validate_candidate_set` only
with an exact, trusted, same-subject hit on one of its own card's expectations, so every
surviving taxon is already inside the pre-filtered set.

The test checks the production retrieval against an independent evidence-side predicate
and proves every surviving fixture candidate is in the retrieved set. Retrieval has no
top-k limit and receives neither proposals nor expected answers.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from dendro_inspector.knowledge.candidate_validation import cards_in_play, validate_candidate_set
from dendro_inspector.knowledge.evidence_hierarchy import project_evidence
from dendro_inspector.knowledge.loader import KnowledgeBase
from dendro_inspector.schemas.candidates import CandidateProposal
from dendro_inspector.schemas.evidence import EvidencePacket, GeneratedEvidencePacket
from dendro_inspector.schemas.taxon import TaxonCard

pytestmark = pytest.mark.contract

EXTRACTOR_KEY = "primary:evidence_extractor"
GENERATOR_KEY = "primary:candidate_generator"


def _scenarios(root: Path) -> list[tuple[str, EvidencePacket, CandidateProposal]]:
    """Every fixture that scripts both an evidence packet and a candidate proposal."""
    scenarios: list[tuple[str, EvidencePacket, CandidateProposal]] = []
    for path in sorted((root / "evals" / "fixtures").glob("*.json")):
        payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        responses = payload.get("responses", {})
        if EXTRACTOR_KEY not in responses or GENERATOR_KEY not in responses:
            continue
        packet = GeneratedEvidencePacket.model_validate(responses[EXTRACTOR_KEY])
        proposal = CandidateProposal.model_validate(responses[GENERATOR_KEY])
        scenarios.append((path.stem, packet.to_evidence_packet(), proposal))
    return scenarios


def _card_is_in_play(card: TaxonCard, evidence: EvidencePacket, subject_id: str) -> bool:
    """Whether any trusted same-subject observation matches one of this card's expectations.

    Deliberately written against the packet rather than against a proposal: the point of N2
    is to decide which cards to show *before* a model has proposed anything.
    """
    expectations = (*card.strong_positive_features, *card.supporting_features)
    for observation in evidence.observations_for(subject_id):
        projection = project_evidence(evidence, observation.observation_id, subject_id)
        if not projection.supports_identification:
            continue
        if any(
            observation.feature == expectation.feature and observation.value in expectation.values
            for expectation in expectations
        ):
            return True
    return False


def _cards_in_play(evidence: EvidencePacket, subject_id: str, knowledge: KnowledgeBase) -> set[str]:
    return {
        taxon_id
        for taxon_id in knowledge.available_taxon_ids()
        if (card := knowledge.try_taxon(taxon_id)) is not None
        and _card_is_in_play(card, evidence, subject_id)
    }


def test_the_fixture_corpus_actually_exercises_this(repo_root):
    """A contract test that silently iterated an empty list would assert nothing."""
    assert len(_scenarios(repo_root)) >= 15


def test_admission_never_keeps_a_taxon_outside_the_pre_filtered_set(repo_root, knowledge):
    for scenario, evidence, proposal in _scenarios(repo_root):
        for candidate_set in proposal.sets:
            subject_id = candidate_set.subject_id
            if not any(subject.subject_id == subject_id for subject in evidence.subjects):
                continue
            in_play = set(cards_in_play(evidence, knowledge, (subject_id,)))
            assert in_play <= _cards_in_play(evidence, subject_id, knowledge)

            validated = validate_candidate_set(candidate_set, evidence, knowledge)

            survivors = {candidate.taxon for candidate in validated.candidates}
            assert survivors <= in_play, (
                f"{scenario}/{subject_id}: admission kept {sorted(survivors - in_play)}, "
                "which the evidence-side pre-filter would not have shown the model."
            )


def test_the_pre_filter_is_narrower_than_the_catalogue_it_replaces(repo_root, knowledge):
    """The saving N2 claims is real on this corpus, not a rounding error.

    If the pre-filter kept nearly every card there would be nothing to gain and the change
    would be pure risk. Stated as a property rather than a measurement, so it does not go
    stale when the pack grows.
    """
    catalogue = len(knowledge.available_taxon_ids())
    widest = 0
    for _, evidence, proposal in _scenarios(repo_root):
        for candidate_set in proposal.sets:
            subject_id = candidate_set.subject_id
            if not any(subject.subject_id == subject_id for subject in evidence.subjects):
                continue
            widest = max(widest, len(cards_in_play(evidence, knowledge, (subject_id,))))

    assert widest < catalogue / 2, (
        f"the widest pre-filtered set was {widest} of {catalogue} cards; N2's premise is "
        "that admission touches a handful of cards per subject."
    )
