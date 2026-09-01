"""Model-provider boundary.

Nodes depend on this Protocol, never on a vendor SDK type. Two failure classes are kept
strictly apart, because conflating them is how an outage becomes a scientific claim:

* :class:`ProviderError` — the model or transport failed. Not evidence about a tree.
* a valid response that says "insufficient evidence" — a scientific result. Not a failure.
"""

from __future__ import annotations

import io
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from dendro_inspector.observability.events import ProviderCallRecord
from dendro_inspector.observability.logging import get_logger
from dendro_inspector.observability.trace import TraceRecorder

ResponseT = TypeVar("ResponseT", bound=BaseModel)

#: Call-metadata key carrying how many leading characters of ``prompt`` are identical
#: across every node in a case. An adapter whose provider supports explicit prompt caching
#: may mark a breakpoint there; one whose provider caches automatically, or not at all,
#: ignores it. Advisory in both directions: it never changes the text that is sent.
CACHE_PREFIX_CHARS = "cache_prefix_chars"

#: Call-metadata key carrying the code-owned subject identifiers a reviewer may return.
#: Adapters with native enum-constrained output can bind every ``subject_id`` field to
#: these exact values. Other adapters ignore it; Pydantic and review synthesis remain the
#: final contract and semantic boundaries.
OUTPUT_SUBJECT_IDS = "output_subject_ids"


def cache_prefix_of(metadata: Mapping[str, Any], prompt: str) -> int:
    """Read the advisory cache boundary, clamped to something the prompt can honour."""
    raw = metadata.get(CACHE_PREFIX_CHARS)
    if not isinstance(raw, int) or raw <= 0:
        return 0
    # A repair retry appends to the prompt, so the prefix stays valid; a caller that
    # reported a boundary past the end of a shortened prompt gets no caching, not a crash.
    return min(raw, len(prompt))


class ProviderError(RuntimeError):
    """Transport, authentication, quota or protocol failure."""


class ProviderUnavailableError(ProviderError):
    """The adapter cannot run at all — missing SDK, missing credential, bad config."""


class StructuredOutputError(ProviderError):
    """The model returned output that does not satisfy the requested contract."""


#: Formats worth re-encoding. Anything else is passed through untouched rather than
#: guessed at — a bounded edge is an optimisation, never a reason to corrupt an input.
_RESIZABLE_MEDIA_TYPES: dict[str, str] = {"image/jpeg": "JPEG", "image/png": "PNG"}

_JPEG_QUALITY = 88

_pillow_warning_issued = False


def _warn_pillow_missing_once(max_edge_px: int) -> None:
    global _pillow_warning_issued
    if _pillow_warning_issued:
        return
    _pillow_warning_issued = True
    get_logger("providers").warning(
        "image_downscale_unavailable",
        extra={
            "max_edge_px": max_edge_px,
            "detail": (
                "Pillow is not installed; sending originals. "
                "Install the 'images' extra to bound the transmitted size."
            ),
        },
    )


@lru_cache(maxsize=64)
def _bounded_bytes(
    path: Path,
    max_edge_px: int,
    pillow_format: str,
    # Part of the cache key only: a file edited in place must not serve stale bytes.
    mtime_ns: int,
    size: int,
) -> bytes:
    try:
        from PIL import Image, ImageOps
    except ImportError:
        _warn_pillow_missing_once(max_edge_px)
        return path.read_bytes()

    with Image.open(path) as opened:
        # Re-encoding drops EXIF, so the rotation flag has to be baked into the pixels
        # first. Phone cameras store a portrait photograph as landscape plus an
        # orientation tag; dropping the tag without applying it hands the model a tree
        # lying on its side. Every photograph in this project's golden set is tagged.
        upright = ImageOps.exif_transpose(opened)
        if max(upright.size) <= max_edge_px and upright.size == opened.size:
            # Already small enough and already upright. Returning the original avoids a
            # pointless second generation of JPEG loss on evidence the model is asked to
            # read closely.
            return path.read_bytes()
        upright.thumbnail((max_edge_px, max_edge_px), Image.Resampling.LANCZOS)
        prepared = upright.convert("RGB") if pillow_format == "JPEG" else upright
        buffer = io.BytesIO()
        save_options: dict[str, object] = (
            {"quality": _JPEG_QUALITY, "optimize": True} if pillow_format == "JPEG" else {}
        )
        prepared.save(buffer, format=pillow_format, **save_options)
    return buffer.getvalue()


