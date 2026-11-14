"""Native SDK uploads and teardown against local recording clients; never a live API."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel

from dendro_inspector import runner
from dendro_inspector.config import Adapter, AppConfig, ProviderConfig, Role
from dendro_inspector.graph.state import GraphState
from dendro_inspector.observability.trace import TraceRecorder
from dendro_inspector.providers.anthropic_sdk_adapter import AnthropicSDKProvider
from dendro_inspector.providers.base import (
    CACHE_PREFIX_CHARS,
    USAGE_SINK,
    ImageInput,
    ProviderUnavailableError,
    StructuredOutputError,
    UsageSink,
)
from dendro_inspector.providers.image_files import SDKImageFiles
from dendro_inspector.providers.openai_adapter import OpenAIProvider
from dendro_inspector.providers.registry import ProviderRegistry
from dendro_inspector.schemas.input import CaseInput


class Answer(BaseModel):
    verdict: str


@pytest.fixture(autouse=True)
def clean_transport_environment(monkeypatch):
    monkeypatch.delenv("OPENAI_IMAGE_TRANSPORT", raising=False)
    monkeypatch.delenv("ANTHROPIC_IMAGE_TRANSPORT", raising=False)


class Client:
    def __init__(self) -> None:
        self.uploads: list[dict[str, Any]] = []
        self.requests: list[dict[str, Any]] = []
        self.deletions: list[str] = []
        self.closed = False
        self.files = SimpleNamespace(create=self.upload, upload=self.upload, delete=self.delete)
        self.responses = SimpleNamespace(create=self.respond)
        self.messages = SimpleNamespace(create=self.respond)
        self.beta = SimpleNamespace(messages=self.messages)
        self.base_url = SimpleNamespace(host="api.openai.com")
        self.status = "completed"
        self.text = '{"verdict":"uncertain"}'

    async def upload(self, **kwargs):
        self.uploads.append(kwargs)
        await asyncio.sleep(0)  # A competing reviewer must wait instead of duplicating this upload.
        return SimpleNamespace(id=f"file-{len(self.uploads)}")

    async def delete(self, file_id):
        self.deletions.append(file_id)

    async def close(self):
        self.closed = True

    async def respond(self, **kwargs):
        self.requests.append(kwargs)
        if "input" in kwargs:
            return SimpleNamespace(
                output_text=self.text,
                status=self.status,
                incomplete_details=SimpleNamespace(reason="max_output_tokens"),
                usage=SimpleNamespace(
                    input_tokens=600,
                    input_tokens_details=SimpleNamespace(cached_tokens=200, cache_write_tokens=300),
                    output_tokens=50,
                    output_tokens_details=SimpleNamespace(reasoning_tokens=30),
                ),
            )
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=self.text)],
            stop_reason="end_turn",
            usage=SimpleNamespace(
                input_tokens=100,
                cache_read_input_tokens=200,
                cache_creation_input_tokens=300,
                output_tokens=50,
            ),
        )


@pytest.fixture
def image(tmp_path):
    path = tmp_path / "private-operator-filename.jpg"
    path.write_bytes(b"\xff\xd8\xff\xdbsynthetic-image")
    return ImageInput("img-7", path)


@pytest.mark.parametrize("family", ["openai", "anthropic"])
def test_concurrent_uploads_are_deduplicated_with_expiry_and_generic_filename(family, image):
    client = Client()
    files = SDKImageFiles(family)

    async def run():
        ids = await asyncio.gather(*(files.get(client, image) for _ in range(3)))
        assert ids == ["file-1"] * 3
        await files.close(client)
        await files.close(client)

    asyncio.run(run())
    [upload] = client.uploads
    assert upload["file"] == ("image.jpeg", image.path.read_bytes(), "image/jpeg")
    if family == "openai":
        assert upload["purpose"] == "vision"
        assert upload["expires_after"] == {"anchor": "created_at", "seconds": 3600}
    else:
        assert upload["expires_in_seconds"] == 3600
    assert client.deletions == ["file-1"]


def test_same_bytes_at_different_paths_share_an_upload_but_changed_bytes_do_not(image, tmp_path):
    client = Client()
    files = SDKImageFiles("openai")
    duplicate = tmp_path / "different-name.jpg"
    duplicate.write_bytes(image.path.read_bytes())

    async def run():
        first = await files.get(client, image)
        assert await files.get(client, ImageInput("img-8", duplicate)) == first
        duplicate.write_bytes(b"\xff\xd8\xff\xdbchanged-image")
        assert await files.get(client, ImageInput("img-8", duplicate)) != first
        await files.close(client)

    asyncio.run(run())
    assert len(client.uploads) == 2
    assert set(client.deletions) == {"file-1", "file-2"}


def test_nearly_expired_references_are_refreshed_and_both_uploads_are_cleaned_up(image):
    now = [0.0]
    client = Client()
    files = SDKImageFiles("openai", clock=lambda: now[0])

    async def run():
        assert await files.get(client, image) == "file-1"
        now[0] = 3301
        assert await files.get(client, image) == "file-2"
        await files.close(client)

    asyncio.run(run())
    assert set(client.deletions) == {"file-1", "file-2"}


def test_failed_upload_is_not_cached(image):
    client = Client()
    files = SDKImageFiles("openai")

    async def fail(**kwargs):
        raise RuntimeError("synthetic upload failure")

    async def run():
        client.files.create = fail
        with pytest.raises(RuntimeError, match="synthetic upload failure"):
            await files.get(client, image)
        client.files.create = client.upload
        assert await files.get(client, image) == "file-1"
        await files.close(client)

    asyncio.run(run())
    assert len(client.uploads) == 1


@pytest.mark.parametrize("adapter", [OpenAIProvider, AnthropicSDKProvider])
def test_transport_can_be_configured_without_silently_accepting_typos(monkeypatch, adapter):
    name = "OPENAI_IMAGE_TRANSPORT" if adapter is OpenAIProvider else "ANTHROPIC_IMAGE_TRANSPORT"
    assert adapter()._image_mode == "files"
    monkeypatch.setenv(name, "inline")
    assert adapter()._image_mode == "inline"
    assert adapter(image_mode="files")._image_mode == "files"
    monkeypatch.setenv(name, "typo")
    with pytest.raises(ProviderUnavailableError, match="must be"):
        adapter()


def test_upload_cannot_fall_back_to_persistent_storage_on_an_older_sdk(image):
    client = Client()

    async def old_create(*, file, purpose):
        raise AssertionError("an upload without expiry must not run")

    client.files.create = old_create
    with pytest.raises(ProviderUnavailableError, match="expiry support"):
        asyncio.run(SDKImageFiles("openai").get(client, image))
    assert not client.uploads


def test_upload_contains_prepared_pixels_without_original_metadata(tmp_path):
    import io

    pil = pytest.importorskip("PIL.Image")
    path = tmp_path / "private-owner.jpg"
    photo = pil.new("RGB", (2000, 1000), "green")
    exif = photo.getexif()
    exif[270] = "private-owner-marker"
    photo.save(path, exif=exif)
    image = ImageInput("img-1", path, max_edge_px=1568)
    client = Client()
    files = SDKImageFiles("openai")

    async def run():
        await files.get(client, image)
        await files.close(client)

    asyncio.run(run())
    _, data, media_type = client.uploads[0]["file"]
    assert data != path.read_bytes()
    assert b"private-owner-marker" not in data
    assert media_type == "image/jpeg"
    with pil.open(io.BytesIO(data)) as uploaded:
        assert uploaded.size == (1568, 784)
        assert not uploaded.getexif()


def test_delete_failure_does_not_skip_other_files_or_log_sensitive_data(image, caplog):
    client = Client()
    files = SDKImageFiles("openai")

    async def delete(file_id):
        client.deletions.append(file_id)
        raise RuntimeError("private-filename-or-response-body")

    async def run():
        await files.get(client, image)
        image.path.write_bytes(b"\xff\xd8\xff\xdbchanged")
        await files.get(client, image)
        client.files.delete = delete
        await files.close(client)

    asyncio.run(run())
    assert set(client.deletions) == {"file-1", "file-2"}
    assert "image_file_cleanup_failed" in caplog.text
    assert "private-filename-or-response-body" not in caplog.text


def test_cleanup_timeout_returns_control_and_keeps_server_expiry_as_fallback(
    image, monkeypatch, caplog
):
    from dendro_inspector.providers import image_files

    monkeypatch.setattr(image_files, "_CLEANUP_TIMEOUT_SECONDS", 0.01)
    client = Client()
    files = SDKImageFiles("openai")

    async def stalled_delete(file_id):
        await asyncio.Event().wait()

    async def run():
        await files.get(client, image)
        client.files.delete = stalled_delete
        await files.close(client)

    asyncio.run(run())
    assert "image_file_cleanup_timed_out" in caplog.text
    assert client.uploads[0]["expires_after"]["seconds"] == 3600


def test_an_already_expired_remote_file_counts_as_cleaned_up(image):
    client = Client()
    files = SDKImageFiles("openai")

    class GoneError(Exception):
        status_code = 404

    async def deleted(file_id):
        client.deletions.append(file_id)
        raise GoneError

    async def run():
        await files.get(client, image)
        client.files.delete = deleted
        await files.close(client)
        await files.close(client)

    asyncio.run(run())
    assert client.deletions == ["file-1"]


@pytest.mark.parametrize("adapter", [OpenAIProvider, AnthropicSDKProvider])
def test_repeated_calls_reference_files_without_resending_base64(adapter, image):
    client = Client()
    provider = adapter(image_mode="files")
    provider._client = client
    usage = UsageSink()

    async def run():
        for tail in ("botanical review", "confidence review"):
            result = await provider.generate_structured(
                role="reviewer",
                prompt="COMMON\n" + tail,
                images=(image,),
                response_model=Answer,
                metadata={CACHE_PREFIX_CHARS: len("COMMON\n"), USAGE_SINK: usage},
            )
            assert result.verdict == "uncertain"
        await provider.aclose()

    asyncio.run(run())
    assert len(client.uploads) == 1
    first, second = client.requests
    assert "base64" not in json.dumps(client.requests)
    if adapter is OpenAIProvider:
        assert first["store"] is False
        assert first["input"][0] == second["input"][0]
        assert first["input"][0]["content"][2] == {
            "type": "input_image",
            "file_id": "file-1",
            "detail": "auto",
        }
        assert first["text"]["format"]["schema"] == Answer.model_json_schema()
        assert usage.reasoning_output_tokens == 60
    else:
        blocks = first["messages"][0]["content"]
        assert blocks[1] == {"type": "image", "source": {"type": "file", "file_id": "file-1"}}
        assert blocks[2]["cache_control"] == {"type": "ephemeral"}
    assert usage.input_tokens == 1200
    assert usage.cache_write_input_tokens == 600
    assert client.deletions == ["file-1"]
    assert client.closed


@pytest.mark.parametrize("failure", ["incomplete", "invalid_json"])
def test_openai_responses_failures_keep_usage_and_surface_as_provider_errors(failure):
    client = Client()
    client.status = "incomplete" if failure == "incomplete" else "completed"
    client.text = "not-json"
    provider = OpenAIProvider()
    provider._client = client
    sink = UsageSink()
    with pytest.raises(StructuredOutputError):
        asyncio.run(
            provider.generate_structured(
                role="primary",
                prompt="inspect",
                images=(),
                response_model=Answer,
                metadata={USAGE_SINK: sink},
            )
        )
    assert sink.input_tokens == 600


def test_files_mode_requires_native_support_instead_of_silently_changing_storage(image):
    provider = OpenAIProvider()
    provider._client = SimpleNamespace()
    with pytest.raises(ProviderUnavailableError, match="Responses API"):
        asyncio.run(
            provider.generate_structured(
                role="primary",
                prompt="inspect",
                images=(image,),
                response_model=Answer,
                metadata={},
            )
        )


def test_registry_shares_uploads_across_models_but_isolates_credentials_and_runs(tmp_path):
    def config(key="OPENAI_API_KEY"):
        return AppConfig(
            providers={
                Role.PRIMARY: ProviderConfig(adapter=Adapter.OPENAI, model="first"),
                Role.REVIEWER: ProviderConfig(adapter=Adapter.OPENAI, model="second"),
                Role.ARBITER: ProviderConfig(
                    adapter=Adapter.OPENAI, model="third", api_key_env=key
                ),
            }
        )

    registry = ProviderRegistry.from_config(config("OTHER_KEY"), root=tmp_path)
    primary = registry.get(Role.PRIMARY)
    reviewer = registry.get(Role.REVIEWER)
    arbiter = registry.get(Role.ARBITER)
    assert isinstance(primary, OpenAIProvider)
    assert isinstance(reviewer, OpenAIProvider)
    assert isinstance(arbiter, OpenAIProvider)
    assert primary._image_files is reviewer._image_files
    assert primary._image_files is not arbiter._image_files
    another = ProviderRegistry.from_config(config(), root=tmp_path).get(Role.PRIMARY)
    assert isinstance(another, OpenAIProvider)
    assert primary._image_files is not another._image_files


def test_anthropic_schema_repair_reuses_the_uploaded_image(monkeypatch, image):
    from dendro_inspector.providers import anthropic_sdk_adapter

    class BadSchemaError(Exception):
        pass

    monkeypatch.setattr(anthropic_sdk_adapter, "_sdk_errors", lambda: ((), (), (BadSchemaError,)))
    client = Client()
    calls = []

    async def respond(**kwargs):
        calls.append(kwargs)
        if "format" in kwargs["output_config"]:
            raise BadSchemaError
        return await client.respond(**kwargs)

    client.messages.create = respond
    provider = AnthropicSDKProvider()
    provider._client = client

    async def run():
        result = await provider.generate_structured(
            role="primary",
            prompt="inspect",
            images=(image,),
            response_model=Answer,
            metadata={},
        )
        assert result.verdict == "uncertain"
        await provider.aclose()

    asyncio.run(run())
    assert len(client.uploads) == 1
    assert len(calls) == 2
    assert calls[0]["messages"][0]["content"][1] == calls[1]["messages"][0]["content"][1]


@pytest.mark.parametrize("outcome", ["success", "failure", "cancelled"])
def test_case_runner_cleans_up_on_success_failure_and_cancellation(monkeypatch, image, outcome):
    client = Client()
    provider = OpenAIProvider()
    provider._client = client
    providers = ProviderRegistry({Role.PRIMARY: provider})
    monkeypatch.setattr(
        runner, "build_context", lambda *a, **kw: SimpleNamespace(providers=providers)
    )
    case = CaseInput(case_id="cleanup-case")

    async def graph(*args):
        await provider.generate_structured(
            role="primary", prompt="inspect", images=(image,), response_model=Answer, metadata={}
        )
        if outcome == "failure":
            raise RuntimeError("original failure")
        if outcome == "cancelled":
            raise asyncio.CancelledError
        return SimpleNamespace(
            state=GraphState(case=case), trace=TraceRecorder(case.case_id).build()
        )

    monkeypatch.setattr(runner, "run_graph", graph)
    if outcome == "success":
        asyncio.run(runner.run_case(case))
    else:
        expected = RuntimeError if outcome == "failure" else asyncio.CancelledError
        with pytest.raises(expected):
            asyncio.run(runner.run_case(case))
    assert client.deletions == ["file-1"]
    assert client.closed
