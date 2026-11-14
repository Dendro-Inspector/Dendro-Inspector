"""Serve the provider adapters from an agent instead of a vendor.

A local HTTP server that speaks the three wire dialects this project's adapters talk, and
answers nothing by itself. Each request is written to disk — the prompt, the JSON schema, and
the decoded image bytes — and the server blocks until an answer file appears. Whoever is
driving the session (a coding agent with vision, or you with a text editor) reads the image
and writes the structured JSON. The model behind the adapter is then a real multimodal model,
reached over a real socket, in the vendor's real wire format.

This is not `providers/fake.py`. The fake replays a fixture and is what every gate runs on.
This bridge exists for the case a fixture cannot cover: does the adapter carry a *live*
model's answer, about a *real* photograph, all the way through the graph.

It is also a deliberately strict vendor. Each dialect rejects the schema constructs the real
host rejects, so `providers/schema_compat.py` is exercised rather than trusted. Run
`probe_dialects.py` to confirm those checks still bite.

Usage
-----
    python scripts/agent-provider/bridge.py [--port 8799] [--state-dir .bridge]

Endpoints
---------
    POST /v1/files, DELETE /v1/files/<id>          Temporary OpenAI / Anthropic image uploads
    POST /v1/responses                           OpenAI Responses with file references
    POST /v1/messages                            Anthropic Messages with files or inline images
    POST /v1/chat/completions                     OpenAI-compatible (NVIDIA NIM, OpenRouter)
    POST /api/chat                                Ollama
    POST /v1beta/models/<model>:generateContent   Gemini

Prefix any path with `/fault/<name>` to inject a failure — see `FAULTS`. The adapters take a
base URL, so this is set through `NVIDIA_BASE_URL`, `GEMINI_ENDPOINT` or `OLLAMA_HOST`
without touching the code under test.

See docs/agent-as-provider.md for the whole workflow.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import re
import time
import uuid
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import RLock
from typing import Any

from dendro_inspector.observability.upstream_usage import upstream_usage
from dendro_inspector.providers.base import (
    MAX_IMAGE_FILE_BYTES,
    InvalidImageError,
    _image_media_type,
)

POLL_SECONDS = 0.5
MAX_UPLOAD_REQUEST_BYTES = MAX_IMAGE_FILE_BYTES + 64 * 1024
MAX_FILE_STORE_BYTES = 256 * 1024 * 1024

#: Constructs Pydantic emits that a host's structured-output dialect cannot accept. Listed
#: independently of `schema_compat`'s own allow-list on purpose: importing that module's
#: constants would make this check agree with the code by construction and prove nothing.
GEMINI_FORBIDDEN = frozenset(
    {"$ref", "$defs", "$schema", "additionalProperties", "const", "allOf", "oneOf", "not"}
)

#: llama.cpp's GBNF converter, which Ollama compiles the `format` schema through, does not
#: know the `\-` escape. Grammar engines behind OpenAI-compatible servers behave the same way.
ESCAPED_HYPHEN_IN_CLASS = re.compile(r"\[[^\]]*\\-[^\]]*\]")

FAULTS = (
    "unauthorized",  # 401 / 403: the adapter must accuse the credential, not the network
    "quota",  # Gemini 429 with `limit: 0` — waiting cannot clear a billing state
    "rate-limit",  # Gemini 429 carrying a retryDelay the adapter is supposed to honour
    "multimodal",  # NIM's 500 for a text-only model handed an image
    "flaky",  # one transient 500, then normal service
    "truncated",  # a well-formed envelope with no usable content
    "fenced",  # the answer wrapped in a ```json fence
    "garbage",  # prose where a JSON object belongs
)


class DialectRejectionError(Exception):
    """The host would refuse this request. Carries the vendor's own status and body."""

    def __init__(self, status: int, body: dict[str, Any], reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.body = body
        self.reason = reason


class State:
    """Where the conversation with the answering agent lives."""

    def __init__(self, root: Path, wait_timeout: float) -> None:
        self.root = root
        self.pending = root / "pending"
        self.answers = root / "answers"
        self.cache = root / "cache"
        self.images = root / "images"
        self.failures = root / "failures"
        self.log = root / "log.jsonl"
        self.wait_timeout = wait_timeout
        self.counter = 0
        self.fault_calls: dict[str, int] = {}
        # File references are transient and credential-scoped. Request evidence on disk
        # retains the bridge's existing lifecycle, independently of these upload handles.
        self.files: dict[str, tuple[str, str, bytes, int, int]] = {}
        self.lock = RLock()
        for directory in (
            self.root,
            self.pending,
            self.answers,
            self.cache,
            self.images,
            self.failures,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def next_id(self) -> int:
        with self.lock:
            self.counter += 1
            return self.counter

    def expire_files(self) -> None:
        with self.lock:
            now = time.time()
            for file_id, entry in tuple(self.files.items()):
                if entry[4] <= now:
                    del self.files[file_id]

    def record(self, request_id: int, **fields: Any) -> None:
        with self.log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"request_id": request_id, **fields}) + "\n")


