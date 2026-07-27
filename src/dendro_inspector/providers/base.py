"""Model-provider boundary.

Nodes depend on this Protocol, never on a vendor SDK type. Two failure classes are kept
strictly apart, because conflating them is how an outage becomes a scientific claim:

* :class:`ProviderError` — the model or transport failed. Not evidence about a tree.
* a valid response that says "insufficient evidence" — a scientific result. Not a failure.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from dendro_inspector.observability.events import ProviderCallRecord
from dendro_inspector.observability.trace import TraceRecorder

ResponseT = TypeVar("ResponseT", bound=BaseModel)


class ProviderError(RuntimeError):
    """Transport, authentication, quota or protocol failure."""


class ProviderUnavailableError(ProviderError):
    """The adapter cannot run at all — missing SDK, missing credential, bad config."""


class StructuredOutputError(ProviderError):
    """The model returned output that does not satisfy the requested contract."""


@dataclass(frozen=True, slots=True)
class ImageInput:
    """An image handed to a provider. Bytes are read at the adapter boundary only."""

    image_id: str
    path: Path
    media_type: str = "image/jpeg"

    def read_bytes(self) -> bytes:
        return self.path.read_bytes()


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
) -> ResponseT:
    """Call a provider for structured output, repairing malformed output at most once.

    The retry budget here is separate from the graph's retry budget: this one fixes
    *protocol* failures, the graph's fixes *scientific* ones. Exhausting this one raises
    rather than degrading a result, so a broken model never masquerades as an uncertain
    tree.
    """
    call_metadata: dict[str, Any] = {"node": node, **(metadata or {})}
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
