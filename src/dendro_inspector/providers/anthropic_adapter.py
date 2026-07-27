"""Anthropic adapter — integration boundary only.

Same scope and lazy-import discipline as the OpenAI adapter. Typically bound to the
``arbiter`` role so that the challenge comes from a genuinely independent model rather
than the same model grading its own homework.
"""

from __future__ import annotations

import base64
import json
import os
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import ValidationError

from dendro_inspector.providers.base import (
    ImageInput,
    ProviderUnavailableError,
    ResponseT,
    StructuredOutputError,
)

DEFAULT_MODEL = "claude-opus-5"
MAX_TOKENS = 4096


class AnthropicProvider:
    """Structured-output adapter for Anthropic multimodal models."""

    adapter_name = "anthropic"

    def __init__(
        self,
        *,
        model: str | None = None,
        api_key_env: str = "ANTHROPIC_API_KEY",
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
                "(DENDRO_ARBITER_PROVIDER=fake)."
            )
            raise ProviderUnavailableError(msg)
        try:
            from anthropic import AsyncAnthropic
        except ImportError as exc:  # pragma: no cover - exercised only without the extra
            msg = "anthropic SDK not installed. Install the extra: pip install '.[anthropic]'"
            raise ProviderUnavailableError(msg) from exc
        self._client = AsyncAnthropic(api_key=api_key, timeout=self._timeout)
        return self._client

    @staticmethod
    def _image_block(image: ImageInput) -> dict[str, Any]:
        encoded = base64.b64encode(image.read_bytes()).decode("ascii")
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": image.media_type, "data": encoded},
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
        del role
        client = self._ensure_client()
        blocks: list[dict[str, Any]] = [self._image_block(image) for image in images]
        schema = json.dumps(response_model.model_json_schema(), ensure_ascii=False)
        blocks.append(
            {
                "type": "text",
                "text": (
                    f"{prompt}\n\n---\n\n## Required output\n\n"
                    f"Return a single JSON object conforming to this schema. "
                    f"No prose, no code fence.\n\n{schema}"
                ),
            }
        )

        response = await client.messages.create(
            model=self.model,
            max_tokens=MAX_TOKENS,
            messages=[{"role": "user", "content": blocks}],
        )
        raw = "".join(
            block.text for block in response.content if getattr(block, "type", "") == "text"
        )
        try:
            return response_model.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError) as exc:
            msg = (
                f"anthropic returned unparseable output for {response_model.__name__} "
                f"(node={metadata.get('node', '?')}): {exc}"
            )
            raise StructuredOutputError(msg) from exc
