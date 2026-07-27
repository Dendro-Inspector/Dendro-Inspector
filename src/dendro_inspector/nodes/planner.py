"""Planner.

Decides what to look for before looking. The model proposes target features; deterministic
facts about the request (declared object type, image count) are merged in afterwards so a
model cannot talk the graph out of knowing that a bark-only request is bark-only.
"""

from __future__ import annotations

from dendro_inspector.config import Role
from dendro_inspector.graph.executor import NodeContext
from dendro_inspector.graph.state import GraphState, InspectionPlan
from dendro_inspector.nodes._support import case_context, image_inputs, locale_of
from dendro_inspector.providers.base import request_structured
from dendro_inspector.schemas.input import DeclaredObjectType

NODE = "planner"

BARK_ONLY_TYPES: frozenset[DeclaredObjectType] = frozenset(
    {
        DeclaredObjectType.BARK,
        DeclaredObjectType.WOOD,
        DeclaredObjectType.SPLIT_FIREWOOD,
    }
)


def deterministic_facts(state: GraphState) -> dict[str, bool]:
    """Facts the graph knows without asking a model."""
    split_firewood = state.case.declared_object_type is DeclaredObjectType.SPLIT_FIREWOOD
    return {
        "bark_only_input": state.case.declared_object_type in BARK_ONLY_TYPES,
        "expect_multiple_subjects": len(state.case.images) > 1 or split_firewood,
        "split_firewood_input": split_firewood,
    }


async def run(state: GraphState, ctx: NodeContext) -> GraphState:
    provider = ctx.providers.get(Role.PRIMARY)
    plan = await request_structured(
        provider=provider,
        role=Role.PRIMARY.value,
        node=NODE,
        prompt=ctx.prompts.compose(
            NODE,
            context=case_context(state.case),
            locale=locale_of(state),
        ),
        images=image_inputs(state.case),
        response_model=InspectionPlan,
        recorder=ctx.recorder,
        max_retries=ctx.config.provider_for(Role.PRIMARY).max_structured_retries,
    )

    facts = deterministic_facts(state)
    merged = plan.model_copy(
        update={
            "bark_only_input": plan.bark_only_input or facts["bark_only_input"],
            "expect_multiple_subjects": plan.expect_multiple_subjects
            or facts["expect_multiple_subjects"],
            "split_firewood_input": plan.split_firewood_input or facts["split_firewood_input"],
        }
    )
    return state.evolve(plan=merged)
