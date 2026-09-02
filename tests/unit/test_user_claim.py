"""Ruling on the user's own version — section 3 of the domain prompt.

The user may be looking at foliage the photograph never captured, may know where the tree
was felled, may have watched it for twenty years. So their version is checked, not dismissed
— and rejection is deliberately hard to reach.
"""

from __future__ import annotations

import pytest

from dendro_inspector.graph.state import GraphState
from dendro_inspector.nodes.final_decision import (
    normalise_claim,
    resolve_user_claim,
    rule_on_user_claim,
)
from dendro_inspector.schemas.candidates import Candidate, CandidateSet, SupportStrength
from dendro_inspector.schemas.decisions import UserClaimVerdict
from dendro_inspector.schemas.evidence import (
    AttachmentStatus,
    EvidencePacket,
    Observation,
    ObservationSource,
    Subject,
)
from dendro_inspector.schemas.input import CaseInput
from dendro_inspector.schemas.taxon import Resolution
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


class TestPhaseZeroClaimGates:
    """Failing gates for F4 and F5 in `docs/specs/core-logic-hardening.md`.

    Strict, so the marker must be removed by the commit that fixes the finding.
    """

    def test_an_unattached_contradiction_cannot_reject_a_version(self, node_context):
        """FAILURE 6 cuts both ways.

        Foliage that cannot be traced to this trunk may not raise a claim, so it may not
        demolish the user's claim either. Here the only observation contradicting Picea
        carries `attachment: unknown`; the evidence hierarchy projects it to context.
        """
        loose = Observation(
            observation_id="o2",
            feature="needles.fascicles",
            value="two",
            subject_id="log_1",
            source=ObservationSource.IMAGE,
            image_id="img-1",
            attachment=AttachmentStatus.UNKNOWN,
        )
        verdict = rule_on_user_claim(
            _state(user_claim="ялина"),
            node_context,
            "log_1",
            _candidates("pinus"),
            _evidence(_obs("o1", "needles.persistence", "evergreen"), loose),
            "pinus",
        )

        assert verdict is not UserClaimVerdict.REJECTED

    def test_a_hedged_claim_is_accepted_when_either_member_is_selected(self, node_context):
        """A user who hedges is not to be punished for it."""
        verdict = rule_on_user_claim(
            _state(user_claim="дуб або ясен"),
            node_context,
            "log_1",
            _candidates("quercus"),
            _evidence(_obs("o1", "bark.pattern", "diamond_fissures")),
            "quercus",
        )

        assert verdict is UserClaimVerdict.ACCEPTED

    def test_a_negated_claim_does_not_name_the_taxon_it_denies(self, node_context):
        verdict = rule_on_user_claim(
            _state(user_claim="не дуб"),
            node_context,
            "log_1",
            _candidates("quercus"),
            _evidence(_obs("o1", "bark.pattern", "diamond_fissures")),
            "quercus",
        )

        assert verdict is not UserClaimVerdict.ACCEPTED

    def test_a_one_letter_claim_matches_nothing(self, knowledge):
        """`a` used to match 22 of the 25 cards as a substring."""
        assert resolve_user_claim("a", knowledge) == ()


class TestClaimResolution:
    """C4: a claim is resolved to every taxon it names, and to nothing it denies."""

    @pytest.mark.parametrize(
        ("claim", "expected"),
        [
            ("дуб", ("quercus",)),
            ("oak", ("quercus",)),
            ("Quercus robur", ("quercus",)),
            ("дуб або ясен", ("fraxinus", "quercus")),
            ("ясен або дуб", ("fraxinus", "quercus")),
            ("не дуб", ()),
            ("not oak", ()),
            ("a", ()),
            ("", ()),
        ],
    )
    def test_the_claim_resolves_to_every_taxon_it_names(self, knowledge, claim, expected):
        assert set(resolve_user_claim(claim, knowledge)) == set(expected)

    def test_a_negation_removes_only_the_word_it_denies(self, knowledge):
        """ "не дуб, скоріше ясен" is still a claim about ash."""
        assert resolve_user_claim("не дуб, скоріше ясен", knowledge) == ("fraxinus",)

    def test_the_longest_matching_name_orders_the_result(self, knowledge):
        """Order is by how much of the card the user actually named, not by catalogue
        position, so a disjunction reads back in the order a person would rank it."""
        resolved = resolve_user_claim("quercus ясен", knowledge)

        assert resolved == ("quercus", "fraxinus")

    def test_a_hedge_is_ruled_on_by_its_best_member(self, node_context):
        """The other member losing is not the user being wrong."""
        for selected in ("quercus", "fraxinus"):
            verdict = rule_on_user_claim(
                _state(user_claim="дуб або ясен"),
                node_context,
                "log_1",
                _candidates(selected),
                _evidence(_obs("o1", "bark.pattern", "diamond_fissures")),
                selected,
            )

            assert verdict is UserClaimVerdict.ACCEPTED

    def test_a_wholly_negated_claim_is_unrecognised_rather_than_ruled_against(self, node_context):
        """The denied taxon must not be read as the version to rule on: that would turn
        "it is not an oak" into a rejected oak claim, which the user never made."""
        verdict = rule_on_user_claim(
            _state(user_claim="не дуб"),
            node_context,
            "log_1",
            _candidates("quercus"),
            _evidence(_obs("o1", "bark.pattern", "diamond_fissures")),
            "quercus",
        )

        assert verdict is UserClaimVerdict.POSSIBLE
