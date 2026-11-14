"""Model-provider boundary.

Nodes depend on this Protocol, never on a vendor SDK type. Two failure classes are kept
strictly apart, because conflating them is how an outage becomes a scientific claim:

* :class:`ProviderError` — the model or transport failed. Not evidence about a tree.
* a valid response that says "insufficient evidence" — a scientific result. Not a failure.
"""

from __future__ import annotations

import io
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
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

#: Call-metadata key carrying the code-owned evidence identifiers a model may point at.
#: The mirror of :data:`OUTPUT_SUBJECT_IDS` for the other identifier space a proposal
#: references. Advisory in the same way: an adapter that can bind it natively cannot then
#: emit a reference to nothing, and one that cannot leaves the work to adjudication.
OUTPUT_EVIDENCE_IDS = "output_evidence_ids"

#: Call-metadata key carrying a mutable sink for provider-reported token accounting.
#: Advisory like the two above: an adapter that does not fill it costs a missing number in
#: the trace and nothing else, and nothing an adapter writes here can change what is sent.
USAGE_SINK = "usage_sink"


def _accumulate(current: int | None, addition: int | None) -> int | None:
    if addition is None:
        return current
    return addition if current is None else current + addition


def _accumulate_cost(current: float | None, addition: float | None) -> float | None:
    if addition is None:
        return current
    return addition if current is None else current + addition


@dataclass(slots=True)
class UsageSink:
    """One logical call's token accounting, written by an adapter and read into the trace.

    A mutable sink rather than a return value, because ``generate_structured`` returns the
    validated contract and nothing else. Widening that signature to carry telemetry would
    put an accounting concern inside the one interface business logic is allowed to know
    about.

    ``None`` means the provider reported nothing, which is different from zero. Every field
    stays ``None`` until some attempt reports it.
    """

    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    cache_write_input_tokens: int | None = None
    output_tokens: int | None = None
    #: The share of ``output_tokens`` spent on reasoning the caller never sees. Kept beside
    #: the total rather than instead of it: billing wants one number, and the question of
    #: which node is expensive wants the other. Providers that hide nothing report ``None``.
    reasoning_output_tokens: int | None = None
    reported_cost_usd: float | None = None

    def record(
        self,
        *,
        input_tokens: int | None = None,
        cached_input_tokens: int | None = None,
        cache_write_input_tokens: int | None = None,
        output_tokens: int | None = None,
        reasoning_output_tokens: int | None = None,
        reported_cost_usd: float | None = None,
    ) -> None:
        """Add one attempt's accounting to the call's running total.

        Attempts accumulate rather than overwrite. The ``duration_ms`` recorded beside these
        numbers already spans every attempt, so a repaired call really did generate both
        responses: reporting only the one that validated would understate the spend and
        would break the relationship between output length and elapsed time.
        """
        self.input_tokens = _accumulate(self.input_tokens, input_tokens)
        self.cached_input_tokens = _accumulate(self.cached_input_tokens, cached_input_tokens)
        self.cache_write_input_tokens = _accumulate(
            self.cache_write_input_tokens, cache_write_input_tokens
        )
        self.output_tokens = _accumulate(self.output_tokens, output_tokens)
        self.reasoning_output_tokens = _accumulate(
            self.reasoning_output_tokens, reasoning_output_tokens
        )
        self.reported_cost_usd = _accumulate_cost(self.reported_cost_usd, reported_cost_usd)


def usage_sink_of(metadata: Mapping[str, Any]) -> UsageSink | None:
    """Read the call's usage sink, or ``None`` when the caller supplied none."""
    sink = metadata.get(USAGE_SINK)
    return sink if isinstance(sink, UsageSink) else None


def record_anthropic_usage(usage: Any, sink: UsageSink | None) -> None:
    """Normalize Claude's disjoint input counters into a total and its cache subsets."""
    if sink is None or usage is None:
        return
    uncached = getattr(usage, "input_tokens", None)
    reads = getattr(usage, "cache_read_input_tokens", None)
    writes = getattr(usage, "cache_creation_input_tokens", None)
    counts = [count for count in (uncached, reads, writes) if count is not None]
    sink.record(
        input_tokens=sum(counts) if counts else None,
        cached_input_tokens=reads,
        cache_write_input_tokens=writes,
        output_tokens=getattr(usage, "output_tokens", None),
    )


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