def walk(node: Any) -> Any:
    """Every mapping and sequence inside a schema, including the root."""
    yield node
    if isinstance(node, dict):
        for value in node.values():
            yield from walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from walk(item)


def schema_stats(schema: dict[str, Any]) -> dict[str, Any]:
    keywords: set[str] = set()
    patterns: list[str] = []
    for node in walk(schema):
        if not isinstance(node, dict):
            continue
        keywords.update(key for key in node if isinstance(key, str))
        pattern = node.get("pattern")
        if isinstance(pattern, str):
            patterns.append(pattern)
    return {
        "top_level_properties": sorted((schema.get("properties") or {}).keys()),
        "distinct_keywords": sorted(keywords),
        "pattern_count": len(patterns),
        "patterns_with_escaped_hyphen": [
            pattern for pattern in patterns if ESCAPED_HYPHEN_IN_CLASS.search(pattern)
        ],
    }


def reject_gemini(stats: dict[str, Any]) -> None:
    bad = sorted(set(stats["distinct_keywords"]) & GEMINI_FORBIDDEN)
    if not bad:
        return
    raise DialectRejectionError(
        400,
        {
            "error": {
                "code": 400,
                "status": "INVALID_ARGUMENT",
                "message": (
                    f"Invalid JSON payload received. Unknown name {bad[0]!r} at "
                    "'generation_config.response_schema': Cannot find field."
                ),
            }
        },
        f"gemini dialect: unsupported schema keywords {bad}",
    )


def reject_ollama(stats: dict[str, Any]) -> None:
    if stats["patterns_with_escaped_hyphen"]:
        raise DialectRejectionError(
            400,
            {"error": "failed to parse grammar"},
            "ollama dialect: an escaped hyphen inside a character class",
        )


def reject_openai(stats: dict[str, Any]) -> None:
    if stats["patterns_with_escaped_hyphen"]:
        raise DialectRejectionError(
            400,
            {
                "error": {
                    "message": (
                        "Failed to compile the guided-decoding grammar for the supplied "
                        "json_schema."
                    ),
                    "type": "invalid_request_error",
                }
            },
            "openai dialect: an escaped hyphen inside a character class",
        )


def reject_anthropic(stats: dict[str, Any]) -> None:
    """Reject nothing, and mean it.

    The other three dialects hand the schema to a constrained decoder, so a construct the
    decoder cannot compile is a request-time error. This adapter appends the schema to the
    prompt as prose and asks for a JSON object back, so there is no grammar to compile and
    nothing to reject: `$ref`, `$defs`, `const` and an escaped hyphen all travel intact.

    Two consequences worth stating rather than discovering. The schema never passes through
    `providers/schema_compat.py` on this path, so the model resolves `$defs` itself; and
    every constraint is enforced only by Pydantic after the fact, which makes the repair
    retry — not the request — this dialect's sole line of defence.
    """
    del stats


REJECTORS = {
    "gemini": reject_gemini,
    "ollama": reject_ollama,
    "openai": reject_openai,
    "anthropic": reject_anthropic,
    "openai-responses": reject_anthropic,
}


