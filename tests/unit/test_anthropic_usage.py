"""Claude's ordinary input, cache reads and cache writes are disjoint counters."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from dendro_inspector.observability.trace import TraceRecorder
from dendro_inspector.providers.anthropic_adapter import AnthropicProvider
from dendro_inspector.providers.base import (
    USAGE_SINK,
    UsageSink,
    record_anthropic_usage,
    request_structured,
)


@pytest.mark.parametrize(
    ("reported", "expected"),
    [
        ({}, (None, None, None)),
        ({"input_tokens": 0}, (0, None, None)),
        ({"input_tokens": 100}, (100, None, None)),
        (
            {
                "input_tokens": 100,
                "cache_read_input_tokens": 200,
                "cache_creation_input_tokens": 300,
            },
            (600, 200, 300),
        ),
    ],
)
def test_normalized_input_is_the_sum_of_all_reported_input_categories(reported, expected):
    sink = UsageSink()
    record_anthropic_usage(SimpleNamespace(**reported), sink)
    assert (sink.input_tokens, sink.cached_input_tokens, sink.cache_write_input_tokens) == expected


class Answer(BaseModel):
    verdict: str


@pytest.mark.parametrize("bridge", [False, True])
def test_bridge_totals_are_not_added_to_their_cache_subsets_twice(bridge):
    class Client:
        async def create(self, **kwargs):
            response = SimpleNamespace(
                content=[SimpleNamespace(type="text", text='{"verdict":"uncertain"}')],
                usage=SimpleNamespace(
                    input_tokens=100, cache_read_input_tokens=200, cache_creation_input_tokens=300
                ),
            )
            if bridge:
                response.dendro_usage = {
                    "input_tokens": 600,
                    "cached_input_tokens": 200,
                    "cache_write_input_tokens": 300,
                }
            return response

    sink = UsageSink()
    provider = AnthropicProvider()
    provider._client = SimpleNamespace(messages=Client())
    asyncio.run(
        provider.generate_structured(
            role="primary",
            prompt="inspect",
            images=(),
            response_model=Answer,
            metadata={USAGE_SINK: sink},
        )
    )
    assert (sink.input_tokens, sink.cached_input_tokens, sink.cache_write_input_tokens) == (
        600,
        200,
        300,
    )


@pytest.mark.parametrize("fail", [False, True])
def test_cache_writes_survive_trace_recording_and_accumulate_over_repairs(fail):
    class Provider:
        adapter_name = "test-anthropic"
        model = "test"

        async def generate_structured(self, **kwargs):
            sink = kwargs["metadata"]["usage_sink"]
            record_anthropic_usage(
                SimpleNamespace(
                    input_tokens=100, cache_read_input_tokens=200, cache_creation_input_tokens=300
                ),
                sink,
            )
            if fail:
                return Answer.model_validate({})
            return Answer(verdict="uncertain")

    recorder = TraceRecorder("cache-test")
    call = request_structured(
        provider=Provider(),
        role="primary",
        node="planner",
        prompt="inspect",
        images=(),
        response_model=Answer,
        recorder=recorder,
        max_retries=1,
    )
    if fail:
        from dendro_inspector.providers.base import StructuredOutputError

        with pytest.raises(StructuredOutputError):
            asyncio.run(call)
    else:
        asyncio.run(call)
    recorder.record_node("planner")
    recorded = recorder.build().events[0].provider_calls[0]
    assert recorded.input_tokens == 600 * recorded.attempts
    assert recorded.cached_input_tokens == 200 * recorded.attempts
    assert recorded.cache_write_input_tokens == 300 * recorded.attempts