class ImageTooLargeError(ProviderError):
    """An image file exceeds the size any provider request may carry."""


class InvalidImageError(ProviderError):
    """An SDK image input is not a supported raster image."""


def _image_media_type(header: bytes) -> str:
    """Identify SDK-supported formats independently of filename and caller MIME hints."""
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if header.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return "image/webp"
    raise InvalidImageError("SDK image inputs must contain JPEG, PNG, GIF or WebP image bytes")


#: Formats worth re-encoding. Anything else is passed through untouched rather than
#: guessed at — a bounded edge is an optimisation, never a reason to corrupt an input.
_RESIZABLE_MEDIA_TYPES: dict[str, str] = {"image/jpeg": "JPEG", "image/png": "PNG"}

_JPEG_QUALITY = 88

#: Hard ceiling on a file read for transmission, applied with or without Pillow and before
#: any decoding. Generous for a photograph; it exists so that a path to something that is
#: not one is refused instead of being read into memory and uploaded once per node.
MAX_IMAGE_FILE_BYTES = 32 * 1024 * 1024

#: Embedded metadata a vision model does not need and a third party should not receive:
#: EXIF (GPS position, device serials, owner names), XMP, IPTC and free-text comments.
_METADATA_INFO_KEYS = frozenset({"exif", "xmp", "XML:com.adobe.xmp", "photoshop", "comment"})


def _read_bounded(path: Path) -> bytes:
    """Read at most the ceiling, so a file that grows after a size check is still refused."""
    with path.open("rb") as handle:
        data = handle.read(MAX_IMAGE_FILE_BYTES + 1)
    if len(data) > MAX_IMAGE_FILE_BYTES:
        msg = f"image {path.name!r} exceeds {MAX_IMAGE_FILE_BYTES} bytes; refusing to send it"
        raise ImageTooLargeError(msg)
    return data


def _carries_metadata(image: Any) -> bool:
    """Embedded metadata, PNG text chunks, or extra frames (MPO depth maps, secondary shots)."""
    return (
        bool(_METADATA_INFO_KEYS & image.info.keys())
        or bool(getattr(image, "text", None))
        or getattr(image, "n_frames", 1) > 1
    )


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
                "Pillow is not installed; sending originals, embedded metadata included. "
                "Install the 'images' extra to bound the transmitted size and strip it."
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
        return _read_bounded(path)

    original = _read_bounded(path)
    with Image.open(io.BytesIO(original)) as opened:
        # Re-encoding drops EXIF, so the rotation flag has to be baked into the pixels
        # first. Phone cameras store a portrait photograph as landscape plus an
        # orientation tag; dropping the tag without applying it hands the model a tree
        # lying on its side. Every photograph in this project's golden set is tagged.
        upright = ImageOps.exif_transpose(opened)
        if (
            max(upright.size) <= max_edge_px
            and upright.size == opened.size
            and not _carries_metadata(opened)
        ):
            # Already small enough, already upright, and carrying nothing but pixels.
            # Returning the original avoids a pointless second generation of JPEG loss on
            # evidence the model is asked to read closely. A small photograph that still
            # carries EXIF or a comment is re-encoded like a large one: its GPS position
            # and device identifiers are not evidence.
            return original
        upright.thumbnail((max_edge_px, max_edge_px), Image.Resampling.LANCZOS)
        prepared = upright.convert("RGB") if pillow_format == "JPEG" else upright
        # Pillow writes a JPEG comment back by default; EXIF and XMP only when asked.
        for key in _METADATA_INFO_KEYS:
            prepared.info.pop(key, None)
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

    def read_payload(self) -> tuple[bytes, str]:
        """Prepare the actual format and return bytes with their matching MIME type."""
        with self.path.open("rb") as handle:
            media_type = _image_media_type(handle.read(12))
        data = replace(self, media_type=media_type).read_bytes()
        return data, _image_media_type(data[:12])

    def read_bytes(self) -> bytes:
        pillow_format = _RESIZABLE_MEDIA_TYPES.get(self.media_type)
        if self.max_edge_px is None or pillow_format is None:
            return _read_bounded(self.path)
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

