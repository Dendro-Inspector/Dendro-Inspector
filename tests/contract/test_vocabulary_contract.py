"""What the node prompts ask for and what the knowledge cards can match are one vocabulary.

Candidate admission matches a card expectation on exact `(feature, value)` equality, so a
feature path no card declares is extracted, carried through the packet, shown to every
reviewer, and then admits nothing. Asking a model for such a path spends its attention on
evidence the system has already decided it cannot use.

Colour is the deliberate exception. `bark.colour` and `inner_bark.colour` are requested on
purpose: the evidence hierarchy caps colour to bark-equivalent authority and the quality
gate reports it as intentionally weak rather than as a coverage gap. That carve-out is
asserted explicitly here so a future non-colour path cannot quietly hide inside it.

Phase 0 of `docs/specs/core-modernisation.md` (finding M1). The registry that N1 introduces
becomes the source this test reads once it exists; until then the union of the cards is the
only declaration of the vocabulary there is.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from dendro_inspector.knowledge.evidence_hierarchy import is_colour_feature
from dendro_inspector.knowledge.loader import KnowledgeBase
from dendro_inspector.knowledge.taxon_cards import card_value_vocabulary
from dendro_inspector.schemas.base import FEATURE_PATH_PATTERN

pytestmark = pytest.mark.contract

_FEATURE_PATH = re.compile(FEATURE_PATH_PATTERN)
_BACKTICKED = re.compile(r"`([^`]+)`")


def _requested_feature_paths(root: Path) -> dict[str, set[str]]:
    """Every backticked token in a node prompt that is shaped like a feature path."""
    requested: dict[str, set[str]] = {}
    for prompt in sorted((root / "prompts" / "nodes").glob("*.md")):
        for match in _BACKTICKED.finditer(prompt.read_text(encoding="utf-8")):
            token = match.group(1)
            if _FEATURE_PATH.match(token):
                requested.setdefault(token, set()).add(prompt.name)
    return requested


def _vocabulary(knowledge: KnowledgeBase) -> frozenset[str]:
    return frozenset(card_value_vocabulary(knowledge.taxa(knowledge.available_taxon_ids())))


@pytest.mark.xfail(
    strict=True,
    reason=(
        "M1 (docs/specs/core-modernisation.md): the planner prompt asks for "
        "`bark.flake_geometry` and `wood.resin_canals`, which no taxon card declares, so "
        "evidence for either is recorded and then admits nothing. Open decision 1."
    ),
)
def test_every_non_colour_feature_the_prompts_request_can_be_matched(repo_root, knowledge):
    vocabulary = _vocabulary(knowledge)
    unmatchable = {
        path: sorted(prompts)
        for path, prompts in _requested_feature_paths(repo_root).items()
        if path not in vocabulary and not is_colour_feature(path)
    }

    assert not unmatchable, (
        f"node prompts request feature paths no card can match: {unmatchable}. "
        "Either declare the feature on a card, or stop asking a model for it."
    )


def test_the_only_unmatchable_paths_the_prompts_request_are_colour(repo_root, knowledge):
    """The carve-out is colour, and nothing else may shelter under it.

    Colour is capped to bark-equivalent authority wherever it appears, and the quality gate
    separates it from genuine card gaps, so requesting it is honest. This test exists so the
    xfail above cannot be made to pass by widening the exception.
    """
    vocabulary = _vocabulary(knowledge)
    unmatchable = {path for path in _requested_feature_paths(repo_root) if path not in vocabulary}

    assert {path for path in unmatchable if is_colour_feature(path)} == {
        "bark.colour",
        "inner_bark.colour",
    }


def test_the_prompts_do_not_invent_a_feature_namespace(repo_root, knowledge):
    """Every requested path belongs to a family the evidence hierarchy already knows.

    A path in an unknown family is context tier by default, which is silent. This turns that
    silence into a failure without waiting for the registry.
    """
    from dendro_inspector.knowledge.evidence_hierarchy import _FAMILY_TIERS, family_of

    known_families = {family_of(prefix) for prefix, _ in _FAMILY_TIERS}
    unknown = {
        path: sorted(prompts)
        for path, prompts in _requested_feature_paths(repo_root).items()
        if family_of(path) not in known_families
    }

    assert not unknown, f"node prompts request paths in unknown feature families: {unknown}"
