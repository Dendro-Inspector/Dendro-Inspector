"""Evil Duck presentation layer.

Adds voice. Adds nothing else.

The domain prompt's central rule is that the duck must be angry **but not stupid** —
sharpness is earned by evidence, never asserted. So this node does not decide how hard to
bite, and it does not own the words either:

* `response_composer.decide_tone` computes `tone_mode` and `joke_allowed` from the evidence
  tier, the confidence, the ruling on the user's version and the accepted findings;
* the selected personality profile under `prompts/personality/` supplies the vocabulary.

This node only renders the mode it was handed, in the register it was configured with.
Register is a deployment choice; the dendrology policy is not. Swapping
`EVIL_DUCK_TONE_PROFILE` changes how an answer sounds and can never change what it says.

Deterministic and template-driven — no model call. A model asked to "make this punchier"
will eventually make it more certain, and confidence inflation is the failure this whole
project exists to prevent. The guarantee is enforced, not promised:
:func:`assert_tone_preserved_decision` compares the structured results, decisions, tone mode
and joke permission before and after, and this node raises if anything moved.
"""

from __future__ import annotations

from evil_duck_dendro.graph.executor import NodeContext
from evil_duck_dendro.graph.state import GraphState
from evil_duck_dendro.prompts.library import PersonalityProfile
from evil_duck_dendro.schemas.decisions import (
    CaseResponse,
    DecisionStatus,
    ToneMode,
    assert_tone_preserved_decision,
)

NODE = "tone_layer"

FALLBACK_LOCALE = "uk"


def _insufficient(response: CaseResponse) -> bool:
    return all(
        decision.status is DecisionStatus.INSUFFICIENT_EVIDENCE for decision in response.decisions
    ) and bool(response.decisions)


def apply_tone(response: CaseResponse, profile: PersonalityProfile) -> CaseResponse:
    """Wrap the composed text in voice. Structured fields pass through untouched."""
    locale = response.locale if response.locale in profile.openers else FALLBACK_LOCALE
    mode = response.tone_mode

    # A weak photograph is never delivered in hard voice, whatever else was computed.
    if _insufficient(response) and mode is not ToneMode.CORRECTIVE:
        mode = ToneMode.CAUTIOUS

    parts = [
        profile.opener(locale, mode.value),
        response.human_readable,
        f"_{profile.closer(locale, mode.value)}_",
    ]
    if response.joke_allowed and mode is ToneMode.HARD:
        parts.insert(2, profile.joke(locale))

    toned = response.model_copy(update={"human_readable": "\n\n".join(parts), "tone_applied": True})
    assert_tone_preserved_decision(response, toned)
    return toned


async def run(state: GraphState, ctx: NodeContext) -> GraphState:
    response = state.response
    if response is None:
        return state
    return state.evolve(final_response=apply_tone(response, ctx.prompts.personality))
