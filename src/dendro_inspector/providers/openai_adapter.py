"""OpenAI adapter — integration boundary only.

Scope is deliberate: enough to be a real, runnable seam, not a feature-complete client.
The SDK is imported lazily so the package installs, imports and tests cleanly without it,
and no test in this repository can reach a paid endpoint.
"""

from __future__ import annotations

import base64
import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from dendro_inspector.providers.base import (
    ContentPolicyError,
    ImageInput,
    ProviderUnavailableError,
    ResponseT,
    StructuredOutputError,
    cache_prefix_of,
    usage_sink_of,
)
from dendro_inspector.providers.daily_budget import LIMITS, capped_response
from dendro_inspector.providers.image_files import ImageTransport, SDKImageFiles, image_transport
from dendro_inspector.providers.schema_compat import to_strict_openai_schema

DEFAULT_MODEL = "gpt-5.6"

# Request tagging (`metadata`) is an OpenAI-platform feature. Azure OpenAI / Microsoft
# Foundry reject it with a 400 unless `store` is enabled, so it is sent only to OpenAI.
OPENAI_PLATFORM_HOST = "api.openai.com"


def _accepts_metadata(client: Any) -> bool:
    """Whether the endpoint behind ``client`` accepts the chat-completions ``metadata``."""
    host = getattr(getattr(client, "base_url", None), "host", None)
    return host == OPENAI_PLATFORM_HOST


# How a content filter refuses a request: a 400 with one of these error codes, or — on
# Azure OpenAI / Microsoft Foundry, which does not always set a code — its own wording.
_CONTENT_POLICY_CODES = frozenset(
    {"content_filter", "content_policy_violation", "moderation_blocked"}
)
_CONTENT_POLICY_WORDING = (
    "content safety system",
    "content management policy",
    "content_filter",
    "content policy",
)


def _is_content_policy_refusal(exc: BaseException) -> bool:
    """Whether ``exc`` is the endpoint's content filter refusing the input itself."""
    if getattr(exc, "status_code", None) != 400:
        return False
    if getattr(exc, "code", None) in _CONTENT_POLICY_CODES:
        return True
    wording = str(exc).lower()
    return any(marker in wording for marker in _CONTENT_POLICY_WORDING)


def _content_policy_error(exc: BaseException, metadata: Mapping[str, Any]) -> ContentPolicyError:
    return ContentPolicyError(
        f"openai refused the input on content-policy grounds "
        f"(node={metadata.get('node', '?')}): {exc}"
    )


