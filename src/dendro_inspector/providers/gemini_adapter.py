"""Gemini adapter — integration boundary only.

No SDK and no new dependency: the Generative Language API is plain HTTPS with a JSON body,
so this adapter uses the standard library, in an executor thread since ``urllib`` blocks.
Two hosts speak this protocol and they authenticate differently. The Generative Language
API takes an API key, which travels in the ``x-goog-api-key`` header rather than a query
parameter so it stays out of proxy logs and browser history. Vertex / Agent Platform takes
an OAuth access token as a bearer credential instead; that route is the one where a Cloud
billing account and its credits apply. The token is read from the environment rather than
minted here: refreshing it is the caller's job, and an SDK to do so would be a dependency
this adapter exists to avoid.

Structured output is requested natively via ``responseSchema``. That field speaks a narrower
dialect than Pydantic emits, so the schema goes through
:mod:`dendro_inspector.providers.schema_compat` first — a translation, not a relaxation:
the response is still validated by the original model.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from pydantic import ValidationError

from dendro_inspector.observability.logging import get_logger
from dendro_inspector.providers.base import (
    OUTPUT_SUBJECT_IDS,
    ImageInput,
    ProviderError,
    ProviderUnavailableError,
    ResponseT,
    StructuredOutputError,
    UsageSink,
    usage_sink_of,
)
from dendro_inspector.providers.schema_compat import to_gemini_schema

DEFAULT_MODEL = "gemini-3.6-flash"
DEFAULT_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_ACCESS_TOKEN_ENV = "GOOGLE_ACCESS_TOKEN"

# Vertex rejects ``x-goog-api-key`` with a 401 that reads like a bad key. Recognising the
# host lets a missing bearer token be reported as the configuration error it is.
_VERTEX_HOST = "aiplatform.googleapis.com"

_MAX_RETRY_SLEEP = 60.0
_DEFAULT_RETRY_SLEEP = 5.0
# 429: rate limit (but not an exhausted quota). 503: the API's own "temporary" overload.
_RETRYABLE_STATUS = frozenset({429, 503})

# A phone photograph is several megabytes, and it is uploaded inline on every node call.
# 120s covers a text prompt and not much else: the first real image put through this adapter
# timed out at 120s and completed comfortably under 300s.
DEFAULT_TIMEOUT_SECONDS = 300.0


def _bind_subject_id_enums(schema: Any, allowed: tuple[str, ...]) -> Any:
    """Constrain every output ``subject_id`` to identifiers owned by orchestration."""
    if isinstance(schema, list):
        return [_bind_subject_id_enums(item, allowed) for item in schema]
    if not isinstance(schema, dict):
        return schema
    bound = {key: _bind_subject_id_enums(value, allowed) for key, value in schema.items()}
    properties = bound.get("properties")
    if isinstance(properties, dict):
        subject = properties.get("subject_id")
        if isinstance(subject, dict):
            properties["subject_id"] = {**subject, "enum": list(allowed)}
    return bound


def _default_timeout() -> float:
    raw = os.environ.get("GEMINI_TIMEOUT_SECONDS")
    if not raw:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        return float(raw)
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS


class GeminiProvider:
    """Structured-output adapter for Google's Generative Language API."""

    adapter_name = "gemini"

    def __init__(
        self,
        *,
        model: str | None = None,
        api_key_env: str = "GEMINI_API_KEY",
        access_token_env: str = DEFAULT_ACCESS_TOKEN_ENV,
        endpoint: str | None = None,
        thinking_level: str | None = None,
        timeout_seconds: float | None = None,
        rate_limit_retries: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.model = model or DEFAULT_MODEL
        self._api_key_env = api_key_env
        self._access_token_env = access_token_env
        self._thinking_level = (
            thinking_level
            if thinking_level is not None
            else os.environ.get("GEMINI_THINKING_LEVEL", "")
        ).strip()
        self._endpoint = (endpoint or os.environ.get("GEMINI_ENDPOINT") or DEFAULT_ENDPOINT).rstrip(
            "/"
        )
        self._timeout = timeout_seconds if timeout_seconds is not None else _default_timeout()
        self._rate_limit_retries = rate_limit_retries
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

    def _auth_headers(self) -> dict[str, str]:
        """Pick the credential the configured host actually accepts.

        A bearer token wins when one is present: nothing sets it by accident, so its
        presence is a deliberate choice of the Vertex route. Falling back to the API key on
        a Vertex endpoint would spend a request to earn a 401 that names the wrong problem.
        """
        token = os.environ.get(self._access_token_env, "").strip()
        if token:
            return {"Authorization": f"Bearer {token}"}
        if _VERTEX_HOST in self._endpoint:
            msg = (
                f"{self._endpoint} is a Vertex endpoint, which authenticates with a bearer "
                f"token, but {self._access_token_env} is not set. Populate it from "
                "`gcloud auth application-default print-access-token`."
            )
            raise ProviderUnavailableError(msg)
        return {"x-goog-api-key": self._api_key()}

    @staticmethod
    def _image_part(image: ImageInput) -> dict[str, Any]:
        encoded = base64.b64encode(image.read_bytes()).decode("ascii")
        return {"inline_data": {"mime_type": image.media_type, "data": encoded}}

    @staticmethod
    def _record_usage(payload: Mapping[str, Any], usage: UsageSink | None) -> None:
        """Read `usageMetadata`, which Gemini sends beside the candidates."""
        if usage is None:
            return
        reported = payload.get("usageMetadata")
        if not isinstance(reported, Mapping):
            return
        # ``thoughtsTokenCount`` is reported beside the answer, never inside it, and bills
        # at the output rate. On a thinking model it is the larger of the two by an order of
        # magnitude, so reading only ``candidatesTokenCount`` would understate spend — and
        # would silently break the relationship between output length and elapsed time that
        # the latency model is fitted on.
        answer = reported.get("candidatesTokenCount")
        thoughts = reported.get("thoughtsTokenCount")
        billed_output = (
            None if answer is None and thoughts is None else (answer or 0) + (thoughts or 0)
        )
        usage.record(
            input_tokens=reported.get("promptTokenCount"),
            cached_input_tokens=reported.get("cachedContentTokenCount"),
            output_tokens=billed_output,
            reasoning_output_tokens=thoughts,
        )
        GeminiProvider._check_usage_adds_up(reported)

    @staticmethod
    def _check_usage_adds_up(reported: Mapping[str, Any]) -> None:
        """Warn when the reported parts stop summing to the reported total.

        `usageMetadata` defines the total as prompt plus answer plus thoughts plus tool-use
        prompt. That arithmetic is the cheapest available detector for the failure this
        adapter has already had once: a counter that exists, bills, and is not being read.
        A future field would show up here as a gap rather than as a quietly low number, so
        this warns and never raises — an unread counter is a measurement bug, not a reason
        to abandon an answer the caller already paid for.
        """
        parts = (
            "promptTokenCount",
            "candidatesTokenCount",
            "thoughtsTokenCount",
            "toolUsePromptTokenCount",
        )
        total = reported.get("totalTokenCount")
        if not isinstance(total, int):
            return
        counted = sum(value for name in parts if isinstance(value := reported.get(name), int))
        if counted == total:
            return
        get_logger("gemini").warning(
            "gemini_usage_does_not_reconcile",
            extra={
                "reported_total": total,
                "counted_total": counted,
                "unaccounted": total - counted,
                "fields_present": sorted(reported),
            },
        )

    def _call(
        self,
        *,
        prompt: str,
        images: Sequence[ImageInput],
        schema: Mapping[str, Any],
        usage: UsageSink | None = None,
    ) -> str:
        parts: list[dict[str, Any]] = [{"text": prompt}]
        parts.extend(self._image_part(image) for image in images)
        generation_config: dict[str, Any] = {
            "temperature": 0,
            "responseMimeType": "application/json",
            "responseSchema": schema,
        }
        if self._thinking_level:
            # Not validated here: the API rejects an unknown level with a 400 naming the
            # field, which is a better error than any list this adapter could keep current.
            generation_config["thinkingConfig"] = {"thinkingLevel": self._thinking_level}
        body = json.dumps(
            {
                "contents": [{"role": "user", "parts": parts}],
                "generationConfig": generation_config,
            }
        ).encode("utf-8")
        for attempt in range(self._rate_limit_retries + 1):
            request = urllib.request.Request(
                f"{self._endpoint}/models/{self.model}:generateContent",
                data=body,
                headers={"Content-Type": "application/json", **self._auth_headers()},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=self._timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                # Caught before URLError, which it subclasses: the API answered, so this is a
                # request or quota problem, not an unreachable host.
                detail = exc.read().decode("utf-8", errors="replace")
                delay = self._retry_after(exc, detail)
                if delay is not None and attempt < self._rate_limit_retries:
                    self._sleep(delay)
                    continue
                raise self._http_error(exc, detail) from exc
            except urllib.error.URLError as exc:
                msg = f"could not reach the Gemini API at {self._endpoint}: {exc.reason}"
                raise ProviderUnavailableError(msg) from exc
            except ConnectionResetError as exc:
                # On Windows an HTTPS peer reset can escape ``urlopen`` directly instead
                # of being wrapped in ``URLError``. Gemini occasionally does this during
                # the concurrent reviewer fan-out, so use the existing bounded retry
                # budget rather than aborting an otherwise healthy graph.
                if attempt < self._rate_limit_retries:
                    self._sleep(_DEFAULT_RETRY_SLEEP)
                    continue
                msg = f"Gemini reset the connection after {attempt + 1} attempt(s): {exc}"
                raise ProviderUnavailableError(msg) from exc
            except TimeoutError as exc:
                msg = f"Gemini did not respond within {self._timeout}s"
                raise ProviderUnavailableError(msg) from exc

            self._record_usage(payload, usage)
            return self._extract_text(payload)

        msg = "unreachable: the retry loop always returns or raises"  # pragma: no cover
        raise ProviderError(msg)  # pragma: no cover

    def _retry_after(self, exc: urllib.error.HTTPError, detail: str) -> float | None:
        """Seconds to wait before retrying, or ``None`` when retrying cannot help.

        A free-tier key with a per-minute request cap will trip this on any graph that calls
        more than a handful of nodes, and the API says exactly how long to wait — so honour
        it. A *quota* exhaustion reports ``limit: 0`` and no amount of waiting clears it;
        that one is a billing state and must surface immediately rather than sleeping three
        times first.

        ``503`` is the other transient case — the API's own wording is "spikes in demand are
        usually temporary" — and it carries no delay, so it falls through to a fixed wait.
        """
        if exc.code not in _RETRYABLE_STATUS or "limit: 0" in detail:
            return None
        for item in self._error_details(detail):
            raw = item.get("retryDelay")
            if isinstance(raw, str) and raw.endswith("s"):
                try:
                    return min(float(raw[:-1]), _MAX_RETRY_SLEEP)
                except ValueError:
                    break
        # The prose form carries a unit, and it is not always seconds: an almost-clear
        # rolling window reports "retry in 115.787057ms". Reading that as seconds would
        # sleep 115s for a 0.1s wait; ignoring the unit entirely would do the same.
        match = re.search(r"retry in ([0-9.]+)(ms|s)\b", detail)
        if match:
            seconds = float(match.group(1))
            if match.group(2) == "ms":
                seconds /= 1000.0
            return min(seconds, _MAX_RETRY_SLEEP)
        return _DEFAULT_RETRY_SLEEP

    @staticmethod
    def _error_details(detail: str) -> list[dict[str, Any]]:
        try:
            items = json.loads(detail)["error"]["details"]
        except (json.JSONDecodeError, KeyError, TypeError):
            return []
        return [item for item in items if isinstance(item, dict)]

    def _http_error(self, exc: urllib.error.HTTPError, detail: str) -> ProviderError:
        try:
            message = json.loads(detail)["error"]["message"]
        except (json.JSONDecodeError, KeyError, TypeError):
            message = detail.strip() or "no detail"
        if exc.code in {401, 403}:
            sent = (
                self._access_token_env
                if os.environ.get(self._access_token_env, "").strip()
                else self._api_key_env
            )
            return ProviderUnavailableError(
                f"Gemini rejected the credential in {sent} ({exc.code}): {message}"
            )
        if exc.code == 404:
            return ProviderUnavailableError(
                f"Gemini has no model {self.model!r} for this key ({exc.code}): {message}"
            )
        return ProviderError(f"Gemini request failed ({exc.code} {exc.reason}): {message}")

    @staticmethod
    def _extract_text(payload: Mapping[str, Any]) -> str:
        """Pull the one text part out, and name the reason when there isn't one.

        A response truncated by the output cap or stopped by a safety filter arrives as a
        well-formed body with no usable content. Reporting that as 'no text' would send the
        reader looking for a transport fault, so the finish reason is carried into the error.
        """
        candidates = payload.get("candidates") or []
        if not candidates:
            blocked = (payload.get("promptFeedback") or {}).get("blockReason")
            reason = f" (prompt blocked: {blocked})" if blocked else ""
            msg = f"Gemini returned no candidates{reason}"
            raise StructuredOutputError(msg)
        candidate = candidates[0]
        chunks = [
            part["text"]
            for part in (candidate.get("content") or {}).get("parts") or []
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        ]
        if not chunks:
            finish = candidate.get("finishReason", "unknown")
            msg = f"Gemini returned no text part (finishReason={finish})"
            raise StructuredOutputError(msg)
        return "".join(chunks)

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
        schema = to_gemini_schema(response_model.model_json_schema())
        allowed_subject_ids = metadata.get(OUTPUT_SUBJECT_IDS)
        if (
            isinstance(allowed_subject_ids, (list, tuple))
            and allowed_subject_ids
            and all(isinstance(item, str) for item in allowed_subject_ids)
        ):
            schema = _bind_subject_id_enums(schema, tuple(allowed_subject_ids))
        raw = await asyncio.to_thread(
            self._call,
            prompt=prompt,
            images=images,
            schema=schema,
            usage=usage_sink_of(metadata),
        )
        try:
            return response_model.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError) as exc:
            msg = (
                f"gemini returned unparseable output for {response_model.__name__} "
                f"(node={metadata.get('node', '?')}): {exc}"
            )
            raise StructuredOutputError(msg) from exc