_REPAIR_RULES: dict[str, str] = {
    "missing": "this required field is missing",
    "extra_forbidden": "this field is not in the schema; remove it",
    "string_type": "must be a string",
    "int_type": "must be an integer",
    "bool_type": "must be true or false",
    "json_invalid": "the response was not valid JSON",
}

#: Pydantic error types that mean "the shape is wrong" rather than "the content broke a
#: rule about evidence". Both are retried; the distinction is reported so a reader can
#: tell a formatting slip from a model contradicting the evidence contract.
_FORMAT_ERROR_TYPES = frozenset(
    {
        "string_pattern_mismatch",
        "string_too_long",
        "string_too_short",
        "enum",
        "literal_error",
        "missing",
        "extra_forbidden",
        "string_type",
        "int_type",
        "bool_type",
        "json_invalid",
        "too_long",
        "too_short",
    }
)

#: At most this many errors are spelled out to the model; the rest are counted.
_MAX_REPAIR_ITEMS = 20
#: At most this many error codes are kept on one call record, over all its attempts.
_MAX_RECORDED_ERRORS = 40


def _validation_error_in(exc: BaseException | None) -> ValidationError | None:
    """The Pydantic error behind an adapter's wrapper, if there is one."""
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        if isinstance(exc, ValidationError):
            return exc
        seen.add(id(exc))
        exc = exc.__cause__ or exc.__context__
    return None


def _pattern_rule(pattern: object) -> str:
    from dendro_inspector.schemas.base import (
        FEATURE_PATH_PATTERN,
        FEATURE_PATH_RULE,
        IDENTIFIER_PATTERN,
        VALUE_TOKEN_PATTERN,
        VALUE_TOKEN_RULE,
    )

    if pattern == VALUE_TOKEN_PATTERN:
        return VALUE_TOKEN_RULE
    if pattern == FEATURE_PATH_PATTERN:
        return FEATURE_PATH_RULE
    if pattern == IDENTIFIER_PATTERN:
        return "A lowercase identifier: letters, digits, `_` or `-`, e.g. `obs-1`, `img-1`."
    return f"must match the pattern `{pattern}`"


def _format_only(codes: tuple[str, ...]) -> bool:
    """Whether every validation error in one attempt was a formatting error."""
    return bool(codes) and all(
        not code.startswith("unparseable:") and code.rsplit(":", 1)[-1] in _FORMAT_ERROR_TYPES
        for code in codes
    )


