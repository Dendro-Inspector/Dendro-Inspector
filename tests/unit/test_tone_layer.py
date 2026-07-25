"""The tone layer may add voice and nothing else.

This is the test that keeps the duck honest. If someone later replaces the deterministic
tone layer with a model call, these assertions are what stop "make it punchier" from
quietly becoming "make it more certain".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from evil_duck_dendro.config import DEFAULT_PERSONALITY_ROOT
from evil_duck_dendro.nodes.tone_layer import apply_tone as _apply_tone
from evil_duck_dendro.prompts.library import load_personality_profile
from evil_duck_dendro.schemas.decisions import (
    CaseResponse,
    DecisionStatus,
    FinalDecision,
    StructuredFinalResult,
    ToneMode,
    assert_tone_preserved_decision,
)
from evil_duck_dendro.schemas.taxon import Confidence, Resolution

PUBLIC_PROFILE = load_personality_profile(
    Path(__file__).resolve().parents[2] / DEFAULT_PERSONALITY_ROOT / "evil-duck-public.md"
)
UNFILTERED_PROFILE = load_personality_profile(
    Path(__file__).resolve().parents[2] / DEFAULT_PERSONALITY_ROOT / "evil-duck.md"
)


def apply_tone(response, profile=PUBLIC_PROFILE):
    """Default to the public profile, matching the shipped configuration."""
    return _apply_tone(response, profile)


def _response() -> CaseResponse:
    decision = FinalDecision(
        subject_id="log_1",
        selected_taxon="pinus",
        resolution=Resolution.GENUS,
        confidence=Confidence.MEDIUM,
        status=DecisionStatus.PROBABLE,
        nearest_alternative="picea",
    )
    result = StructuredFinalResult(
        verdict="Probable: Pinus at genus level (medium confidence).",
        subject="log_1",
        taxonomic_resolution=Resolution.GENUS,
        confidence=Confidence.MEDIUM,
        nearest_alternative="picea",
    )
    return CaseResponse(
        case_id="c1",
        results=(result,),
        decisions=(decision,),
        human_readable="**1. Verdict.** Probable: Pinus at genus level.",
    )


class TestPreservation:
    def test_tone_changes_only_the_prose(self):
        before = _response()
        after = apply_tone(before)
        assert after.decisions == before.decisions
        assert after.results == before.results
        assert after.tone_applied
        assert after.human_readable != before.human_readable

    def test_the_original_text_survives_inside_the_toned_output(self):
        before = _response()
        assert before.human_readable in apply_tone(before).human_readable

    def test_taxon_and_confidence_are_untouched(self):
        after = apply_tone(_response())
        assert after.decisions[0].selected_taxon == "pinus"
        assert after.decisions[0].confidence is Confidence.MEDIUM
        assert after.decisions[0].resolution is Resolution.GENUS


class TestGuardrail:
    def test_mutating_a_decision_is_a_contract_violation(self):
        before = _response()
        tampered = before.model_copy(
            update={
                "decisions": (
                    before.decisions[0].model_copy(update={"confidence": Confidence.HIGH}),
                )
            }
        )
        with pytest.raises(ValueError, match="mutated final decisions"):
            assert_tone_preserved_decision(before, tampered)

    def test_mutating_a_structured_result_is_a_contract_violation(self):
        before = _response()
        tampered = before.model_copy(
            update={
                "results": (
                    before.results[0].model_copy(
                        update={"taxonomic_resolution": Resolution.SPECIES}
                    ),
                )
            }
        )
        with pytest.raises(ValueError, match="mutated structured results"):
            assert_tone_preserved_decision(before, tampered)


class TestFraming:
    def test_ukrainian_is_the_default_output_language(self):
        """Section 4 of the domain prompt: `Мова відповіді за замовчуванням: українська`."""
        assert "Кряк" in apply_tone(_response()).human_readable

    def test_english_is_available(self):
        english = _response().model_copy(update={"locale": "en"})
        assert "Kryak" in apply_tone(english).human_readable

    def test_insufficient_evidence_is_always_delivered_cautiously(self):
        """Whatever mode was computed, a weak photograph is never delivered in hard voice."""
        base = _response().model_copy(update={"tone_mode": ToneMode.HARD, "joke_allowed": True})
        weak = base.model_copy(
            update={
                "decisions": (
                    base.decisions[0].model_copy(
                        update={
                            "status": DecisionStatus.INSUFFICIENT_EVIDENCE,
                            "confidence": Confidence.LOW,
                        }
                    ),
                )
            }
        )
        text = apply_tone(weak).human_readable
        assert "не бив 95+" in text
        assert "не проходить" not in text

    def test_hard_mode_renders_the_sharp_opener(self):
        hard = _response().model_copy(update={"tone_mode": ToneMode.HARD})
        assert "не проходить" in apply_tone(hard).human_readable

    def test_corrective_mode_admits_the_error_without_sarcasm(self):
        corrective = _response().model_copy(update={"tone_mode": ToneMode.CORRECTIVE})
        text = apply_tone(corrective).human_readable
        assert "Приймаю" in text
        assert "Кряк" not in text


class TestJokePermission:
    def test_a_joke_needs_hard_mode_and_explicit_permission(self):
        allowed = _response().model_copy(update={"tone_mode": ToneMode.HARD, "joke_allowed": True})
        assert "Дендрологія" in apply_tone(allowed).human_readable

    def test_no_joke_without_permission(self):
        denied = _response().model_copy(update={"tone_mode": ToneMode.HARD, "joke_allowed": False})
        assert "Дендрологія" not in apply_tone(denied).human_readable

    def test_no_joke_in_measured_mode_even_if_permitted(self):
        odd = _response().model_copy(update={"tone_mode": ToneMode.MEASURED, "joke_allowed": True})
        assert "Дендрологія" not in apply_tone(odd).human_readable

    def test_no_joke_after_the_system_has_been_corrected(self):
        """Section 12: after admitting an error, no joke and no belittling."""
        corrective = _response().model_copy(
            update={"tone_mode": ToneMode.CORRECTIVE, "joke_allowed": True}
        )
        assert "Дендрологія" not in apply_tone(corrective).human_readable

    def test_the_tone_layer_cannot_grant_itself_permission(self):
        before = _response()
        forged = before.model_copy(update={"joke_allowed": True})
        with pytest.raises(ValueError, match="granted itself permission"):
            assert_tone_preserved_decision(before, forged)

    def test_the_tone_layer_cannot_escalate_its_own_mode(self):
        before = _response()
        forged = before.model_copy(update={"tone_mode": ToneMode.HARD})
        with pytest.raises(ValueError, match="changed its own permission level"):
            assert_tone_preserved_decision(before, forged)


class TestProfileSeparation:
    """Register is a deployment choice; the science is not."""

    def test_the_public_profile_is_the_shipped_default(self):
        assert PUBLIC_PROFILE.profile == "evil_duck_public"
        assert not PUBLIC_PROFILE.allows_profanity

    def test_the_unfiltered_profile_declares_itself(self):
        assert UNFILTERED_PROFILE.allows_profanity

    def test_both_profiles_cover_every_mode_and_locale(self):
        for profile in (PUBLIC_PROFILE, UNFILTERED_PROFILE):
            for locale in ("uk", "en"):
                for mode in ToneMode:
                    assert profile.opener(locale, mode.value)
                    assert profile.closer(locale, mode.value)
                assert profile.joke(locale)

    def test_switching_profile_changes_wording_and_nothing_else(self):
        response = _response().model_copy(update={"tone_mode": ToneMode.HARD})
        public = _apply_tone(response, PUBLIC_PROFILE)
        unfiltered = _apply_tone(response, UNFILTERED_PROFILE)

        assert public.human_readable != unfiltered.human_readable
        assert public.decisions == unfiltered.decisions
        assert public.results == unfiltered.results
        assert public.tone_mode is unfiltered.tone_mode

    def test_the_public_profile_carries_a_register_instruction_for_the_model(self):
        note = PUBLIC_PROFILE.register_note("uk")
        assert note
        assert "публічний" in note
