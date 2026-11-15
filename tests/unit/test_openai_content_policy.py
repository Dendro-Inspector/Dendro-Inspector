"""A content-filter refusal is its own error: the same input is refused on every retry."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel

from dendro_inspector.providers.base import ContentPolicyError, ImageInput
from dendro_inspector.providers.openai_adapter import OpenAIProvider, _is_content_policy_refusal


class Answer(BaseModel):
    verdict: str


class APIError(Exception):
    """An SDK status error, reduced to what the adapter reads from it."""

    def __init__(self, message: str, *, status_code: int, code: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


# The wording an Azure OpenAI / Foundry deployment returned for a photograph of cut logs.
FOUNDRY_REFUSAL = (
    "Error code: 400 - Your input image may contain content that is not allowed by our "
    "content safety system."
)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (APIError(FOUNDRY_REFUSAL, status_code=400), True),
        (APIError("refused", status_code=400, code="content_filter"), True),
        (APIError("refused", status_code=400, code="content_policy_violation"), True),
        # Any other bad request stays what it was: a request this adapter built wrongly.
        (APIError("Invalid schema for response_format", status_code=400), False),
        # The filter answers with a 400; the same words on a 429 or 500 are something else.
        (APIError(FOUNDRY_REFUSAL, status_code=500), False),
        (RuntimeError(FOUNDRY_REFUSAL), False),
    ],
)
def test_only_a_filter_refusal_is_recognised(error: Exception, expected: bool) -> None:
    assert _is_content_policy_refusal(error) is expected


def _client(error: Exception) -> Any:
    async def refuse(**kwargs: Any) -> Any:
        raise error

    async def upload(**kwargs: Any) -> Any:
        return SimpleNamespace(id="file-1")

    return SimpleNamespace(
        files=SimpleNamespace(create=upload),
        responses=SimpleNamespace(create=refuse),
        chat=SimpleNamespace(completions=SimpleNamespace(create=refuse)),
        base_url=SimpleNamespace(host="example.services.ai.azure.com"),
    )


@pytest.mark.parametrize("image_mode", ["files", "inline"])
def test_a_refused_photograph_surfaces_as_a_content_policy_error(tmp_path, image_mode) -> None:
    path = tmp_path / "logs.jpg"
    path.write_bytes(b"\xff\xd8\xff\xdbsynthetic-image")
    provider = OpenAIProvider(image_mode=image_mode)
    provider._client = _client(APIError(FOUNDRY_REFUSAL, status_code=400))

    with pytest.raises(ContentPolicyError, match="content safety system") as raised:
        asyncio.run(
            provider.generate_structured(
                role="primary",
                prompt="inspect",
                images=(ImageInput("img-1", path),),
                response_model=Answer,
                metadata={"node": "planner"},
            )
        )
    assert "node=planner" in str(raised.value)


def test_another_bad_request_is_passed_through_unchanged(tmp_path) -> None:
    error = APIError("Invalid schema for response_format", status_code=400)
    provider = OpenAIProvider(image_mode="inline")
    provider._client = _client(error)

    with pytest.raises(APIError) as raised:
        asyncio.run(
            provider.generate_structured(
                role="primary",
                prompt="inspect",
                images=(),
                response_model=Answer,
                metadata={},
            )
        )
    assert raised.value is error


@pytest.mark.parametrize(
    "mode", ["chat-refusal", "chat-filter", "response-refusal", "response-filter"]
)
def test_successful_http_refusals_are_terminal_without_repair(mode: str) -> None:
    calls = []
    provider = OpenAIProvider(image_mode="inline" if mode.startswith("chat") else "files")

    async def create(**kwargs: Any) -> Any:
        calls.append(kwargs)
        if mode.startswith("chat"):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="content_filter" if mode == "chat-filter" else "stop",
                        message=SimpleNamespace(
                            content=None, refusal="refused" if mode == "chat-refusal" else None
                        ),
                    )
                ],
                usage=None,
            )
        return SimpleNamespace(
            output=[SimpleNamespace(content=[SimpleNamespace(type="refusal")])]
            if mode == "response-refusal"
            else [],
            status="incomplete" if mode == "response-filter" else "completed",
            incomplete_details=SimpleNamespace(reason="content_filter"),
            usage=None,
        )

    provider._client = SimpleNamespace(
        responses=SimpleNamespace(create=create),
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
    )
    with pytest.raises(ContentPolicyError):
        asyncio.run(
            provider.generate_structured(
                role="reviewer", prompt="inspect", images=(), response_model=Answer, metadata={}
            )
        )
    assert len(calls) == 1
    schema = (
        calls[0]["response_format"]["json_schema"]
        if mode.startswith("chat")
        else calls[0]["text"]["format"]
    )
    assert schema["strict"] is True
    assert schema["schema"]["required"] == ["verdict"]
