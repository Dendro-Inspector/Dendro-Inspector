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
from typing import Any

from pydantic import ValidationError

from evil_duck_dendro.providers.base import (
    ImageInput,
    ProviderUnavailableError,
    ResponseT,
    StructuredOutputError,
)

DEFAULT_MODEL = "gpt-5.6"


class OpenAIProvider:
    """Structured-output adapter for OpenAI-compatible multimodal chat models."""

    adapter_name = "openai"

    def __init__(
        self,
        *,
        model: str | None = None,
        api_key_env: str = "OPENAI_API_KEY",
        timeout_seconds: float = 120.0,
    ) -> None:
        self.model = model or DEFAULT_MODEL
        self._api_key_env = api_key_env
        self._timeout = timeout_seconds
        self._client: Any | None = None

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        api_key = os.environ.get(self._api_key_env)
        if not api_key:
            msg = (
                f"{self._api_key_env} is not set. Export it, or run with the fake adapter "
                "(EVIL_DUCK_PRIMARY_PROVIDER=fake)."
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
        encoded = base64.b64encode(image.read_bytes()).decode("ascii")
        return {
            "type": "image_url",
            "image_url": {"url": f"data:{image.media_type};base64,{encoded}"},
        }

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
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        content.extend(self._image_part(image) for image in images)

        response = await client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": content}],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": response_model.__name__,
                    "schema": response_model.model_json_schema(),
                    "strict": False,
                },
            },
            metadata={"node": str(metadata.get("node", "")), "logical_role": role},
        )
        raw = response.choices[0].message.content or ""
        try:
            return response_model.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError) as exc:
            msg = f"openai returned unparseable output for {response_model.__name__}: {exc}"
            raise StructuredOutputError(msg) from exc