def repair_feedback(exc: BaseException) -> tuple[str, tuple[str, ...]]:
    """Targeted repair text for the model, and stable codes for the call record.

    The raw Pydantic message names a regex, and a model that wrote a sentence where a token
    belongs mostly writes the sentence again (golden-003, 046 and 048 each failed twice
    this way). Naming the field, the value it sent, the rule in words and an example is
    what a second attempt can act on. Nothing here edits the response: the model repairs
    its own output, and anything it cannot fill honestly it is told to leave out.
    """
    error = _validation_error_in(exc)
    if error is None:
        text = str(exc)[:2000]
        return text, (f"unparseable:{type(exc).__name__}",)

    lines: list[str] = []
    codes: list[str] = []
    details = error.errors(include_url=False)
    for detail in details[:_MAX_REPAIR_ITEMS]:
        location = ".".join(str(part) for part in detail["loc"]) or "<root>"
        kind = detail["type"]
        context = detail.get("ctx") or {}
        received = detail.get("input")
        shown = repr(received)[:100] if isinstance(received, str | int | float | bool) else ""
        if kind == "string_pattern_mismatch":
            rule = _pattern_rule(context.get("pattern"))
        elif kind in ("enum", "literal_error"):
            rule = f"allowed values: {context.get('expected', detail['msg'])}"
        elif kind == "string_too_long":
            rule = f"at most {context.get('max_length', '?')} characters"
            if isinstance(received, str):
                # A model told only the limit trims a little and overruns again; the
                # size of the overrun is what tells it how much to cut.
                rule += f", you sent {len(received)}: shorten it, do not split it"
        else:
            rule = _REPAIR_RULES.get(kind, str(detail["msg"]))
        category = "format" if kind in _FORMAT_ERROR_TYPES else "semantic"
        got = f" got {shown};" if shown else ""
        lines.append(f"- `{location}` [{category}]{got} {rule}")
        codes.append(f"{location}:{kind}")
    if len(details) > _MAX_REPAIR_ITEMS:
        lines.append(f"- … and {len(details) - _MAX_REPAIR_ITEMS} more of the same kinds.")

    text = (
        f"{len(details)} field(s) failed validation:\n"
        + "\n".join(lines)
        + "\n\nFix only these fields and keep every other field as you returned it. "
        "Token fields take one lowercase token, never a sentence. If a value cannot be "
        "stated honestly as a token, leave the field out or the list empty — do not "
        "invent an observation, a value or an id to satisfy the schema."
    )
    return text, tuple(codes)


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
    validate_response: Callable[[ResponseT], None] | None = None,
) -> ResponseT:
    """Call a provider for structured output, repairing malformed output.

    ``max_retries`` repairs are allowed, plus at most one more when every remaining error
    is a *formatting* error (a sentence in a token field, a missing required key). That
    attempt is one call with targeted feedback; failing here instead costs the whole case
    again — golden-003 ran three full case attempts that each died on the same token.
    Semantic errors, refusals and truncation get no extra attempt.

    The retry budget here is separate from the graph's retry budget: this one fixes
    *protocol* failures, the graph's fixes *scientific* ones. Exhausting this one raises
    rather than degrading a result, so a broken model never masquerades as an uncertain
    tree.

    ``cache_prefix_chars`` is advisory and reaches adapters through call metadata: how much
    of the leading prompt is byte-identical across every node in the case.
    """
    usage = UsageSink()
    call_metadata: dict[str, Any] = {
        "node": node,
        CACHE_PREFIX_CHARS: cache_prefix_chars,
        USAGE_SINK: usage,
        **(metadata or {}),
    }
    attempt_prompt = prompt
    validation_failures = 0
    validation_errors: list[str] = []
    started = time.perf_counter()
    last_error: Exception | None = None
    allowed = max_retries + 1
    extended = False

    attempt = 0
    while attempt < allowed:
        try:
            result = await provider.generate_structured(
                role=role,
                prompt=attempt_prompt,
                images=images,
                response_model=response_model,
                metadata=call_metadata,
            )
            if validate_response is not None:
                validate_response(result)
        except (ValidationError, StructuredOutputError) as exc:
            validation_failures += 1
            last_error = exc
            feedback, codes = repair_feedback(exc)
            validation_errors.extend(f"attempt-{attempt + 1}:{code}" for code in codes)
            attempt_prompt = prompt + _REPAIR_INSTRUCTION + feedback
            attempt += 1
            if attempt == allowed and max_retries > 0 and not extended and _format_only(codes):
                allowed += 1
                extended = True
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
                    input_tokens=usage.input_tokens,
                    cached_input_tokens=usage.cached_input_tokens,
                    cache_write_input_tokens=usage.cache_write_input_tokens,
                    output_tokens=usage.output_tokens,
                    reasoning_output_tokens=usage.reasoning_output_tokens,
                    reported_cost_usd=usage.reported_cost_usd,
                    validation_errors=tuple(validation_errors[:_MAX_RECORDED_ERRORS]),
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
                attempts=attempt,
                validation_failures=validation_failures,
                duration_ms=(time.perf_counter() - started) * 1000.0,
                input_tokens=usage.input_tokens,
                cached_input_tokens=usage.cached_input_tokens,
                cache_write_input_tokens=usage.cache_write_input_tokens,
                output_tokens=usage.output_tokens,
                reasoning_output_tokens=usage.reasoning_output_tokens,
                reported_cost_usd=usage.reported_cost_usd,
                validation_errors=tuple(validation_errors[:_MAX_RECORDED_ERRORS]),
            )
        )
    msg = (
        f"{provider.adapter_name} returned unusable structured output for node {node!r} "
        f"after {attempt} attempt(s): {last_error}"
    )
    raise StructuredOutputError(msg) from last_error