def cache_key(
    prompt: str,
    properties: list[str],
    image_digests: list[str],
    model_namespace: str,
) -> str:
    """Identity of a model call, independent of the dialect that carried it.

    The prompt carries every upstream node's output, so an identical prompt means an identical
    position in an identical run. A second dialect replaying the first dialect's answers is
    therefore the same model answering the same question at temperature 0, and any divergence
    upstream changes the prompt and falls through to a fresh request.

    The photographs are hashed in because the prompt does **not** contain them. Case context
    names each image by id and media type only, so two different photographs inspected with
    the same season, object type and locale produce a byte-identical planner prompt. Keyed on
    the prompt alone, the second run silently received answers authored while looking at the
    first run's picture — a wrong result that looked like a fast one, visible only by
    comparing the digest in the pending metadata against the file on disk.

    ``model_namespace`` separates answer producers. A cached Ox-factory proposal must never
    become a Sol or Opus proposal merely because both were asked the same question through
    the same local wire dialect.
    """
    digest = hashlib.sha256()
    digest.update(model_namespace.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(prompt.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(",".join(properties).encode("utf-8"))
    digest.update(b"\x00")
    digest.update(",".join(image_digests).encode("utf-8"))
    return digest.hexdigest()[:16]


#: Faults that answer with an HTTP error, so no model answer is needed to trigger them. They
#: are raised before the answer lookup — asking an agent to author a response the fault then
#: throws away wastes the one resource this harness is short of.
RAISING_FAULTS = frozenset({"unauthorized", "quota", "rate-limit", "multimodal", "flaky"})

#: Faults that damage an otherwise valid answer on its way out. These need *some* text, but not
#: a good one: a repair retry carries a different prompt, misses the cache, and would otherwise
#: block waiting for an answer nobody is going to write.
CORRUPTING_FAULTS = frozenset({"truncated", "fenced", "garbage"})


def raise_fault(fault: str, state: State, dialect: str) -> None:
    """Answer with the vendor error this fault names. Returns only if the fault has passed."""
    seen = state.fault_calls.get(fault, 0)
    state.fault_calls[fault] = seen + 1
    if fault == "unauthorized" and dialect == "gemini":
        raise DialectRejectionError(
            403,
            {
                "error": {
                    "code": 403,
                    "status": "PERMISSION_DENIED",
                    "message": "API key not valid.",
                }
            },
            "fault: unauthorized",
        )
    if fault == "unauthorized":
        raise DialectRejectionError(
            401,
            {"error": {"message": "Incorrect API key provided.", "code": "invalid_api_key"}},
            "fault: unauthorized",
        )
    if fault == "quota":
        raise DialectRejectionError(
            429,
            {"error": {"code": 429, "message": "Quota exceeded, limit: 0, model: pro"}},
            "fault: exhausted quota",
        )
    if fault == "rate-limit":
        raise DialectRejectionError(
            429,
            {
                "error": {
                    "code": 429,
                    "message": "Too many requests. Please retry in 3s.",
                    "details": [
                        {
                            "@type": "type.googleapis.com/google.rpc.RetryInfo",
                            "retryDelay": "3s",
                        }
                    ],
                }
            },
            "fault: rate limited",
        )
    if fault == "multimodal":
        raise DialectRejectionError(
            500,
            {
                "error": {
                    "message": (
                        "Received multimodal data but multimodal processing is not enabled "
                        "for this model"
                    )
                }
            },
            "fault: text-only model handed an image",
        )
    if fault == "flaky" and seen == 0:
        raise DialectRejectionError(
            500,
            {"error": {"message": "EngineCore encountered an issue"}},
            "fault: one transient failure",
        )


def corrupt_answer(fault: str, answer: str) -> str:
    """Damage a valid answer the way a real model sometimes does."""
    if fault == "truncated":
        return ""
    if fault == "fenced":
        return f"```json\n{answer}\n```"
    if fault == "garbage":
        return "Looking at the photograph, I would say this is probably a pine log."
    return answer


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    state: State

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[http] {fmt % args}", flush=True)

    # The stdlib spells its request hooks this way.
    def do_POST(self) -> None:
        try:
            path, _ = self._split_fault(self.path)
        except DialectRejectionError as exc:
            self.close_connection = True
            self._send(exc.status, exc.body)
            return
        if path in {"/files", "/v1/files"}:
            self._upload_file()
            return
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        request_id = self.state.next_id()
        started = time.monotonic()
        dialect = "unknown"
        wanted = "unknown"
        stats: dict[str, Any] = {}
        saved: list[dict[str, Any]] = []
        try:
            body = json.loads(raw)
            path, fault = self._split_fault(self.path)
            dialect, prompt, images, schema, model, wanted = self._parse(path, body)
            stats = schema_stats(schema)
            REJECTORS[dialect](stats)
            saved = self._decode_images(images, request_id)
            if fault in RAISING_FAULTS:
                raise_fault(fault, self.state, dialect)
            key = cache_key(
                prompt,
                stats["top_level_properties"],
                [str(image["sha256"]) for image in saved],
                model,
            )
            answer, source, usage = self._answer(
                request_id,
                dialect,
                prompt,
                schema,
                saved,
                key,
                wanted,
                model,
                stub_on_miss=fault in CORRUPTING_FAULTS,
            )
            if fault:
                answer = corrupt_answer(fault, answer)
                source = f"{source}+fault:{fault}"
            self._log(request_id, dialect, model, wanted, stats, saved, "accepted", source, started)
            self._send(200, self._envelope(dialect, model, answer, usage))
        except DialectRejectionError as exc:
            verdict = f"rejected: {exc.reason}"
            self._log(request_id, dialect, "-", wanted, stats, saved, verdict, "vendor", started)
            print(f"[bridge] req-{request_id:03d} {verdict}", flush=True)
            self._send(exc.status, exc.body)
        except Exception as exc:
            # A bridge fault must not be mistaken for a model or transport fault.
            print(f"[bridge] req-{request_id:03d} bridge error: {exc!r}", flush=True)
            self._send(500, {"error": {"message": f"bridge fault: {exc!r}"}})

    def _file_scope(self) -> str:
        """Check native SDK headers; never retain the placeholder credential itself."""
        anthropic = bool(self.headers.get("anthropic-version"))
        if anthropic:
            self._anthropic_headers()
        else:
            self._require_header("Authorization", "Bearer ")
        bearer = self.headers.get("authorization") or ""
        key = bearer[7:].strip() if bearer.startswith("Bearer ") else ""
        key = key or (self.headers.get("x-api-key") or "").strip()
        return hashlib.sha256(f"{anthropic}:{key}".encode()).hexdigest()

    @staticmethod
    def _file_error(status: int, message: str) -> DialectRejectionError:
        return DialectRejectionError(
            status,
            {"type": "error", "error": {"type": "invalid_request_error", "message": message}},
            message,
        )

    def _upload_file(self) -> None:
        try:
            scope = self._file_scope()
            length = int(self.headers.get("Content-Length") or 0)
            if not 0 < length <= MAX_UPLOAD_REQUEST_BYTES:
                raise self._file_error(413, "image upload exceeds the bridge request limit")
            content_type = self.headers.get("Content-Type", "")
            raw = self.rfile.read(length)
            message = BytesParser(policy=policy.default).parsebytes(
                f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode() + raw
            )
            if message.get_content_type() != "multipart/form-data" or not message.is_multipart():
                raise self._file_error(400, "file upload requires multipart/form-data")
            fields: dict[str, bytes] = {}
            media_type = ""
            for part in message.iter_parts():
                name = part.get_param("name", header="content-disposition")
                if not isinstance(name, str) or name in fields:
                    raise self._file_error(400, "invalid or duplicate upload field")
                payload = part.get_payload(decode=True)
                if not isinstance(payload, bytes):
                    raise self._file_error(400, "invalid upload field")
                fields[name] = payload
                if name == "file":
                    media_type = part.get_content_type()
            data = fields.get("file", b"")
            if not data or len(data) > MAX_IMAGE_FILE_BYTES:
                raise self._file_error(413, "invalid image file size")
            if _image_media_type(data[:12]) != media_type:
                raise self._file_error(400, "image MIME type does not match its bytes")
            anthropic = bool(self.headers.get("anthropic-version"))
            if anthropic:
                ttl = int(fields.get("expires_in_seconds", b"0"))
                maximum = 7776000
            else:
                if fields.get("purpose") != b"vision":
                    raise self._file_error(400, "bridge image uploads require purpose=vision")
                if fields.get("expires_after[anchor]") != b"created_at":
                    raise self._file_error(400, "bridge image uploads require created_at expiry")
                ttl = int(fields.get("expires_after[seconds]", b"0"))
                maximum = 2592000
            if not 3600 <= ttl <= maximum:
                raise self._file_error(400, "image upload requires a valid expiry")
            with self.state.lock:
                self.state.expire_files()
                if (
                    sum(len(entry[2]) for entry in self.state.files.values()) + len(data)
                    > MAX_FILE_STORE_BYTES
                ):
                    raise self._file_error(413, "bridge temporary image storage is full")
                file_id = "file-" + uuid.uuid4().hex
                created = int(time.time())
                self.state.files[file_id] = (scope, media_type, data, created, created + ttl)
            filename = f"image.{media_type.split('/', 1)[1]}"
            if anthropic:
                result = {
                    "id": file_id,
                    "type": "file",
                    "filename": filename,
                    "mime_type": media_type,
                    "size_bytes": len(data),
                    "downloadable": False,
                    "created_at": self._timestamp(created),
                    "expires_at": self._timestamp(created + ttl),
                }
            else:
                result = {
                    "id": file_id,
                    "object": "file",
                    "filename": filename,
                    "bytes": len(data),
                    "created_at": created,
                    "expires_at": created + ttl,
                    "purpose": "vision",
                    "status": "processed",
                }
            self._send(200, result)
        except (ValueError, InvalidImageError):
            self.close_connection = True
            self._send(400, {"error": {"message": "invalid image upload"}})
        except DialectRejectionError as exc:
            self.close_connection = True
            self._send(exc.status, exc.body)

    @staticmethod
    def _timestamp(value: int) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(value))

    def _file_image(self, file_id: str) -> tuple[str, bytes]:
        scope = self._file_scope()
        with self.state.lock:
            self.state.expire_files()
            entry = self.state.files.get(file_id)
            if entry is None or entry[0] != scope:
                raise self._file_error(404, "image file is unavailable")
            return entry[1], entry[2]

    def do_DELETE(self) -> None:
        try:
            path, _ = self._split_fault(self.path)
            match = re.fullmatch(r"/(?:v1/)?files/(file-[a-f0-9]{32})", path)
            if match is None:
                raise self._file_error(404, "unknown file route")
            file_id = match.group(1)
            with self.state.lock:
                self._file_image(file_id)
                del self.state.files[file_id]
            result = (
                {"id": file_id, "type": "file_deleted"}
                if self.headers.get("anthropic-version")
                else {"id": file_id, "object": "file", "deleted": True}
            )
            self._send(200, result)
        except DialectRejectionError as exc:
            self._send(exc.status, exc.body)

    def _anthropic_headers(self) -> None:
        bearer = self.headers.get("authorization") or ""
        if not (bearer.startswith("Bearer ") and bearer[7:].strip()):
            self._require_header("x-api-key", "")
        self._require_header("anthropic-version", "")

    @staticmethod
    def _content(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Keep every message: the shared image prefix and changing task are separate."""
        result = []
        for message in messages:
            content = message["content"]
            result.extend(
                [{"type": "text", "text": content}] if isinstance(content, str) else content
            )
        return result

    @staticmethod
    def _split_fault(path: str) -> tuple[str, str]:
        match = re.match(r"^/fault/([a-z-]+)(/.*)$", path)
        if not match:
            return path, ""
        fault = match.group(1)
        if fault not in FAULTS:
            raise DialectRejectionError(
                404,
                {"error": {"message": f"unknown fault {fault!r}; known: {', '.join(FAULTS)}"}},
                f"unknown fault {fault!r}",
            )
        return match.group(2), fault

    def _parse(
        self, path: str, body: dict[str, Any]
    ) -> tuple[str, str, list[tuple[str, str | bytes]], dict[str, Any], str, str]:
        if path.endswith("/responses"):
            self._require_header("Authorization", "Bearer ")
            content = self._content(body["input"])
            prompt = "".join(
                part["text"] for part in content if part.get("type") in {"text", "input_text"}
            )
            images: list[tuple[str, str | bytes]] = [
                self._file_image(part["file_id"])
                if part.get("file_id")
                else self._split_data_uri(part["image_url"])
                for part in content
                if part.get("type") == "input_image"
            ]
            spec = body["text"]["format"]
            return (
                "openai-responses",
                prompt,
                images,
                spec["schema"],
                body.get("model", "?"),
                spec["name"],
            )
        if path.endswith("/chat/completions"):
            self._require_header("Authorization", "Bearer ")
            content = self._content(body["messages"])
            prompt = "".join(part["text"] for part in content if part.get("type") == "text")
            images = [
                self._split_data_uri(part["image_url"]["url"])
                for part in content
                if part.get("type") == "image_url"
            ]
            spec = body["response_format"]["json_schema"]
            return "openai", prompt, images, spec["schema"], body.get("model", "?"), spec["name"]
        if path.endswith("/api/chat"):
            message = body["messages"][0]
            images = [("image/jpeg", payload) for payload in message.get("images") or []]
            schema = body["format"]
            name = schema.get("title", "unknown")
            return "ollama", message["content"], images, schema, body.get("model", "?"), name
        if path.endswith("/v1/messages"):
            # SDK releases on API version 2026-10-21 send the key as a bearer token; older
            # ones use x-api-key. The real host accepts both, so the bridge does too.
            self._anthropic_headers()
            if not body.get("max_tokens"):
                raise DialectRejectionError(
                    400,
                    {
                        "type": "error",
                        "error": {
                            "type": "invalid_request_error",
                            "message": "max_tokens: Field required",
                        },
                    },
                    "anthropic requires max_tokens",
                )
            content = self._content(body["messages"])
            prompt = "".join(part["text"] for part in content if part.get("type") == "text")
            images = [
                self._file_image(part["source"]["file_id"])
                if part["source"]["type"] == "file"
                else (part["source"]["media_type"], part["source"]["data"])
                for part in content
                if part.get("type") == "image"
            ]
            # Accept native Messages schemas as well as the bridge adapter's prompt
            # contract. A missing schema is a rejection, never a silent empty contract.
            native = body.get("output_config", {}).get("format", {})
            schema = (
                native["schema"]
                if native.get("type") == "json_schema"
                else self._trailing_schema(prompt)
            )
            return (
                "anthropic",
                prompt,
                images,
                schema,
                body.get("model", "?"),
                schema.get("title", "unknown"),
            )
        match = re.search(r"/models/([^:]+):generateContent$", path)
        if match:
            self._require_header("x-goog-api-key", "")
            parts = body["contents"][0]["parts"]
            prompt = "".join(part["text"] for part in parts if "text" in part)
            images = [
                (part["inline_data"]["mime_type"], part["inline_data"]["data"])
                for part in parts
                if "inline_data" in part
            ]
            schema = body["generationConfig"]["responseSchema"]
            return "gemini", prompt, images, schema, match.group(1), schema.get("title", "unknown")
        raise DialectRejectionError(
            404, {"error": {"message": f"no route for {path}"}}, f"unrouted path {path}"
        )

    @staticmethod
    def _trailing_schema(prompt: str) -> dict[str, Any]:
        """Recover the JSON Schema the Anthropic adapter appended to the prompt text.

        The object runs to the end of the prompt, so decoding from the last ``{`` that
        parses is enough; scanning from the marker forward would stop at the first brace
        inside the prose above it.
        """
        marker = prompt.rfind("## Required output")
        tail = prompt[marker:] if marker >= 0 else prompt
        start = tail.find("{")
        while start >= 0:
            try:
                decoded = json.loads(tail[start:])
            except json.JSONDecodeError:
                start = tail.find("{", start + 1)
                continue
            if isinstance(decoded, dict):
                return decoded
            break
        raise DialectRejectionError(
            400,
            {
                "type": "error",
                "error": {
                    "type": "invalid_request_error",
                    "message": "no JSON Schema found in the message content",
                },
            },
            "anthropic prompt carried no schema",
        )

    def _require_header(self, name: str, prefix: str) -> None:
        value = self.headers.get(name) or ""
        if value.startswith(prefix) and value[len(prefix) :].strip():
            return
        if name == "anthropic-version":
            raise DialectRejectionError(
                400,
                {
                    "type": "error",
                    "error": {
                        "type": "invalid_request_error",
                        "message": "anthropic-version: Field required",
                    },
                },
                f"no {name} header",
            )
        if name == "x-api-key":
            raise DialectRejectionError(
                401,
                {
                    "type": "error",
                    "error": {
                        "type": "authentication_error",
                        "message": "invalid x-api-key",
                    },
                },
                f"no credential in {name}",
            )
        if name == "x-goog-api-key":
            raise DialectRejectionError(
                403,
                {
                    "error": {
                        "code": 403,
                        "status": "PERMISSION_DENIED",
                        "message": "API key not valid. Please pass a valid API key.",
                    }
                },
                f"no credential in {name}",
            )
        raise DialectRejectionError(
            401,
            {"error": {"message": "Incorrect API key provided.", "code": "invalid_api_key"}},
            f"no credential in {name}",
        )

    @staticmethod
    def _split_data_uri(url: str) -> tuple[str, str]:
        match = re.match(r"^data:([^;]+);base64,(.*)$", url, re.DOTALL)
        if not match:
            raise DialectRejectionError(
                400,
                {"error": {"message": "image_url must be an inline base64 data URI"}},
                "image_url was not a data URI",
            )
        return match.group(1), match.group(2)

    def _decode_images(
        self, raw: list[tuple[str, str | bytes]], request_id: int
    ) -> list[dict[str, Any]]:
        """Persist every image the adapter actually transmitted, and fingerprint it."""
        out: list[dict[str, Any]] = []
        for index, (media_type, payload) in enumerate(raw, start=1):
            try:
                data = (
                    payload
                    if isinstance(payload, bytes)
                    else base64.b64decode(payload, validate=True)
                )
            except (binascii.Error, ValueError) as exc:
                raise DialectRejectionError(
                    400,
                    {"error": {"message": f"image {index} is not valid base64: {exc}"}},
                    "image payload was not decodable base64",
                ) from exc
            suffix = {
                "image/jpeg": ".jpg",
                "image/png": ".png",
                "image/webp": ".webp",
                "image/gif": ".gif",
            }.get(media_type, ".bin")
            path = self.state.images / f"req-{request_id:03d}-img{index}{suffix}"
            path.write_bytes(data)
            out.append(
                {
                    "index": index,
                    "media_type": media_type,
                    "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "path": str(path),
                }
            )
        return out

    def _answer(
        self,
        request_id: int,
        dialect: str,
        prompt: str,
        schema: dict[str, Any],
        images: list[dict[str, Any]],
        key: str,
        wanted: str,
        requested_model: str,
        stub_on_miss: bool = False,
    ) -> tuple[str, str, dict[str, int] | None]:
        cached = self.state.cache / f"{key}.json"
        if cached.is_file():
            print(f"[bridge] req-{request_id:03d} {dialect} cache hit {key}", flush=True)
            source, usage = self._answer_provenance(cached.with_suffix(".meta.json"), "cache")
            return (
                cached.read_text(encoding="utf-8"),
                source,
                usage,
            )
        if stub_on_miss:
            # A corrupting fault is about to damage this text anyway.
            return "{}", "stub", None
        self._write_pending(
            request_id,
            dialect,
            prompt,
            schema,
            images,
            key,
            wanted,
            requested_model,
        )
        answer = self.state.answers / f"{key}.json"
        deadline = time.monotonic() + self.state.wait_timeout
        print(
            f"[bridge] req-{request_id:03d} {dialect}/{wanted} needs answers/{key}.json", flush=True
        )
        while time.monotonic() < deadline:
            if answer.is_file():
                text = answer.read_text(encoding="utf-8")
                cached.write_text(text, encoding="utf-8")
                answer_meta = answer.with_suffix(".meta.json")
                cache_meta = cached.with_suffix(".meta.json")
                if answer_meta.is_file():
                    cache_meta.write_text(answer_meta.read_text(encoding="utf-8"), encoding="utf-8")
                print(f"[bridge] req-{request_id:03d} answered ({len(text)} chars)", flush=True)
                source, usage = self._answer_provenance(answer_meta, "live")
                return text, source, usage
            terminal = self._terminal_failure(request_id, key)
            if terminal is not None:
                error_type = str(terminal.get("error_type") or "WorkerTerminalError")
                message = str(terminal.get("message") or "upstream worker failed")[:500]
                raise DialectRejectionError(
                    424,
                    {"error": {"message": f"upstream worker failed: {message}"}},
                    f"upstream worker terminal failure ({error_type})",
                )
            time.sleep(POLL_SECONDS)
        raise DialectRejectionError(
            504,
            {"error": {"message": "the model did not answer in time"}},
            f"no answer file appeared within {self.state.wait_timeout}s",
        )

    def _write_pending(
        self,
        request_id: int,
        dialect: str,
        prompt: str,
        schema: dict[str, Any],
        images: list[dict[str, Any]],
        key: str,
        wanted: str,
        requested_model: str,
    ) -> None:
        stem = f"req-{request_id:03d}"
        # Request ids restart with a restarted bridge. An old marker from the same state
        # directory must not make a new request fail before a worker has examined it.
        (self.state.failures / f"{stem}-terminal.json").unlink(missing_ok=True)
        (self.state.pending / f"{stem}-prompt.txt").write_text(prompt, encoding="utf-8")
        (self.state.pending / f"{stem}-schema.json").write_text(
            json.dumps(schema, indent=2), encoding="utf-8"
        )
        (self.state.pending / f"{stem}-meta.json").write_text(
            json.dumps(
                {
                    "request_id": request_id,
                    "dialect": dialect,
                    "requested_model": requested_model,
                    "response_model": wanted,
                    "cache_key": key,
                    "answer_file": str(self.state.answers / f"{key}.json"),
                    "images": images,
                    "prompt_chars": len(prompt),
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    @staticmethod
    def _answer_provenance(meta_path: Path, prefix: str) -> tuple[str, dict[str, int] | None]:
        """Read worker identity and measured usage without making metadata a dependency."""
        if not meta_path.is_file():
            return f"{prefix}:manual", None
        try:
            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return f"{prefix}:unknown", None
        if not isinstance(metadata, dict):
            return f"{prefix}:unknown", None
        worker_id = metadata.get("worker_id")
        source = f"{prefix}:{worker_id}" if isinstance(worker_id, str) else f"{prefix}:unknown"
        upstream = metadata.get("upstream")
        usage = upstream_usage(upstream) if isinstance(upstream, dict) else None
        return source, usage

    def _terminal_failure(self, request_id: int, cache_key: str) -> dict[str, Any] | None:
        """Read only the terminal marker for this exact bridge request and cache key."""
        path = self.state.failures / f"req-{request_id:03d}-terminal.json"
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict) or payload.get("cache_key") != cache_key:
            return None
        return payload

    def _log(
        self,
        request_id: int,
        dialect: str,
        model: str,
        wanted: str,
        stats: dict[str, Any],
        images: list[dict[str, Any]],
        verdict: str,
        source: str,
        started: float,
    ) -> None:
        self.state.record(
            request_id,
            dialect=dialect,
            model=model,
            response_model=wanted,
            stats=stats,
            images=images,
            verdict=verdict,
            source=source,
            elapsed_seconds=round(time.monotonic() - started, 3),
        )

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def _envelope(
        dialect: str,
        model: str,
        text: str,
        usage: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        if dialect == "openai-responses":
            response: dict[str, Any] = {
                "id": "resp_bridge",
                "object": "response",
                "created_at": int(time.time()),
                "model": model,
                "status": "completed" if text else "incomplete",
                "store": False,
                "error": None,
                "incomplete_details": None if text else {"reason": "max_output_tokens"},
                "output": [
                    {
                        "id": "msg_bridge",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": text, "annotations": []}],
                    }
                ]
                if text
                else [],
                "usage": None,
            }
            if usage is not None:
                response["usage"] = {
                    "input_tokens": usage.get("input_tokens"),
                    "output_tokens": usage.get("output_tokens"),
                    "input_tokens_details": {
                        "cached_tokens": usage.get("cached_input_tokens"),
                        "cache_write_tokens": usage.get("cache_write_input_tokens"),
                    },
                    "output_tokens_details": {
                        "reasoning_tokens": usage.get("reasoning_output_tokens")
                    },
                }
            return response
        if dialect == "openai":
            envelope: dict[str, Any] = {
                "id": "chatcmpl-bridge",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": text},
                        "finish_reason": "stop" if text else "length",
                    }
                ],
            }
            if usage is not None:
                envelope["usage"] = {
                    "prompt_tokens": usage.get("input_tokens"),
                    "completion_tokens": usage.get("output_tokens"),
                    "prompt_tokens_details": {"cached_tokens": usage.get("cached_input_tokens")},
                }
            return envelope
        if dialect == "ollama":
            envelope = {
                "model": model,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "message": {"role": "assistant", "content": text},
                "done": True,
                "done_reason": "stop",
            }
            if usage is not None:
                envelope["prompt_eval_count"] = usage.get("input_tokens")
                envelope["eval_count"] = usage.get("output_tokens")
            return envelope
        if dialect == "anthropic":
            # `usage` and `stop_reason` are not decoration: the SDK parses this into typed
            # objects and a caller that reads `stop_reason` to detect truncation needs the
            # empty-text case to say `max_tokens`, exactly as the real API does.
            bridge_usage = {
                "input_tokens": usage.get("input_tokens") if usage is not None else None,
                "cached_input_tokens": (
                    usage.get("cached_input_tokens") if usage is not None else None
                ),
                "output_tokens": usage.get("output_tokens") if usage is not None else None,
                "cache_write_input_tokens": usage.get("cache_write_input_tokens")
                if usage is not None
                else None,
                "reasoning_output_tokens": usage.get("reasoning_output_tokens")
                if usage is not None
                else None,
            }
            anthropic_usage = {
                # The Anthropic SDK requires these two integers. `dendro_usage` below
                # carries the authoritative nullable bridge counters for the adapter.
                "input_tokens": max(
                    0,
                    (bridge_usage["input_tokens"] or 0)
                    - (bridge_usage["cached_input_tokens"] or 0)
                    - (bridge_usage["cache_write_input_tokens"] or 0),
                ),
                "output_tokens": bridge_usage["output_tokens"] or 0,
            }
            if bridge_usage["cached_input_tokens"] is not None:
                anthropic_usage["cache_read_input_tokens"] = bridge_usage["cached_input_tokens"]
            if bridge_usage["cache_write_input_tokens"] is not None:
                anthropic_usage["cache_creation_input_tokens"] = bridge_usage[
                    "cache_write_input_tokens"
                ]
            return {
                "id": "msg_bridge",
                "type": "message",
                "role": "assistant",
                "model": model,
                "content": [{"type": "text", "text": text}] if text else [],
                "stop_reason": "end_turn" if text else "max_tokens",
                "stop_sequence": None,
                "usage": anthropic_usage,
                "dendro_usage": bridge_usage,
            }
        envelope = {
            "candidates": [
                {
                    "content": {"role": "model", "parts": [{"text": text}] if text else []},
                    "finishReason": "STOP" if text else "MAX_TOKENS",
                    "index": 0,
                }
            ],
            "modelVersion": model,
        }
        if usage is not None:
            envelope["usageMetadata"] = {
                "promptTokenCount": usage.get("input_tokens"),
                "cachedContentTokenCount": usage.get("cached_input_tokens"),
                "candidatesTokenCount": usage.get("output_tokens"),
            }
        return envelope


class BridgeServer(ThreadingHTTPServer):
    def service_actions(self) -> None:
        self.RequestHandlerClass.state.expire_files()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--port", type=int, default=8799)
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=Path(".bridge"),
        help="where prompts, schemas, images, answers and the cache live (git-ignored)",
    )
    parser.add_argument(
        "--wait-timeout",
        type=float,
        default=3000.0,
        help="seconds to wait for an answer file before returning 504",
    )
    args = parser.parse_args()

    Handler.state = State(args.state_dir.resolve(), args.wait_timeout)
    server = BridgeServer(("127.0.0.1", args.port), Handler)
    print(f"[bridge] listening on http://127.0.0.1:{args.port}", flush=True)
    print(f"[bridge] state    {Handler.state.root}", flush=True)
    print(f"[bridge] faults   {', '.join(FAULTS)} (prefix a path with /fault/<name>)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[bridge] stopped", flush=True)


if __name__ == "__main__":
    main()
