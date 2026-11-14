"""Run-scoped native SDK image files; references save bytes, not image tokens."""

from __future__ import annotations

import asyncio
import hashlib
import os
import time
from collections.abc import Callable
from typing import Any, Literal

from dendro_inspector.observability.logging import get_logger
from dendro_inspector.providers.base import ImageInput, ProviderUnavailableError

ImageTransport = Literal["files", "inline"]
FILE_TTL_SECONDS = 3600
# Refresh before expiry so a later node does not start with a nearly expired reference.
_REUSE_SECONDS = FILE_TTL_SECONDS - 300
_CLEANUP_TIMEOUT_SECONDS = 15


def image_transport(env_name: str, configured: ImageTransport | None) -> ImageTransport:
    value = configured or os.environ.get(env_name, "").strip().lower() or "files"
    if value == "files":
        return "files"
    if value == "inline":
        return "inline"
    raise ProviderUnavailableError(f"{env_name} must be 'files' or 'inline'")


class SDKImageFiles:
    """Share uploads only within one run and credential scope, including parallel reviews.

    Only prepared bytes are uploaded, under a generic filename. Every upload requests an
    expiry as a crash/network-failure fallback. Normal teardown explicitly deletes files.
    No original paths, credentials, image bytes or remote IDs are persisted by this cache.
    """

    def __init__(
        self,
        provider: Literal["openai", "anthropic"],
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._provider = provider
        self._clock = clock
        self._entries: dict[tuple[str, str], tuple[str, float]] = {}
        self._uploaded_ids: set[str] = set()
        self._lock = asyncio.Lock()
        self._closed = False

    async def get(self, client: Any, image: ImageInput) -> str:
        data, media_type = image.read_payload()
        key = (hashlib.sha256(data).hexdigest(), media_type)
        async with self._lock:
            if self._closed:
                raise ProviderUnavailableError("image upload cache has already been closed")
            cached = self._entries.get(key)
            if cached is not None and self._clock() < cached[1]:
                return cached[0]
            files = getattr(client, "files", None)
            method = "create" if self._provider == "openai" else "upload"
            if files is None or not callable(getattr(files, method, None)):
                raise ProviderUnavailableError(
                    f"{self._provider} file transport requires a current SDK with Files API "
                    "and expiry support; select inline image transport for older clients"
                )
            # MIME was inferred from prepared bytes, never from an operator's filename.
            upload = (f"image.{media_type.split('/', 1)[1]}", data, media_type)
            created_at = self._clock()
            try:
                if self._provider == "openai":
                    uploaded = await files.create(
                        file=upload,
                        purpose="vision",
                        expires_after={"anchor": "created_at", "seconds": FILE_TTL_SECONDS},
                    )
                else:
                    uploaded = await files.upload(file=upload, expires_in_seconds=FILE_TTL_SECONDS)
            except TypeError as exc:
                raise ProviderUnavailableError(
                    f"{self._provider} file transport requires an SDK with expiry support; "
                    "select inline image transport for older clients"
                ) from exc
            file_id = getattr(uploaded, "id", None)
            if not isinstance(file_id, str) or not file_id:
                raise ProviderUnavailableError("image upload returned no file identifier")
            self._uploaded_ids.add(file_id)
            self._entries[key] = (file_id, created_at + _REUSE_SECONDS)
            return file_id

    async def close(self, client: Any) -> None:
        """Attempt every deletion without hiding the inspection's result or original error."""
        async with self._lock:
            self._closed = True
            try:
                async with asyncio.timeout(_CLEANUP_TIMEOUT_SECONDS):
                    for file_id in tuple(self._uploaded_ids):
                        try:
                            await client.files.delete(file_id)
                        except Exception as exc:
                            if getattr(exc, "status_code", None) != 404:
                                get_logger("providers").warning(
                                    "image_file_cleanup_failed", extra={"provider": self._provider}
                                )
                                continue
                        self._uploaded_ids.remove(file_id)
            except TimeoutError:
                get_logger("providers").warning(
                    "image_file_cleanup_timed_out", extra={"provider": self._provider}
                )
            self._entries.clear()
