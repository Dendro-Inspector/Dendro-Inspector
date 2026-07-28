"""The domain prompt is the same block on every call; adapters may cache it.

43% of one measured case's prompt text was the domain prompt sent seven times. Marking a
cache breakpoint after it is purely a transport optimisation, so these tests care about one
thing above all: the text the model receives must be byte-identical either way.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from pydantic import BaseModel

from dendro_inspector.providers.anthropic_adapter import AnthropicProvider
from dendro_inspector.providers.base import CACHE_PREFIX_CHARS, ImageInput, cache_prefix_of


class _Answer(BaseModel):
    verdict: str


class _RecordingClient:
    """Captures the request the adapter would send, and replies with a valid answer."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.messages = self

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        block = type("Block", (), {"type": "text", "text": json.dumps({"verdict": "betula"})})
        return type("Response", (), {"content": [block()]})()


def _run(provider: AnthropicProvider, prompt: str, prefix_chars: int) -> list[dict[str, Any]]:
    client = _RecordingClient()
    provider._client = client  # the adapter's own lazily-built client slot
    asyncio.run(
        provider.generate_structured(
            role="primary",
            prompt=prompt,
            images=(),
            response_model=_Answer,
            metadata={"node": "planner", CACHE_PREFIX_CHARS: prefix_chars},
        )
    )
    content = client.calls[0]["messages"][0]["content"]
    assert isinstance(content, list)
    return content


PROMPT = "SHARED DOMAIN POLICY\n\n---\n\nNODE INSTRUCTIONS\n\n---\n\ncase specific"
BOUNDARY = PROMPT.index("NODE INSTRUCTIONS")


def test_prefix_is_marked_as_a_cache_breakpoint():
    blocks = _run(AnthropicProvider(model="test"), PROMPT, BOUNDARY)

    cached = [block for block in blocks if "cache_control" in block]
    assert len(cached) == 1, "exactly one breakpoint, at the end of the shared prefix"
    assert cached[0]["text"] == PROMPT[:BOUNDARY]
    assert cached[0]["cache_control"] == {"type": "ephemeral"}


def test_caching_does_not_change_the_text_the_model_receives():
    """The whole optimisation is void if splitting the prompt alters or drops any of it."""
    with_cache = _run(AnthropicProvider(model="test"), PROMPT, BOUNDARY)
    without_cache = _run(AnthropicProvider(model="test"), PROMPT, 0)

    assert "".join(block["text"] for block in with_cache) == "".join(
        block["text"] for block in without_cache
    )
    assert PROMPT in "".join(block["text"] for block in with_cache)


def test_no_breakpoint_is_emitted_when_no_boundary_is_reported():
    blocks = _run(AnthropicProvider(model="test"), PROMPT, 0)
    assert not any("cache_control" in block for block in blocks)
    assert len(blocks) == 1


def test_images_fall_inside_the_cached_prefix(tmp_path):
    """A breakpoint covers everything before it, and the photograph never varies by node."""
    photo = tmp_path / "log.jpg"
    photo.write_bytes(b"\xff\xd8\xff\xdbfake-jpeg-bytes")
    client = _RecordingClient()
    provider = AnthropicProvider(model="test")
    provider._client = client
    asyncio.run(
        provider.generate_structured(
            role="primary",
            prompt=PROMPT,
            images=(ImageInput(image_id="img-1", path=photo),),
            response_model=_Answer,
            metadata={"node": "planner", CACHE_PREFIX_CHARS: BOUNDARY},
        )
    )
    blocks = client.calls[0]["messages"][0]["content"]
    image_index = next(i for i, block in enumerate(blocks) if block["type"] == "image")
    cache_index = next(i for i, block in enumerate(blocks) if "cache_control" in block)
    assert image_index < cache_index


@pytest.mark.parametrize(
    ("reported", "expected"),
    [(0, 0), (-5, 0), (None, 0), ("40", 0), (10_000, len(PROMPT)), (12, 12)],
)
def test_a_nonsensical_boundary_degrades_to_no_caching_rather_than_crashing(reported, expected):
    assert cache_prefix_of({CACHE_PREFIX_CHARS: reported}, PROMPT) == expected


def test_boundary_survives_the_repair_retry_appending_to_the_prompt():
    """`request_structured` appends its repair instruction, so the prefix stays valid."""
    repaired = PROMPT + "\n\nYour previous output was invalid: ..."
    assert cache_prefix_of({CACHE_PREFIX_CHARS: BOUNDARY}, repaired) == BOUNDARY
    assert repaired[:BOUNDARY] == PROMPT[:BOUNDARY]
