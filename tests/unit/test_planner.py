"""Deterministic request facts merged by the planner."""

from __future__ import annotations

from evil_duck_dendro.graph.state import GraphState
from evil_duck_dendro.nodes.planner import deterministic_facts
from evil_duck_dendro.schemas.input import DeclaredObjectType


def test_split_firewood_forces_multiple_subject_conservatism(simple_case):
    state = GraphState(
        case=simple_case.model_copy(
            update={"declared_object_type": DeclaredObjectType.SPLIT_FIREWOOD}
        )
    )

    facts = deterministic_facts(state)

    assert facts == {
        "bark_only_input": True,
        "expect_multiple_subjects": True,
        "split_firewood_input": True,
    }


def test_ordinary_log_does_not_inherit_split_firewood_policy(simple_case):
    facts = deterministic_facts(GraphState(case=simple_case))

    assert facts["bark_only_input"] is False
    assert facts["expect_multiple_subjects"] is False
    assert facts["split_firewood_input"] is False