class OpenAIProvider:
    """Native Responses/file transport, with an inline Chat Completions compatibility mode."""

    adapter_name = "openai"

    def __init__(
        self,
        *,
        model: str | None = None,
        api_key_env: str = "OPENAI_API_KEY",
        timeout_seconds: float | None = None,
        image_mode: ImageTransport | None = None,
        image_files: SDKImageFiles | None = None,
    ) -> None:
        self.model = model or DEFAULT_MODEL
        self._api_key_env = api_key_env
        if timeout_seconds is not None:
            self._timeout = timeout_seconds
        else:
            try:
                self._timeout = float(os.environ.get("OPENAI_TIMEOUT_SECONDS") or 120.0)
            except ValueError:
                self._timeout = 120.0
        self._client: Any | None = None
        self._image_mode = image_transport("OPENAI_IMAGE_TRANSPORT", image_mode)
        self._image_files = image_files if image_files is not None else SDKImageFiles("openai")
        self._budget_path = os.environ.get("DENDRO_OPENAI_BUDGET_DB")
        if self._budget_path and (self.model not in LIMITS or self._image_mode != "files"):
            raise ProviderUnavailableError(
                "Capped OpenAI jobs require a permitted model and file transport"
            )

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        api_key = os.environ.get(self._api_key_env)
        if not api_key:
            msg = (
                f"{self._api_key_env} is not set. Export it, or run with the fake adapter "
                "(DENDRO_PRIMARY_PROVIDER=fake)."
            )
            raise ProviderUnavailableError(msg)
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:  # pragma: no cover - exercised only without the extra
            msg = "openai SDK not installed. Install the extra: pip install '.[openai]'"
            raise ProviderUnavailableError(msg) from exc
        self._client = AsyncOpenAI(api_key=api_key, timeout=self._timeout)
        return self._client

    @staticmethod
    def _image_part(image: ImageInput) -> dict[str, Any]:
        data, media_type = image.read_payload()
        encoded = base64.b64encode(data).decode("ascii")
        return {
            "type": "image_url",
            "image_url": {"url": f"data:{media_type};base64,{encoded}"},
        }

    async def aclose(self) -> None:
        """Release this run's uploads before closing the SDK client."""
        if self._client is not None:
            await self._image_files.close(self._client)
            close = getattr(self._client, "close", None)
            if callable(close):
                await close()

    async def _file_response(
        self,
        client: Any,
        *,
        role: str,
        prompt: str,
        images: Sequence[ImageInput],
        response_model: type[ResponseT],
        metadata: Mapping[str, Any],
    ) -> ResponseT:
        responses = getattr(client, "responses", None)
        if responses is None or not callable(getattr(responses, "create", None)):
            raise ProviderUnavailableError(
                "OpenAI file transport requires a current SDK with Responses API support; "
                "set OPENAI_IMAGE_TRANSPORT=inline for a Chat Completions endpoint"
            )
        prefix_chars = cache_prefix_of(metadata, prompt)
        content: list[dict[str, Any]] = []
        if prefix_chars:
            content.append({"type": "input_text", "text": prompt[:prefix_chars]})
        for image in images:
            file_id = await self._image_files.get(client, image)
            content.extend(
                [
                    {"type": "input_text", "text": f"Image ID: {image.image_id}"},
                    {"type": "input_image", "file_id": file_id, "detail": "auto"},
                ]
            )
        inputs: list[dict[str, Any]] = []
        if content:
            inputs.append({"role": "user", "content": content})
        if prompt[prefix_chars:]:
            inputs.append(
                {"role": "user", "content": [{"type": "input_text", "text": prompt[prefix_chars:]}]}
            )
        request: dict[str, Any] = {
            "model": self.model,
            "input": inputs,
            "store": False,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": response_model.__name__,
                    "schema": to_strict_openai_schema(response_model.model_json_schema()),
                    "strict": True,
                }
            },
        }
        if _accepts_metadata(client):
            request["metadata"] = {"node": str(metadata.get("node", "")), "logical_role": role}
        try:
            if self._budget_path:
                response = await capped_response(client, Path(self._budget_path), request)
            else:
                response = await responses.create(**request)
        except Exception as exc:
            if _is_content_policy_refusal(exc):
                raise _content_policy_error(exc, metadata) from exc
            raise
        sink = usage_sink_of(metadata)
        usage = getattr(response, "usage", None)
        if sink is not None and usage is not None:
            details = getattr(usage, "input_tokens_details", None)
            sink.record(
                input_tokens=getattr(usage, "input_tokens", None),
                cached_input_tokens=getattr(details, "cached_tokens", None),
                cache_write_input_tokens=getattr(details, "cache_write_tokens", None),
                output_tokens=getattr(usage, "output_tokens", None),
                reasoning_output_tokens=getattr(
                    getattr(usage, "output_tokens_details", None), "reasoning_tokens", None
                ),
            )
        if any(
            getattr(part, "type", None) == "refusal"
            for item in getattr(response, "output", ()) or ()
            for part in getattr(item, "content", ()) or ()
        ):
            raise ContentPolicyError(
                "openai refused to produce an answer on content-policy grounds"
            )
        if getattr(response, "status", None) == "incomplete":
            reason = getattr(getattr(response, "incomplete_details", None), "reason", "unknown")
            if reason == "content_filter":
                raise ContentPolicyError("openai stopped output because of its content filter")
            raise StructuredOutputError(f"openai returned incomplete output: {reason}")
        try:
            return response_model.model_validate_json(response.output_text)
        except ValidationError as exc:
            msg = f"openai returned unparseable output for {response_model.__name__}: {exc}"
            raise StructuredOutputError(msg) from exc

    async def generate_structured(
        self,
        *,
        role: str,
        prompt: str,
        images: Sequence[ImageInput],
        response_model: type[ResponseT],
        metadata: Mapping[str, Any],
    ) -> ResponseT:
        client = self._ensure_client()
        if self._image_mode == "files":
            return await self._file_response(
                client,
                role=role,
                prompt=prompt,
                images=images,
                response_model=response_model,
                metadata=metadata,
            )
        prefix_chars = cache_prefix_of(metadata, prompt)
        content: list[dict[str, Any]] = []
        if prefix_chars:
            content.append({"type": "text", "text": prompt[:prefix_chars]})
        for image in images:
            content.append({"type": "text", "text": f"Image ID: {image.image_id}"})
            content.append(self._image_part(image))
        # A separate stable message leaves a reusable cache boundary before the node's
        # changing instructions. Image bytes used to follow those instructions, so their
        # prefix differed on every node. Output-schema changes can still prevent a hit.
        messages: list[dict[str, Any]] = []
        if content:
            messages.append({"role": "user", "content": content})
        if prompt[prefix_chars:]:
            messages.append(
                {"role": "user", "content": [{"type": "text", "text": prompt[prefix_chars:]}]}
            )

        request: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": response_model.__name__,
                    "schema": to_strict_openai_schema(response_model.model_json_schema()),
                    "strict": True,
                },
            },
        }
        if _accepts_metadata(client):
            request["metadata"] = {"node": str(metadata.get("node", "")), "logical_role": role}
        try:
            response = await client.chat.completions.create(**request)
        except Exception as exc:
            if _is_content_policy_refusal(exc):
                raise _content_policy_error(exc, metadata) from exc
            raise
        sink = usage_sink_of(metadata)
        reported = getattr(response, "usage", None)
        if sink is not None and reported is not None:
            details = getattr(reported, "prompt_tokens_details", None)
            sink.record(
                input_tokens=getattr(reported, "prompt_tokens", None),
                cached_input_tokens=getattr(details, "cached_tokens", None),
                output_tokens=getattr(reported, "completion_tokens", None),
                reasoning_output_tokens=getattr(
                    getattr(reported, "completion_tokens_details", None), "reasoning_tokens", None
                ),
            )
        choice = response.choices[0]
        if getattr(choice, "finish_reason", None) == "content_filter" or getattr(
            choice.message, "refusal", None
        ):
            raise ContentPolicyError(
                "openai refused to produce an answer on content-policy grounds"
            )
        raw = choice.message.content or ""
        try:
            return response_model.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError) as exc:
            msg = f"openai returned unparseable output for {response_model.__name__}: {exc}"
            raise StructuredOutputError(msg) from exc
