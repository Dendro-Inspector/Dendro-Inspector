"""Ruling on the user's own version — section 3 of the domain prompt.

The user may be looking at foliage the photograph never captured, may know where the tree
was felled, may have watched it for twenty years. So their version is checked, not dismissed
— and rejection is deliberately hard to reach.
"""

from __future__ import annotations

import pytest

from evil_duck_dendro.graph.state import GraphState
from evil_duck_dendro.nodes.final_decision import normalise_claim, rule_on_user_claim
from evil_duck_dendro.schemas.candidates import Candidate, CandidateSet, SupportStrength
from evil_duck_dendro.schemas.decisions import UserClaimVerdict
from evil_duck_dendro.schemas.evidence import (
    EvidencePacket,
    Observation,
    ObservationSource,
    Subject,
)
from evil_duck_dendro.schemas.input import CaseInput
from evil_duck_dendro.schemas.taxon import Resolution
from tests.conftest import _attachment

DETACHABLE = ("leaf", "needles", "fruit", "cones", "branch", "bud", "seed", "nut", "acorn")


def _obs(observation_id, feature, value):
    return Observation(
        observation_id=observation_id,
        feature=feature,
        value=value,
        subject_id="log_1",
        source=ObservationSource.IMAGE,
        image_id="img-1",
        attachment=_attachment(feature, True),
    )


def _evidence(*observations):
    return EvidencePacket(subjects=(Subject(subject_id="log_1"),), observations=observations)


def _candidates(*taxa):
    return CandidateSet(
        subject_id="log_1",
        candidates=tuple(
            Candidate(
                taxon=taxon,
                resolution=Resolution.GENUS,
                rank=index,
                score=SupportStrength.MODERATE,
            )
            for index, taxon in enumerate(taxa, start=1)
        ),
    )


def _state(**changes) -> GraphState:
    return GraphState(case=CaseInput(case_id="c1", user_text="?", **changes))


class TestClaimNormalisation:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("  Дуб  ", "дуб"), ("Pinus sylvestris", "pinussylvestris"), ("oak!", "oak")],
    )
    def test_claims_are_reduced_to_comparable_tokens(self, raw, expected):
        assert normalise_claim(raw) == expected


class TestVerdicts:
    def test_no_claim_is_not_a_verdict(self, node_context):
        verdict = rule_on_user_claim(
            _state(),
            node_context,
            "log_1",
            _candidates("pinus"),
            _evidence(_obs("o1", "leaf.shape", "oval_serrate")),
            "pinus",
        )
        assert verdict is UserClaimVerdict.NOT_PROVIDED

    def test_a_matching_claim_is_accepted(self, node_context):
        """Section 9: "Так, [порода] приймається."."""
        verdict = rule_on_user_claim(
            _state(user_claim="сосна"),
            node_context,
            "log_1",
            _candidates("pinus"),
            _evidence(_obs("o1", "needles.fascicles", "two")),
            "pinus",
        )
        assert verdict is UserClaimVerdict.ACCEPTED

    def test_the_claim_is_matched_in_the_users_own_language(self, node_context):
        for claim in ("дуб", "oak", "Quercus"):
            verdict = rule_on_user_claim(
                _state(user_claim=claim),
                node_context,
                "log_1",
                _candidates("quercus"),
                _evidence(_obs("o1", "leaf.shape", "simple_lobed")),
                "quercus",
            )
            assert verdict is UserClaimVerdict.ACCEPTED, claim

    def test_a_claim_still_in_the_running_is_possible(self, node_context):
        verdict = rule_on_user_claim(
            _state(user_claim="ялина"),
            node_context,
            "log_1",
            _candidates("pinus", "picea"),
            _evidence(_obs("o1", "leaf.shape", "oval_serrate")),
            "pinus",
        )
        assert verdict is UserClaimVerdict.POSSIBLE

    def test_a_taxon_the_project_has_no_card_for_is_our_gap_not_their_error(self, node_context):
        verdict = rule_on_user_claim(
            _state(user_claim="eucalyptus"),
            node_context,
            "log_1",
            _candidates("pinus"),
            _evidence(_obs("o1", "leaf.shape", "oval_serrate")),
            "pinus",
        )
        assert verdict is UserClaimVerdict.POSSIBLE

    def test_contradicted_evidence_above_bark_can_reject(self, node_context):
        """Fascicled needles genuinely disqualify Picea, and foliage is above bark level."""
        verdict = rule_on_user_claim(
            _state(user_claim="ялина"),
            node_context,
            "log_1",
            _candidates("pinus"),
            _evidence(_obs("o1", "needles.fascicles", "two")),
            "pinus",
        )
        assert verdict is UserClaimVerdict.REJECTED


class TestRestraint:
    def test_bark_only_evidence_can_never_reject_a_version(self, node_context):
        """FAILURE 3 — one patch of bark does not get to overrule the person who was there."""
        verdict = rule_on_user_claim(
            _state(user_claim="дуб"),
            node_context,
            "log_1",
            _candidates("fraxinus"),
            _evidence(
                _obs("o1", "bark.texture", "deep_longitudinal_fissures"),
                _obs("o2", "bark.pattern", "weathered_grey_uneven"),
            ),
            "fraxinus",
        )
        assert verdict is not UserClaimVerdict.REJECTED
        assert verdict is UserClaimVerdict.POSSIBLE

    def test_field_context_blocks_rejection_even_with_contrary_foliage(self, node_context):
        """The user can see the crown, the fruit and the stump. The camera cannot."""
        verdict = rule_on_user_claim(
            _state(user_claim="ялина", user_has_field_context=True),
            node_context,
            "log_1",
            _candidates("pinus"),
            _evidence(_obs("o1", "needles.fascicles", "two")),
            "pinus",
        )
        assert verdict is not UserClaimVerdict.REJECTED

    def test_without_field_context_the_same_evidence_does_reject(self, node_context):
        """The contrast that proves the restraint clause is doing the work."""
        verdict = rule_on_user_claim(
            _state(user_claim="ялина"),
            node_context,
            "log_1",
            _candidates("pinus"),
            _evidence(_obs("o1", "needles.fascicles", "two")),
            "pinus",
        )
        assert verdict is UserClaimVerdict.REJECTED


class TestNotEvaluable:
    """`not_evaluable` is not `doubtful`.

    Doubtful means there are reasons to doubt the claim. Not-evaluable means the photograph
    cannot support any assessment of it in either direction — reporting the second as the
    first quietly credits the system with an opinion it does not have.
    """

    def test_a_photograph_that_carries_no_verdict_carries_no_ruling_either(
        self, simple_case, run_scenario
    ):
        case = simple_case.model_copy(update={"user_claim": "дуб"})
        result = run_scenario(case, "primary-insufficient")
        assert result.state.decisions[0].user_claim_verdict is UserClaimVerdict.NOT_EVALUABLE

    def test_no_claim_still_means_not_provided(self, simple_case, run_scenario):
        result = run_scenario(simple_case, "primary-insufficient")
        assert result.state.decisions[0].user_claim_verdict is UserClaimVerdict.NOT_PROVIDED

    def test_the_vocabulary_distinguishes_no_opinion_from_a_negative_one(self):
        values = {verdict.value for verdict in UserClaimVerdict}
        assert {"not_evaluable", "doubtful", "rejected"} <= values
