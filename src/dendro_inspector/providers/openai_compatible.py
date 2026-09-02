"""Adapters for servers speaking the OpenAI chat-completions dialect.

Written against the *dialect*, not a vendor: NVIDIA NIM, OpenRouter, vLLM and a self-hosted
NIM differ only in base URL, credential and default model, so they are three class
attributes rather than three adapters. The standard library is enough — the ``openai`` SDK
is an optional extra this project does not require, and one more HTTP POST is not worth a
dependency (§14).

The model **must accept images**, because every node call here carries the photograph, and
a text-only model reports that badly. Measured 2026-07-27:

* `nvidia/nemotron-3-super-120b-a12b` answers `500 Received multimodal data but multimodal
  processing is not enabled`; the adapter rewrites that into an error naming the cause.
* OpenRouter advertises modality per model — `openai/gpt-oss-120b`,
  `nvidia/nemotron-3-nano-30b-a3b` and `nvidia/nemotron-3-super-120b-a12b` all report
  `input_modalities: ['text']` and cannot serve any role here.
* `google/gemma-4-31b-it` handles image plus `json_schema` cleanly on both hosts.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from pydantic import ValidationError

from dendro_inspector.providers.base import (
    ImageInput,
    ProviderError,
    ProviderUnavailableError,
    ResponseT,
    StructuredOutputError,
    UsageSink,
    usage_sink_of,
)
from dendro_inspector.providers.schema_compat import to_openai_schema

DEFAULT_TIMEOUT_SECONDS = 300.0
DEFAULT_MAX_TOKENS = 8192

# Hosted inference behind a shared endpoint returns transient failures that a second attempt
# clears: an `EngineCore encountered an issue` 500 was observed on a request that succeeded
# unchanged moments later.
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_RETRY_SLEEP_SECONDS = 4.0


class OpenAICompatibleProvider:
    """Structured-output adapter for any server speaking OpenAI chat-completions.

    Subclasses supply the four things that actually differ between hosts. Everything else —
    image encoding, schema translation, retry classification, response extraction — is the
    dialect, and is shared.
    """

    adapter_name = "openai-compatible"
    default_model = ""
    default_base_url = ""
    key_env = ""
    base_url_env = ""

    def _provider_preferences(self) -> Mapping[str, Any] | None:
        return None

    def _structured_request(
        self,
        *,
        prompt: str,
        schema: Mapping[str, Any],
        model_name: str,
    ) -> tuple[str, Mapping[str, Any]]:
        return prompt, {
            "type": "json_schema",
            "json_schema": {"name": model_name, "schema": schema, "strict": True},
        }

    def __init__(
        self,
        *,
        model: str | None = None,
        api_key_env: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        retries: int = 2,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.model = model or self.default_model
        self._api_key_env = api_key_env or self.key_env
        self._base_url = (
            base_url or os.environ.get(self.base_url_env) or self.default_base_url
        ).rstrip("/")
        self._timeout = timeout_seconds
        self._max_tokens = max_tokens
        self._retries = retries
        self._sleep = sleep

    def _api_key(self) -> str:
        key = os.environ.get(self._api_key_env)
        if not key:
            msg = (
                f"{self._api_key_env} is not set. Export it or put it in .env, or run with "
                "the fake adapter (DENDRO_PRIMARY_PROVIDER=fake)."
            )
            raise ProviderUnavailableError(msg)
        return key

    @staticmethod
    def _image_part(image: ImageInput) -> dict[str, Any]:
        encoded = base64.b64encode(image.read_bytes()).decode("ascii")
        return {
            "type": "image_url",
            "image_url": {"url": f"data:{image.media_type};base64,{encoded}"},
        }

    @staticmethod
    def _record_usage(payload: Mapping[str, Any], usage: UsageSink | None) -> None:
        """Read the OpenAI-dialect `usage` block, where the server sends one."""
        if usage is None:
            return
        reported = payload.get("usage")
        if not isinstance(reported, Mapping):
            return
        details = reported.get("prompt_tokens_details")
        cached = details.get("cached_tokens") if isinstance(details, Mapping) else None
        usage.record(
            input_tokens=reported.get("prompt_tokens"),
            cached_input_tokens=cached,
            output_tokens=reported.get("completion_tokens"),
        )

    def _call(
        self,
        *,
        prompt: str,
        images: Sequence[ImageInput],
        schema: Mapping[str, Any],
        model_name: str,
        usage: UsageSink | None = None,
    ) -> str:
        prompt, response_format = self._structured_request(
            prompt=prompt,
            schema=schema,
            model_name=model_name,
        )
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        content.extend(self._image_part(image) for image in images)
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": content}],
            "temperature": 0,
            "max_tokens": self._max_tokens,
            "stream": False,
            "response_format": response_format,
        }
        provider_preferences = self._provider_preferences()
        if provider_preferences is not None:
            payload["provider"] = dict(provider_preferences)
        body = json.dumps(payload).encode("utf-8")
        for attempt in range(self._retries + 1):
            request = urllib.request.Request(
                f"{self._base_url}/chat/completions",
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self._api_key()}",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=self._timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                # Caught before URLError, which it subclasses: the server answered.
                if exc.code in _RETRYABLE_STATUS and attempt < self._retries:
                    self._sleep(_RETRY_SLEEP_SECONDS)
                    continue
                raise self._http_error(exc) from exc
            except urllib.error.URLError as exc:
                msg = f"could not reach {self.adapter_name} at {self._base_url}: {exc.reason}"
                raise ProviderUnavailableError(msg) from exc
            except TimeoutError as exc:
                msg = f"{self.adapter_name} did not respond within {self._timeout}s"
                raise ProviderUnavailableError(msg) from exc

            self._record_usage(payload, usage)
            return self._extract_text(payload)

        msg = "unreachable: the retry loop always returns or raises"  # pragma: no cover
        raise ProviderError(msg)  # pragma: no cover

    def _http_error(self, exc: urllib.error.HTTPError) -> ProviderError:
        detail = exc.read().decode("utf-8", errors="replace")
        message = detail.strip() or "no detail"
        try:
            parsed = json.loads(detail)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            error = parsed.get("error")
            if isinstance(error, dict) and isinstance(error.get("message"), str):
                message = error["message"]
            elif isinstance(parsed.get("detail"), str):
                message = parsed["detail"]
        if exc.code in {401, 403}:
            return ProviderUnavailableError(
                f"{self.adapter_name} rejected the credential in {self._api_key_env} "
                f"({exc.code}): {message}"
            )
        if "multimodal" in message.lower():
            return ProviderUnavailableError(
                f"{self.model!r} does not accept images, and every node call here carries "
                f"one. Choose a vision model such as {self.default_model!r}. Server said: {message}"
            )
        return ProviderError(
            f"{self.adapter_name} request failed ({exc.code} {exc.reason}): {message}"
        )

    @staticmethod
    def _extract_text(payload: Mapping[str, Any]) -> str:
        choices = payload.get("choices") or []
        if not choices:
            msg = f"no choices in the response: {payload!r}"
            raise StructuredOutputError(msg)
        message = choices[0].get("message") or {}
        text = message.get("content")
        if not isinstance(text, str) or not text.strip():
            finish = choices[0].get("finish_reason", "unknown")
            msg = f"no content in the response (finish_reason={finish})"
            raise StructuredOutputError(msg)
        return text

    async def generate_structured(
        self,
        *,
        role: str,
        prompt: str,
        images: Sequence[ImageInput],
        response_model: type[ResponseT],
        metadata: Mapping[str, Any],
    ) -> ResponseT:
        del role
        schema = to_openai_schema(response_model.model_json_schema())
        raw = await asyncio.to_thread(
            self._call,
            prompt=prompt,
            images=images,
            schema=schema,
            model_name=response_model.__name__,
            usage=usage_sink_of(metadata),
        )
        try:
            return response_model.model_validate(json.loads(_strip_fence(raw)))
        except (json.JSONDecodeError, ValidationError) as exc:
            msg = (
                f"{self.adapter_name} returned unparseable output for {response_model.__name__} "
                f"(node={metadata.get('node', '?')}): {exc}"
            )
            raise StructuredOutputError(msg) from exc


def _strip_fence(raw: str) -> str:
    """Drop a ```json fence if the model wrapped its object in one.

    Constrained decoding is supposed to make this impossible, and mostly does. It costs two
    lines to tolerate the case where a server applies the schema loosely, and the alternative
    is burning the single repair retry on punctuation.
    """
    text = raw.strip()
    if not text.startswith("```"):
        return text
    body = text.split("\n", 1)[1] if "\n" in text else ""
    return body.rpartition("```")[0].strip() or text
