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
    cache_prefix_of,
    usage_sink_of,
)

DEFAULT_MODEL = "claude-opus-5"
MAX_TOKENS = 4096
DEFAULT_TIMEOUT_SECONDS = 120.0


def _default_timeout() -> float:
    """Same override the Gemini and Ollama adapters take, for the same reason.

    A hosted vendor answers inside two minutes or not at all, so the default stands. The
    override exists because `scripts/agent-provider/bridge.py` puts a human or an agent
    behind this socket, and neither writes a structured answer in 120 seconds.
    """
    raw = os.environ.get("ANTHROPIC_TIMEOUT_SECONDS")
    if not raw:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        return float(raw)
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS


class AnthropicProvider:
    """Structured-output adapter for Anthropic multimodal models."""

    adapter_name = "anthropic"

    def __init__(
        self,
        *,
        model: str | None = None,
        api_key_env: str = "ANTHROPIC_API_KEY",
        timeout_seconds: float | None = None,
    ) -> None:
        self.model = model or DEFAULT_MODEL
        self._api_key_env = api_key_env
        self._timeout = timeout_seconds if timeout_seconds is not None else _default_timeout()
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

        # A cache breakpoint covers everything before it, so placing one after the domain
        # prompt also covers the photographs — which are byte-identical across all seven
        # calls in a case, and the largest single payload in each of them.
        prefix_chars = cache_prefix_of(metadata, prompt)
        if prefix_chars:
            blocks.append(
                {
                    "type": "text",
                    "text": prompt[:prefix_chars],
                    "cache_control": {"type": "ephemeral"},
                }
            )

        blocks.append(
            {
                "type": "text",
                "text": (
                    f"{prompt[prefix_chars:]}\n\n---\n\n## Required output\n\n"
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
        sink = usage_sink_of(metadata)
        reported = getattr(response, "usage", None)
        if sink is not None and reported is not None:
            # `cache_read_input_tokens` is the half of the caching story worth having: it
            # says the breakpoint above actually hit, rather than that one was requested.
            sink.record(
                input_tokens=getattr(reported, "input_tokens", None),
                cached_input_tokens=getattr(reported, "cache_read_input_tokens", None),
                output_tokens=getattr(reported, "output_tokens", None),
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
