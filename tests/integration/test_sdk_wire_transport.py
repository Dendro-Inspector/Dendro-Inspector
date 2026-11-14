"""Real SDK serialization into the bridge handler, with no socket or paid endpoint."""

from __future__ import annotations

import asyncio
import importlib.util
import io
import json
from email.message import Message
from pathlib import Path

import pytest
from pydantic import BaseModel

from dendro_inspector.providers.anthropic_adapter import AnthropicProvider
from dendro_inspector.providers.anthropic_sdk_adapter import AnthropicSDKProvider
from dendro_inspector.providers.base import CACHE_PREFIX_CHARS, USAGE_SINK, ImageInput, UsageSink
from dendro_inspector.providers.openai_adapter import OpenAIProvider


class Answer(BaseModel):
    verdict: str


@pytest.fixture
def wire(tmp_path):
    httpx = pytest.importorskip("httpx2")
    script = Path(__file__).parents[2] / "scripts" / "agent-provider" / "bridge.py"
    spec = importlib.util.spec_from_file_location("sdk_wire_bridge", script)
    assert spec and spec.loader
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    state = bridge.State(tmp_path / "bridge", wait_timeout=1)
    requests = []
    answers = []

    async def dispatch(request):
        raw = await request.aread()
        requests.append((request.method, request.url.path, request.headers, raw))
        handler = object.__new__(bridge.Handler)
        handler.state = state
        handler.path = request.url.path
        handler.headers = Message()
        for name, value in request.headers.items():
            handler.headers[name] = value
        handler.rfile = io.BytesIO(raw)
        sent = []
        handler._send = lambda status, body: sent.append((status, body))

        def answer(*args, **kwargs):
            answers.append(args)
            return (
                '{"verdict":"uncertain"}',
                "synthetic",
                {
                    "input_tokens": 600,
                    "cached_input_tokens": 200,
                    "cache_write_input_tokens": 300,
                    "output_tokens": 50,
                    "reasoning_output_tokens": 20,
                },
            )

        handler._answer = answer
        if request.method == "POST":
            handler.do_POST()
        elif request.method == "DELETE":
            handler.do_DELETE()
        else:
            raise AssertionError(f"Unexpected request: {request.method} {request.url.path}")
        assert len(sent) == 1
        status, body = sent[0]
        return httpx.Response(status, json=body, request=request)

    return httpx, dispatch, state, requests, answers, bridge


def make_client(family, wire, *, key="synthetic-sdk-key"):
    httpx, dispatch, *_ = wire
    http = httpx.AsyncClient(transport=httpx.MockTransport(dispatch), trust_env=False)
    options = {
        "api_key": key,
        "base_url": "https://bridge.invalid/v1" if family == "openai" else "https://bridge.invalid",
        "http_client": http,
        "max_retries": 0,
    }
    if family == "openai":
        return pytest.importorskip("openai", minversion="3.51.0").AsyncOpenAI(**options)
    return pytest.importorskip("anthropic", minversion="2.8.0").AsyncAnthropic(**options)


