"""Run an isolated card-free assessment after the production answer has been frozen.

Running last prevents its hypotheses, failure or timeout from changing planner/extractor
inputs or consuming the production graph's deadline. It is a temporary measurement arm,
not another permanent identification step. It requires explicit opt-in and a paid pilot
still needs an operator-approved budget.
"""

from __future__ import annotations

import asyncio
import hashlib
import time

from dendro_inspector.config import Role
from dendro_inspector.graph.executor import NodeContext
from dendro_inspector.nodes._support import case_context, image_inputs, locale_of_case
from dendro_inspector.observability.events import NodeStatus
from dendro_inspector.observability.trace import TraceRecorder
from dendro_inspector.providers.base import request_structured
from dendro_inspector.schemas.input import CaseInput
from dendro_inspector.schemas.vision import VisionAssessment, VisionShadowResult

NODE = "vision_assessment"


async def assess_shadow(case: CaseInput, ctx: NodeContext) -> VisionShadowResult:
    config = ctx.config.graph
    if not config.vision_shadow:
        return VisionShadowResult(status="skipped", reason="disabled")
    if not case.images or len(case.images) > config.vision_shadow_max_images:
        return VisionShadowResult(status="skipped", reason="image_count_budget")
    try:
        if any(not image.exists for image in case.images):
            return VisionShadowResult(status="skipped", reason="missing_image")
        if sum(image.path.stat().st_size for image in case.images) > config.vision_shadow_max_bytes:
            return VisionShadowResult(status="skipped", reason="image_byte_budget")
    except OSError:
        return VisionShadowResult(status="skipped", reason="unreadable_image")

    provider = ctx.providers.get(Role.PRIMARY)
    recorder = TraceRecorder(case.case_id)
    started = time.perf_counter()
    prompt = ctx.prompts.compose(NODE, context=case_context(case), locale=locale_of_case(case))
    assessment: VisionAssessment | None = None
    reason: str | None = None

    def validate(result: VisionAssessment) -> None:
        if any(not set(subject.image_ids) <= set(case.image_ids) for subject in result.subjects):
            raise ValueError("vision assessment cites images not supplied with this case")

    try:
        async with asyncio.timeout(config.vision_shadow_timeout_seconds):
            assessment = await request_structured(
                provider=provider,
                role=Role.PRIMARY.value,
                node=NODE,
                prompt=prompt,
                images=image_inputs(case, max_edge_px=config.image_max_edge_px),
                response_model=VisionAssessment,
                recorder=recorder,
                max_retries=0,
                validate_response=validate,
                cache_prefix_chars=ctx.prompts.cacheable_prefix_chars(locale_of_case(case)),
            )
    except TimeoutError:
        reason = "timeout"
    except Exception as exc:
        # Preserve the completed production answer. Exception text may contain private
        # provider data; the stable class identifies the failure without copying it.
        reason = type(exc).__name__
    recorder.record_node(NODE, status=NodeStatus.OK if assessment else NodeStatus.FAILED)
    trace = recorder.build()
    calls = tuple(call for event in trace.events for call in event.provider_calls)
    return VisionShadowResult(
        status="completed" if assessment is not None else "failed",
        reason=reason,
        assessment=assessment,
        prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
        adapter=provider.adapter_name,
        model=provider.model,
        duration_ms=(time.perf_counter() - started) * 1000,
        input_tokens=(
            sum(call.input_tokens or 0 for call in calls)
            if calls and all(call.input_tokens is not None for call in calls)
            else None
        ),
        output_tokens=(
            sum(call.output_tokens or 0 for call in calls)
            if calls and all(call.output_tokens is not None for call in calls)
            else None
        ),
        attempts=sum(call.attempts for call in calls) if calls else 1,
    )
