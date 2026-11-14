"""SDK image wire format and cache boundaries, entirely against recording clients."""

from __future__ import annotations

import asyncio
import base64
import io
import json
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel

from dendro_inspector.cli import _build_case
from dendro_inspector.providers.anthropic_sdk_adapter import AnthropicSDKProvider
from dendro_inspector.providers.base import (
    CACHE_PREFIX_CHARS,
    USAGE_SINK,
    ImageInput,
    InvalidImageError,
    UsageSink,
)
from dendro_inspector.providers.openai_adapter import OpenAIProvider
from dendro_inspector.schemas.input import DeclaredObjectType, Season


class Answer(BaseModel):
    verdict: str


class RecordingClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=self)
        self.base_url = SimpleNamespace(host="api.openai.com")

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"verdict":"uncertain"}'))],
            usage=SimpleNamespace(
                prompt_tokens=300,
                prompt_tokens_details=SimpleNamespace(cached_tokens=200),
                completion_tokens=50,
                completion_tokens_details=SimpleNamespace(reasoning_tokens=30),
            ),
        )


@pytest.mark.parametrize(
    ("filename", "expected"), [("leaf.png", "image/png"), ("bark.webp", "image/webp")]
)
def test_cli_preserves_image_format_hints(tmp_path, filename, expected):
    case = _build_case(
        [tmp_path / filename],
        case_id="mime-case",
        text=None,
        claim=None,
        field_context=False,
        location=None,
        season=Season.UNKNOWN,
        habitat=None,
        object_type=DeclaredObjectType.UNKNOWN,
        lang="en",
    )
    assert case.images[0].media_type == expected


def run_openai(provider, prompt, images=(), *, boundary=0, sink=None):
    return asyncio.run(
        provider.generate_structured(
            role="primary",
            prompt=prompt,
            images=images,
            response_model=Answer,
            metadata={CACHE_PREFIX_CHARS: boundary, USAGE_SINK: sink},
        )
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (b"\xff\xd8\xff\xdbsynthetic", "image/jpeg"),
        (b"\x89PNG\r\n\x1a\nsynthetic", "image/png"),
        (b"GIF89asynthetic", "image/gif"),
        (b"RIFF\x00\x00\x00\x00WEBPsynthetic", "image/webp"),
    ],
)
def test_sdk_mime_matches_bytes_despite_misleading_filename_and_hint(tmp_path, raw, expected):
    path = tmp_path / "mislabelled.jpg"
    path.write_bytes(raw)
    image = ImageInput("img-2", path, media_type="image/jpeg")

    openai_part = OpenAIProvider._image_part(image)
    header, encoded = openai_part["image_url"]["url"].split(",", 1)
    assert header == f"data:{expected};base64"
    assert base64.b64decode(encoded, validate=True) == raw

    claude_part = AnthropicSDKProvider._image_block(image)
    assert claude_part["source"]["media_type"] == expected
    assert base64.b64decode(claude_part["source"]["data"], validate=True) == raw


def test_preparation_uses_actual_format_without_converting_a_png_to_jpeg(tmp_path):
    pil = pytest.importorskip("PIL.Image")
    path = tmp_path / "mislabelled.jpg"
    pil.new("RGBA", (2000, 1000), (10, 20, 30, 80)).save(path, format="PNG")

    data, media_type = ImageInput("img-1", path, max_edge_px=1568).read_payload()

    assert media_type == "image/png"
    with pil.open(io.BytesIO(data)) as decoded:
        assert decoded.format == "PNG"
        assert decoded.mode == "RGBA"
        assert decoded.size == (1568, 784)


def test_invalid_image_is_rejected_before_a_model_request(tmp_path):
    path = tmp_path / "photo.jpg"
    path.write_bytes(b"this is not a supported image")
    provider = OpenAIProvider(model="test", image_mode="inline")
    client = RecordingClient()
    provider._client = client

    with pytest.raises(InvalidImageError, match="JPEG, PNG, GIF or WebP"):
        run_openai(provider, "inspect", (ImageInput("img-1", path),))
    assert not client.calls


def test_openai_keeps_images_and_shared_text_in_a_stable_leading_message(tmp_path):
    path = tmp_path / "photo.jpg"
    path.write_bytes(b"\xff\xd8\xff\xdbsynthetic")
    image = ImageInput("img-9", path)
    provider = OpenAIProvider(model="test", image_mode="inline")
    client = RecordingClient()
    provider._client = client
    prefix = "SHARED POLICY\n"

    for tail in ("review botanical evidence", "review confidence"):
        run_openai(provider, prefix + tail, (image,), boundary=len(prefix))

    first, second = [call["messages"] for call in client.calls]
    assert first[0] == second[0]
    assert first[0]["content"][0] == {"type": "text", "text": prefix}
    assert first[0]["content"][1] == {"type": "text", "text": "Image ID: img-9"}
    assert first[0]["content"][2]["type"] == "image_url"
    assert first[1]["content"][0]["text"] == "review botanical evidence"
    assert second[1]["content"][0]["text"] == "review confidence"
    assert client.calls[0]["response_format"] == client.calls[1]["response_format"]


@pytest.mark.parametrize("boundary", [0, 7, 999])
def test_splitting_openai_messages_preserves_all_prompt_characters(boundary):
    provider = OpenAIProvider(model="test", image_mode="inline")
    client = RecordingClient()
    provider._client = client
    prompt = "POLICY\n\nDo not invent evidence."

    run_openai(provider, prompt, boundary=boundary)

    messages = client.calls[0]["messages"]
    assert all(message["content"] for message in messages)
    assert "".join(part["text"] for m in messages for part in m["content"]) == prompt


def test_anthropic_labels_every_image_inside_the_shared_cache_boundary(tmp_path):
    path = tmp_path / "photo.jpg"
    path.write_bytes(b"\xff\xd8\xff\xdbsynthetic")
    images = (ImageInput("img-2", path), ImageInput("img-7", path))
    request = AnthropicSDKProvider()._request(
        "POLICY\nreview", images, Answer, {CACHE_PREFIX_CHARS: len("POLICY\n")}
    )
    blocks = request["messages"][0]["content"]

    assert [block["text"] for block in blocks[:4] if block["type"] == "text"] == [
        "Image ID: img-2",
        "Image ID: img-7",
    ]
    assert blocks[4]["cache_control"] == {"type": "ephemeral"}
    assert blocks[4]["text"] == "POLICY\n"
    assert blocks[5]["text"] == "review"
    # Encoded image data belongs only in native image blocks, never in text.
    for block in blocks:
        if block["type"] == "text":
            assert "base64" not in block["text"]
    assert json.loads(json.dumps(request))["output_config"]["format"]["type"] == "json_schema"


def test_openai_records_reasoning_without_double_counting_output():
    provider = OpenAIProvider(model="test", image_mode="inline")
    provider._client = RecordingClient()
    usage = UsageSink()
    run_openai(provider, "inspect", sink=usage)
    assert (usage.input_tokens, usage.cached_input_tokens) == (300, 200)
    assert (usage.output_tokens, usage.reasoning_output_tokens) == (50, 30)
