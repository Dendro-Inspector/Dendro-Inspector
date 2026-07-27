"""Ollama adapter — integration boundary for a locally hosted model.

No SDK, no extra dependency: Ollama exposes a plain HTTP API on the local machine, so this
adapter talks to it with the standard library, in an executor thread since ``urllib`` is
blocking. Same scope discipline as the vendor adapters — enough to be a real, runnable seam.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import ValidationError

from dendro_inspector.providers.base import (
    ImageInput,
    ProviderUnavailableError,
    ResponseT,
    StructuredOutputError,
)
from dendro_inspector.providers.schema_compat import to_ollama_schema

# Vision is not optional here — every node call carries images. `gemma4:e4b` is the
# library default tag, multimodal, and edge-sized. A text-only model silently ignores the
# `images` field rather than failing, which would look like a bad identification.
DEFAULT_MODEL = "gemma4:e4b"
DEFAULT_HOST = "http://localhost:11434"

# Local inference is not slower than a hosted API by a small margin — it is slower by an
# order of magnitude, and the first call to a cold model pays the load as well. A 23 GB
# model exceeded 120s before emitting anything. Overriding is a per-machine decision, so it
# reads the environment.
DEFAULT_TIMEOUT_SECONDS = 900.0


def _default_timeout() -> float:
    raw = os.environ.get("OLLAMA_TIMEOUT_SECONDS")
    if not raw:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        return float(raw)
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS


class OllamaProvider:
    """Structured-output adapter for a local Ollama server."""

    adapter_name = "ollama"

    def __init__(
        self,
        *,
        model: str | None = None,
        host_env: str = "OLLAMA_HOST",
        timeout_seconds: float | None = None,
    ) -> None:
        self.model = model or DEFAULT_MODEL
        self._host_env = host_env
        self._timeout = timeout_seconds if timeout_seconds is not None else _default_timeout()

    def _host(self) -> str:
        return (os.environ.get(self._host_env) or DEFAULT_HOST).rstrip("/")

    @staticmethod
    def _image_payload(image: ImageInput) -> str:
        return base64.b64encode(image.read_bytes()).decode("ascii")

    def _call(self, *, prompt: str, images: Sequence[ImageInput], schema: dict[str, Any]) -> str:
        message: dict[str, Any] = {"role": "user", "content": prompt}
        if images:
            message["images"] = [self._image_payload(image) for image in images]
        body = json.dumps(
            {
                "model": self.model,
                "messages": [message],
                "format": schema,
                "stream": False,
                # Ollama's own guidance for schema adherence, and the right default here:
                # sampling variance in an adapter would show up downstream as an unstable
                # identification, which is exactly the thing this project refuses to ship.
                "options": {"temperature": 0},
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self._host()}/api/chat",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # Caught before URLError, which it subclasses: the server answered, so telling
            # the operator to start it would send them the wrong way. A 404 here almost
            # always means the model was never pulled.
            detail = exc.read().decode("utf-8", errors="replace").strip()
            msg = (
                f"Ollama at {self._host()} rejected the request "
                f"({exc.code} {exc.reason}): {detail or 'no detail'}. "
                f"Check that the model is pulled: `ollama pull {self.model}`."
            )
            raise ProviderUnavailableError(msg) from exc
        except urllib.error.URLError as exc:
            msg = (
                f"could not reach Ollama at {self._host()}: {exc.reason}. "
                "Start it with `ollama serve`, or run with the fake adapter "
                "(DENDRO_PRIMARY_PROVIDER=fake)."
            )
            raise ProviderUnavailableError(msg) from exc
        except TimeoutError as exc:
            msg = f"Ollama at {self._host()} did not respond within {self._timeout}s"
            raise ProviderUnavailableError(msg) from exc

        try:
            content: str = payload["message"]["content"]
        except (KeyError, TypeError) as exc:
            msg = f"Ollama response had no message content: {payload!r}"
            raise StructuredOutputError(msg) from exc
        return content

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
        schema = to_ollama_schema(response_model.model_json_schema())
        raw = await asyncio.to_thread(self._call, prompt=prompt, images=images, schema=schema)
        try:
            return response_model.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError) as exc:
            msg = (
                f"ollama returned unparseable output for {response_model.__name__} "
                f"(node={metadata.get('node', '?')}): {exc}"
            )
            raise StructuredOutputError(msg) from exc