@dataclass(frozen=True, slots=True)
class ImageInput:
    """An image handed to a provider. Bytes are read at the adapter boundary only.

    ``max_edge_px`` bounds the longest edge of what is actually transmitted. Every node in
    a case sends the same photograph, so an unbounded original is uploaded once per node —
    a nine-photo run measured 349 MB sent for 47 MB of distinct images. Vision models
    downsample server-side regardless, so the bytes above the bound buy nothing.
    """

    image_id: str
    path: Path
    media_type: str = "image/jpeg"
    max_edge_px: int | None = None

    def read_bytes(self) -> bytes:
        pillow_format = _RESIZABLE_MEDIA_TYPES.get(self.media_type)
        if self.max_edge_px is None or pillow_format is None:
            return self.path.read_bytes()
        stat = self.path.stat()
        return _bounded_bytes(
            self.path,
            self.max_edge_px,
            pillow_format,
            stat.st_mtime_ns,
            stat.st_size,
        )


class ModelProvider(Protocol):
    """The only model interface business logic is allowed to know about.

    ``adapter_name`` and ``model`` are read-only properties rather than mutable attributes
    so that an adapter may narrow them (``str`` where the protocol says ``str | None``).
    A mutable attribute would be invariant and reject every concrete adapter.
    """

    @property
    def adapter_name(self) -> str: ...

    @property
    def model(self) -> str | None: ...

    async def generate_structured(
        self,
        *,
        role: str,
        prompt: str,
        images: Sequence[ImageInput],
        response_model: type[ResponseT],
        metadata: Mapping[str, Any],
    ) -> ResponseT: ...


_REPAIR_INSTRUCTION = (
    "\n\n---\n\n## Output repair\n\n"
    "Your previous response did not validate against the required schema.\n"
    "Return a single JSON object that satisfies the schema exactly. "
    "No prose, no code fence, no commentary.\n"
    "Validation error:\n"
)


async def request_structured(
    *,
    provider: ModelProvider,
    role: str,
    node: str,
    prompt: str,
    images: Sequence[ImageInput],
    response_model: type[ResponseT],
    metadata: Mapping[str, Any] | None = None,
    recorder: TraceRecorder | None = None,
    max_retries: int = 1,
    cache_prefix_chars: int = 0,
) -> ResponseT:
    """Call a provider for structured output, repairing malformed output at most once.

    The retry budget here is separate from the graph's retry budget: this one fixes
    *protocol* failures, the graph's fixes *scientific* ones. Exhausting this one raises
    rather than degrading a result, so a broken model never masquerades as an uncertain
    tree.

    ``cache_prefix_chars`` is advisory and reaches adapters through call metadata: how much
    of the leading prompt is byte-identical across every node in the case.
    """
    call_metadata: dict[str, Any] = {
        "node": node,
        CACHE_PREFIX_CHARS: cache_prefix_chars,
        **(metadata or {}),
    }
    attempt_prompt = prompt
    validation_failures = 0
    started = time.perf_counter()
    last_error: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            result = await provider.generate_structured(
                role=role,
                prompt=attempt_prompt,
                images=images,
                response_model=response_model,
                metadata=call_metadata,
            )
        except (ValidationError, StructuredOutputError) as exc:
            validation_failures += 1
            last_error = exc
            attempt_prompt = prompt + _REPAIR_INSTRUCTION + str(exc)[:2000]
            continue

        if recorder is not None:
            recorder.record_provider_call(
                ProviderCallRecord(
                    role=role,
                    adapter=provider.adapter_name,
                    model=provider.model,
                    node=node,
                    response_model=response_model.__name__,
                    attempts=attempt + 1,
                    validation_failures=validation_failures,
                    duration_ms=(time.perf_counter() - started) * 1000.0,
                )
            )
        return result

    if recorder is not None:
        recorder.record_provider_call(
            ProviderCallRecord(
                role=role,
                adapter=provider.adapter_name,
                model=provider.model,
                node=node,
                response_model=response_model.__name__,
                attempts=max_retries + 1,
                validation_failures=validation_failures,
                duration_ms=(time.perf_counter() - started) * 1000.0,
            )
        )
    msg = (
        f"{provider.adapter_name} returned unusable structured output for node {node!r} "
        f"after {max_retries + 1} attempt(s): {last_error}"
    )
    raise StructuredOutputError(msg) from last_error
