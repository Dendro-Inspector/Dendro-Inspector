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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

POLL_SECONDS = 0.5

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
        self.log = root / "log.jsonl"
        self.wait_timeout = wait_timeout
        self.counter = 0
        self.fault_calls: dict[str, int] = {}
        for directory in (self.root, self.pending, self.answers, self.cache, self.images):
            directory.mkdir(parents=True, exist_ok=True)

    def next_id(self) -> int:
        self.counter += 1
        return self.counter

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
}


def cache_key(prompt: str, properties: list[str], image_digests: list[str]) -> str:
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
    """
    digest = hashlib.sha256()
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
            )
            answer, source = self._answer(
                request_id,
                dialect,
                prompt,
                schema,
                saved,
                key,
                wanted,
                stub_on_miss=fault in CORRUPTING_FAULTS,
            )
            if fault:
                answer = corrupt_answer(fault, answer)
                source = f"{source}+fault:{fault}"
            self._log(request_id, dialect, model, wanted, stats, saved, "accepted", source, started)
            self._send(200, self._envelope(dialect, model, answer))
        except DialectRejectionError as exc:
            verdict = f"rejected: {exc.reason}"
            self._log(request_id, dialect, "-", wanted, stats, saved, verdict, "vendor", started)
            print(f"[bridge] req-{request_id:03d} {verdict}", flush=True)
            self._send(exc.status, exc.body)
        except Exception as exc:
            # A bridge fault must not be mistaken for a model or transport fault.
            print(f"[bridge] req-{request_id:03d} bridge error: {exc!r}", flush=True)
            self._send(500, {"error": {"message": f"bridge fault: {exc!r}"}})

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
    ) -> tuple[str, str, list[tuple[str, str]], dict[str, Any], str, str]:
        if path.endswith("/chat/completions"):
            self._require_header("Authorization", "Bearer ")
            content = body["messages"][0]["content"]
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
            self._require_header("x-api-key", "")
            self._require_header("anthropic-version", "")
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
            content = body["messages"][0]["content"]
            prompt = "".join(part["text"] for part in content if part.get("type") == "text")
            images = [
                (part["source"]["media_type"], part["source"]["data"])
                for part in content
                if part.get("type") == "image"
            ]
            # Unlike the other three, this adapter carries no structured-output field: it
            # appends the raw Pydantic schema to the prompt as prose. Recover it from the
            # tail so the pending request still shows the contract that was actually asked
            # for, and so a missing one is a loud rejection rather than a silent `{}`.
            schema = self._trailing_schema(prompt)
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

    def _decode_images(self, raw: list[tuple[str, str]], request_id: int) -> list[dict[str, Any]]:
        """Persist every image the adapter actually transmitted, and fingerprint it."""
        out: list[dict[str, Any]] = []
        for index, (media_type, payload) in enumerate(raw, start=1):
            try:
                data = base64.b64decode(payload, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise DialectRejectionError(
                    400,
                    {"error": {"message": f"image {index} is not valid base64: {exc}"}},
                    "image payload was not decodable base64",
                ) from exc
            suffix = {"image/jpeg": ".jpg", "image/png": ".png"}.get(media_type, ".bin")
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
        stub_on_miss: bool = False,
    ) -> tuple[str, str]:
        cached = self.state.cache / f"{key}.json"
        if cached.is_file():
            print(f"[bridge] req-{request_id:03d} {dialect} cache hit {key}", flush=True)
            return cached.read_text(encoding="utf-8"), "cache"
        if stub_on_miss:
            # A corrupting fault is about to damage this text anyway.
            return "{}", "stub"
        self._write_pending(request_id, dialect, prompt, schema, images, key, wanted)
        answer = self.state.answers / f"{key}.json"
        deadline = time.monotonic() + self.state.wait_timeout
        print(
            f"[bridge] req-{request_id:03d} {dialect}/{wanted} needs answers/{key}.json", flush=True
        )
        while time.monotonic() < deadline:
            if answer.is_file():
                text = answer.read_text(encoding="utf-8")
                cached.write_text(text, encoding="utf-8")
                print(f"[bridge] req-{request_id:03d} answered ({len(text)} chars)", flush=True)
                return text, "live"
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
    ) -> None:
        stem = f"req-{request_id:03d}"
        (self.state.pending / f"{stem}-prompt.txt").write_text(prompt, encoding="utf-8")
        (self.state.pending / f"{stem}-schema.json").write_text(
            json.dumps(schema, indent=2), encoding="utf-8"
        )
        (self.state.pending / f"{stem}-meta.json").write_text(
            json.dumps(
                {
                    "request_id": request_id,
                    "dialect": dialect,
                    "response_model": wanted,
                    "answer_file": str(self.state.answers / f"{key}.json"),
                    "images": images,
                    "prompt_chars": len(prompt),
                },
                indent=2,
            ),
            encoding="utf-8",
        )

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
    def _envelope(dialect: str, model: str, text: str) -> dict[str, Any]:
        if dialect == "openai":
            return {
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
        if dialect == "ollama":
            return {
                "model": model,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "message": {"role": "assistant", "content": text},
                "done": True,
                "done_reason": "stop",
            }
        if dialect == "anthropic":
            # `usage` and `stop_reason` are not decoration: the SDK parses this into typed
            # objects and a caller that reads `stop_reason` to detect truncation needs the
            # empty-text case to say `max_tokens`, exactly as the real API does.
            return {
                "id": "msg_bridge",
                "type": "message",
                "role": "assistant",
                "model": model,
                "content": [{"type": "text", "text": text}] if text else [],
                "stop_reason": "end_turn" if text else "max_tokens",
                "stop_sequence": None,
                "usage": {"input_tokens": 0, "output_tokens": 0},
            }
        return {
            "candidates": [
                {
                    "content": {"role": "model", "parts": [{"text": text}] if text else []},
                    "finishReason": "STOP" if text else "MAX_TOKENS",
                    "index": 0,
                }
            ],
            "modelVersion": model,
        }


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
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"[bridge] listening on http://127.0.0.1:{args.port}", flush=True)
    print(f"[bridge] state    {Handler.state.root}", flush=True)
    print(f"[bridge] faults   {', '.join(FAULTS)} (prefix a path with /fault/<name>)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[bridge] stopped", flush=True)


if __name__ == "__main__":
    main()