@pytest.mark.parametrize("adapter", [OpenAIProvider, AnthropicProvider, AnthropicSDKProvider])
@pytest.mark.parametrize("mode", ["files", "inline"])
def test_real_sdks_preserve_images_prompts_usage_and_cleanup(wire, tmp_path, adapter, mode):
    _, _, state, requests, answers, _ = wire
    pil = pytest.importorskip("PIL.Image")
    photo = tmp_path / "private-operator-name.jpg"
    pil.new("RGB", (16, 8), "green").save(photo, format="PNG")
    # Intentionally incorrect extension and hint: the transmitted MIME must follow bytes.
    image = ImageInput("img-7", photo, media_type="image/jpeg")
    family = "openai" if adapter is OpenAIProvider else "anthropic"
    provider = adapter(model="synthetic-model", image_mode=mode)
    provider._client = make_client(family, wire)
    sink = UsageSink()

    async def run():
        try:
            for task in ("botanical review", "confidence review"):
                result = await provider.generate_structured(
                    role="reviewer",
                    prompt="COMMON\n" + task,
                    images=(image,),
                    response_model=Answer,
                    metadata={CACHE_PREFIX_CHARS: len("COMMON\n"), USAGE_SINK: sink},
                )
                assert result.verdict == "uncertain"
        finally:
            await provider.aclose()

    asyncio.run(run())
    assert len(answers) == 2
    for answer, task in zip(answers, ("botanical review", "confidence review"), strict=True):
        assert "COMMON\n" in answer[2] and task in answer[2]
        assert "Image ID: img-7" in answer[2]
        [saved] = answer[4]
        assert saved["media_type"] == "image/png"
        assert Path(saved["path"]).read_bytes() == photo.read_bytes()
        assert answer[3]["properties"] == Answer.model_json_schema()["properties"]
    assert sink.input_tokens == 1200
    assert sink.cached_input_tokens == 400
    if not (adapter is OpenAIProvider and mode == "inline"):
        assert sink.cache_write_input_tokens == 600
    assert provider._client.is_closed()
    assert not state.files
    uploads = [request for request in requests if request[1] == "/v1/files"]
    calls = [
        request
        for request in requests
        if request[1] in {"/v1/responses", "/v1/messages", "/v1/chat/completions"}
    ]
    assert len(calls) == 2
    if mode == "files":
        assert len(uploads) == 1
        assert b"private-operator-name" not in uploads[0][3]
        assert b'filename="image.png"' in uploads[0][3]
        assert b"3600" in uploads[0][3]
        assert all(b"base64" not in call[3] for call in calls)
        assert sum(request[0] == "DELETE" for request in requests) == 1
        if adapter is OpenAIProvider:
            assert all(json.loads(call[3])["store"] is False for call in calls)
    else:
        assert not uploads
        assert all(b"base64" in call[3] for call in calls)


@pytest.mark.parametrize("family", ["openai", "anthropic"])
def test_bridge_expiry_and_credential_scope_are_enforced_by_real_sdk(wire, monkeypatch, family):
    _, _, state, requests, _, bridge = wire
    sdk = pytest.importorskip(family, minversion="3.51.0" if family == "openai" else "2.8.0")
    now = [1800000000]
    monkeypatch.setattr(bridge.time, "time", lambda: now[0])

    async def run():
        client = make_client(family, wire)
        stranger = make_client(family, wire, key="different-synthetic-key")
        try:
            file = ("synthetic.png", b"\x89PNG\r\n\x1a\nsynthetic", "image/png")
            if family == "openai":
                uploaded = await client.files.create(
                    file=file,
                    purpose="vision",
                    expires_after={"anchor": "created_at", "seconds": 3600},
                )
            else:
                uploaded = await client.files.upload(file=file, expires_in_seconds=3600)
            with pytest.raises(sdk.NotFoundError):
                await stranger.files.delete(uploaded.id)
            assert uploaded.id in state.files
            now[0] += 3601
            with pytest.raises(sdk.NotFoundError):
                await client.files.delete(uploaded.id)
            assert not state.files
        finally:
            await stranger.close()
            await client.close()

    asyncio.run(run())
    assert len(requests) == 3


@pytest.mark.parametrize("family", ["openai", "anthropic"])
def test_bridge_refuses_upload_without_expiry(wire, family):
    sdk = pytest.importorskip(family, minversion="3.51.0" if family == "openai" else "2.8.0")

    async def run():
        client = make_client(family, wire)
        try:
            file = ("synthetic.png", b"\x89PNG\r\n\x1a\nsynthetic", "image/png")
            with pytest.raises(sdk.BadRequestError):
                if family == "openai":
                    await client.files.create(file=file, purpose="vision")
                else:
                    await client.files.upload(file=file)
        finally:
            await client.close()

    asyncio.run(run())
    assert not wire[2].files
