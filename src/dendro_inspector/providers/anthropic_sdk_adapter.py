"""Anthropic Claude API adapter — direct SDK, no bridge.

A separate adapter from ``anthropic_adapter``, not a replacement for it. That one also
serves the agent-provider bridge (a Claude Code or Codex worker behind an
Anthropic-shaped socket), so it may only send what the bridge understands. This one talks
to the Claude API itself and uses what the API offers:

- native structured output (``output_config.format``), so the reply is schema-shaped JSON
  instead of prose the prompt asked to be JSON;
- an explicit effort level, since thinking cannot be switched off on current models and its
  default depth is below what a dendrology judgement deserves;
- server-side refusal fallback, so a safety decline is re-run on another model inside the
  same call instead of failing the case.

Like every adapter, it is an integration boundary only: the response is still validated by
the node's own Pydantic model, which re-imposes every constraint the schema dialect dropped.
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
from dendro_inspector.providers.schema_compat import to_anthropic_schema

DEFAULT_MODEL = "claude-opus-5-5"
# Non-streaming ceiling that stays inside the SDK's HTTP timeout. Thinking shares this
# budget with the answer, so it is set well above the size of any node's JSON.
MAX_TOKENS = 16000
DEFAULT_TIMEOUT_SECONDS = 600.0
DEFAULT_EFFORT = "high"
EFFORTS = ("low", "medium", "high", "xhigh", "max")
FALLBACK_BETA = "server-side-fallback-2026-07-01"


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _effort() -> str:
    effort = (os.environ.get("ANTHROPIC_EFFORT") or DEFAULT_EFFORT).strip().lower()
    return effort if effort in EFFORTS else DEFAULT_EFFORT


_Errors = tuple[type[BaseException], ...]


def _sdk_errors() -> tuple[_Errors, _Errors, _Errors]:
    """The SDK's credential, not-found and bad-request errors; nothing when it is absent.

    Rate limits, server errors and timeouts are deliberately left alone: the SDK retries
    them itself, and what still escapes is the transient failure the run loop retries.
    """
    try:
        import anthropic
    except ImportError:  # pragma: no cover - only a test double can get this far without it
        return (), (), ()
    return (
        (anthropic.AuthenticationError, anthropic.PermissionDeniedError),
        (anthropic.NotFoundError,),
        (anthropic.BadRequestError,),
    )


def _api_key(env_name: str) -> str | None:
    """The configured key, else the conventional ANTHROPIC_API_KEY — but never the bridge's.

    Bridge pods set ANTHROPIC_BASE_URL and a placeholder ANTHROPIC_API_KEY for the other
    adapter. A run mixing bridge roles with this adapter must not send that placeholder to
    the real API, so the fallback is taken only where no bridge is configured.
    """
    configured = os.environ.get(env_name)
    if configured:
        return configured
    if env_name != "ANTHROPIC_API_KEY" and not os.environ.get("ANTHROPIC_BASE_URL"):
        return os.environ.get("ANTHROPIC_API_KEY") or None
    return None


def _fallbacks_enabled() -> bool:
    return (os.environ.get("ANTHROPIC_FALLBACKS") or "default").strip().lower() != "off"


class AnthropicSDKProvider:
    """Structured-output adapter for the Claude API, called directly through the SDK."""

    adapter_name = "anthropic-sdk"

    def __init__(
        self,
        *,
        model: str | None = None,
        api_key_env: str = "ANTHROPIC_SDK_API_KEY",
        timeout_seconds: float | None = None,
        effort: str | None = None,
    ) -> None:
        self.model = model or DEFAULT_MODEL
        self._api_key_env = api_key_env
        self._timeout = (
            timeout_seconds
            if timeout_seconds is not None
            else _env_float("ANTHROPIC_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)
        )
        self._effort = effort if effort in EFFORTS else _effort()
        self._fallbacks = _fallbacks_enabled()
        self._client: Any | None = None
        # Contracts whose schema the API refused to compile: asked for in the prompt instead.
        self._prompt_only: set[str] = set()

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        api_key = _api_key(self._api_key_env)
        if not api_key:
            msg = (
                f"{self._api_key_env} is not set (nor ANTHROPIC_API_KEY outside a bridge pod). "
                "Export it, or run with the fake adapter (DENDRO_PRIMARY_PROVIDER=fake)."
            )
            raise ProviderUnavailableError(msg)
        try:
            from anthropic import AsyncAnthropic
        except ImportError as exc:  # pragma: no cover - exercised only without the extra
            msg = "anthropic SDK not installed. Install the extra: pip install '.[anthropic]'"
            raise ProviderUnavailableError(msg) from exc
        # base_url is pinned to the SDK default on purpose: an inherited ANTHROPIC_BASE_URL
        # (the bridge sets one for the other adapter) must not divert this one.
        self._client = AsyncAnthropic(
            api_key=api_key, timeout=self._timeout, base_url="https://api.anthropic.com"
        )
        return self._client

    @staticmethod
    def _image_block(image: ImageInput) -> dict[str, Any]:
        encoded = base64.b64encode(image.read_bytes()).decode("ascii")
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": image.media_type, "data": encoded},
        }

    def _request(
        self,
        prompt: str,
        images: Sequence[ImageInput],
        response_model: type[ResponseT],
        metadata: Mapping[str, Any],
        *,
        native: bool = True,
    ) -> dict[str, Any]:
        blocks: list[dict[str, Any]] = [self._image_block(image) for image in images]

        # Same cache placement as the bridge adapter: one breakpoint after the domain prompt
        # covers the photographs too, which repeat byte for byte across a case's calls.
        prefix_chars = cache_prefix_of(metadata, prompt)
        if prefix_chars:
            blocks.append(
                {
                    "type": "text",
                    "text": prompt[:prefix_chars],
                    "cache_control": {"type": "ephemeral"},
                }
            )

        output_config: dict[str, Any] = {"effort": self._effort}
        schema = to_anthropic_schema(response_model.model_json_schema()) if native else None
        tail = prompt[prefix_chars:]
        if schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        else:
            raw_schema = json.dumps(response_model.model_json_schema(), ensure_ascii=False)
            tail = (
                f"{tail}\n\n---\n\n## Required output\n\n"
                f"Return a single JSON object conforming to this schema. "
                f"No prose, no code fence.\n\n{raw_schema}"
            )
        blocks.append({"type": "text", "text": tail})

        request: dict[str, Any] = {
            "model": self.model,
            "max_tokens": MAX_TOKENS,
            "messages": [{"role": "user", "content": blocks}],
            "output_config": output_config,
        }
        if self._fallbacks:
            request["betas"] = [FALLBACK_BETA]
            request["fallbacks"] = "default"
        return request

    @staticmethod
    async def _send(client: Any, request: dict[str, Any]) -> Any:
        if "betas" in request:
            return await client.beta.messages.create(**request)
        return await client.messages.create(**request)

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
        name = response_model.__name__
        native = name not in self._prompt_only
        request = self._request(prompt, images, response_model, metadata, native=native)
        node = metadata.get("node", "?")

        auth_errors, missing_errors, bad_request = _sdk_errors()
        try:
            try:
                response = await self._send(client, request)
            except bad_request:
                # A schema the API cannot compile (too large, an unsupported construct) would
                # fail every call for this contract. Ask for it in the prompt instead, as the
                # bridge adapter always has; any other bad request fails again and surfaces.
                if "format" not in request["output_config"]:
                    raise
                self._prompt_only.add(name)
                request = self._request(prompt, images, response_model, metadata, native=False)
                response = await self._send(client, request)
        except auth_errors as exc:
            msg = f"Claude API rejected the credential in {self._api_key_env}: {exc}"
            raise ProviderUnavailableError(msg) from exc
        except missing_errors as exc:
            msg = f"Claude API does not know model {self.model!r}: {exc}"
            raise ProviderUnavailableError(msg) from exc

        sink = usage_sink_of(metadata)
        usage = getattr(response, "usage", None)
        if sink is not None and usage is not None:
            sink.record(
                input_tokens=getattr(usage, "input_tokens", None),
                cached_input_tokens=getattr(usage, "cache_read_input_tokens", None),
                output_tokens=getattr(usage, "output_tokens", None),
            )

        stop = getattr(response, "stop_reason", None)
        if stop == "refusal":
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None) if details is not None else None
            msg = (
                f"Claude declined {response_model.__name__} (node={node}, "
                f"category={category or 'unspecified'}); no fallback model answered."
            )
            raise StructuredOutputError(msg)
        if stop == "max_tokens":
            msg = (
                f"Claude ran out of output budget ({MAX_TOKENS} tokens) for "
                f"{response_model.__name__} (node={node}); the JSON is incomplete."
            )
            raise StructuredOutputError(msg)

        raw = "".join(
            block.text for block in response.content if getattr(block, "type", "") == "text"
        )
        try:
            return response_model.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError) as exc:
            msg = (
                f"Claude API returned unparseable output for {response_model.__name__} "
                f"(node={node}): {exc}"
            )
            raise StructuredOutputError(msg) from exc
