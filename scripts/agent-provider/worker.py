"""Run one automatic answer worker against the local agent-provider bridge queue.

The bridge remains the single loopback gateway. It writes typed requests under
``.bridge/pending``; any number of these workers may watch that directory. A worker claims
one answer key with an exclusive file create, calls its upstream model, records provenance,
and publishes the raw answer atomically. The first free worker that creates the claim gets
the job.

Only workers configured for the request's ``requested_model`` route may claim it. This keeps
the cheap Ox pool separate from Sol/Opus judge routes even though all of them use the same
bridge state directory.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dendro_inspector.providers.schema_compat import to_strict_openai_schema

POLL_SECONDS = 0.5
DEFAULT_TIMEOUT_SECONDS = 600.0
DEFAULT_CLAIM_TTL_SECONDS = 1800.0
DEFAULT_FAILURE_COOLDOWN_SECONDS = 30.0
DEFAULT_RATE_LIMIT_COOLDOWN_SECONDS = 90.0
DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class WorkerError(Exception):
    """An upstream worker could not produce a usable text answer."""


class WorkerRateLimitError(WorkerError):
    """The route is temporarily unavailable and should enter cooldown."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


@dataclass(frozen=True, slots=True)
class Job:
    request_id: int
    cache_key: str
    route: str
    response_model: str
    prompt_path: Path
    schema_path: Path
    image_paths: tuple[Path, ...]
    answer_path: Path
    claim_path: Path


@dataclass(frozen=True, slots=True)
class WorkerResult:
    text: str
    upstream: dict[str, Any]


def load_env_file(path: Path) -> None:
    """Load the repository's ignored env file without logging any value."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, raw = stripped.partition("=")
        key = name.strip()
        if not key or key in os.environ:
            continue
        value = raw.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ[key] = value


def atomic_write_text(path: Path, text: str) -> None:
    """Publish a complete file; the bridge must never observe a partial answer."""
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def utf8_subprocess_environment() -> dict[str, str]:
    """Give local CLI children a strict, platform-independent text contract."""
    environment = os.environ.copy()
    environment.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
    return environment


def codex_usage(stdout: str) -> dict[str, int] | None:
    """Extract measured token counts from Codex ``--json`` turn completion events."""
    usage: dict[str, int] | None = None
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        raw = event.get("usage") if event.get("type") == "turn.completed" else None
        if not isinstance(raw, dict):
            continue
        parsed = {
            key: int(value)
            for key, value in raw.items()
            if key
            in {
                "input_tokens",
                "cached_input_tokens",
                "cache_write_input_tokens",
                "output_tokens",
                "reasoning_output_tokens",
                "total_tokens",
            }
            and isinstance(value, int)
            and not isinstance(value, bool)
            and value >= 0
        }
        if parsed:
            usage = parsed
    return usage


def _claim(path: Path, worker_id: str, ttl_seconds: float) -> bool:
    # Read the age in one call: two workers sharing a capacity group race here, and an
    # `exists()` that is true before the other worker releases the lease must not crash the
    # poll loop with the stat that follows it.
    try:
        expired = time.time() - path.stat().st_mtime > ttl_seconds
    except FileNotFoundError:
        expired = False
    if expired:
        with contextlib.suppress(FileNotFoundError):
            path.unlink()
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    except PermissionError:
        # Windows reports a delete-pending file as EACCES rather than EEXIST: the holder
        # unlinked the lease microseconds ago and the name is not free yet. That is a lost
        # race like any other, not a broken directory, so poll again instead of dying.
        return False
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(
            {"worker_id": worker_id, "pid": os.getpid(), "claimed_at": time.time()},
            handle,
        )
    return True


def _job_from_meta(meta_path: Path, state_dir: Path, route: str) -> Job | None:
    try:
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if metadata.get("requested_model") != route:
        return None
    cache_key = metadata.get("cache_key")
    answer_file = metadata.get("answer_file")
    request_id = metadata.get("request_id")
    response_model = metadata.get("response_model")
    if not (
        isinstance(cache_key, str)
        and isinstance(answer_file, str)
        and isinstance(request_id, int)
        and isinstance(response_model, str)
    ):
        return None
    answer_path = Path(answer_file)
    if answer_path.is_file():
        return None
    stem = meta_path.name.removesuffix("-meta.json")
    prompt_path = state_dir / "pending" / f"{stem}-prompt.txt"
    schema_path = state_dir / "pending" / f"{stem}-schema.json"
    if not prompt_path.is_file() or not schema_path.is_file():
        return None
    images = metadata.get("images") or []
    image_paths = tuple(
        Path(item["path"])
        for item in images
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    )
    return Job(
        request_id=request_id,
        cache_key=cache_key,
        route=route,
        response_model=response_model,
        prompt_path=prompt_path,
        schema_path=schema_path,
        image_paths=image_paths,
        answer_path=answer_path,
        claim_path=state_dir / "claims" / f"{cache_key}.json",
    )


def claim_next_job(
    *,
    state_dir: Path,
    route: str,
    worker_id: str,
    claim_ttl_seconds: float,
) -> Job | None:
    for meta_path in sorted((state_dir / "pending").glob("req-*-meta.json")):
        job = _job_from_meta(meta_path, state_dir, route)
        if job is not None and _claim(job.claim_path, worker_id, claim_ttl_seconds):
            return job
    return None


def claim_capacity(
    *, state_dir: Path, capacity_group: str, worker_id: str, claim_ttl_seconds: float
) -> Path | None:
    """Lease one upstream capacity slot shared by clients using the same account/route."""
    safe_group = "".join(
        character if character.isalnum() or character in {"-", "_"} else "_"
        for character in capacity_group
    )
    path = state_dir / "capacity" / f"{safe_group}.json"
    return path if _claim(path, worker_id, claim_ttl_seconds) else None


def compose_prompt(job: Job) -> str:
    schema = job.schema_path.read_text(encoding="utf-8")
    prompt = job.prompt_path.read_text(encoding="utf-8")
    return (
        "You are an automatic model worker for Dendro Inspector. Inspect every attached "
        "image and follow the complete Dendro prompt below. Return exactly one JSON object "
        "matching the required schema. No Markdown fence, prose, commentary, or hidden "
        "reasoning. Do not invent an observation that is not visible. Uncertainty and "
        "abstention are valid.\n\n"
        f"## Required JSON Schema ({job.response_model})\n\n{schema}\n\n"
        f"## Dendro prompt\n\n{prompt}"
    )


def _retry_after(headers: Any) -> float | None:
    raw = headers.get("Retry-After") if headers is not None else None
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return None


class OpenCodeBackend:
    def __init__(self, *, executable: str, model: str, timeout_seconds: float) -> None:
        resolved = shutil.which(executable) or executable
        if not Path(resolved).is_file():
            raise WorkerError(f"OpenCode executable not found: {executable}")
        self._executable = resolved
        self._model = model
        self._timeout = timeout_seconds

    def generate(self, job: Job) -> WorkerResult:
        command = [
            self._executable,
            "run",
            "--pure",
            "--format",
            "json",
            "--model",
            self._model,
        ]
        for image_path in job.image_paths:
            command.extend(("--file", str(image_path)))
        completed = subprocess.run(
            command,
            input=compose_prompt(job),
            text=True,
            encoding="utf-8",
            errors="strict",
            env=utf8_subprocess_environment(),
            capture_output=True,
            timeout=self._timeout,
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()[-2000:]
            if "429" in detail or "rate limit" in detail.lower():
                raise WorkerRateLimitError(f"OpenCode route rate-limited: {detail}")
            raise WorkerError(f"OpenCode exited {completed.returncode}: {detail}")

        text_parts: list[str] = []
        finish: dict[str, Any] = {}
        for line in completed.stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "text":
                part = event.get("part") or {}
                text = part.get("text")
                if isinstance(text, str):
                    text_parts.append(text)
            elif event.get("type") == "step_finish":
                part = event.get("part") or {}
                finish = {
                    "session_id": event.get("sessionID"),
                    "tokens": part.get("tokens"),
                    "cost": part.get("cost"),
                    "reason": part.get("reason"),
                }
        answer = "".join(text_parts).strip()
        if not answer:
            raise WorkerError("OpenCode returned no text event")
        return WorkerResult(
            text=answer,
            upstream={"provider": "opencode", "model": self._model, **finish},
        )


class OpenRouterBackend:
    def __init__(
        self,
        *,
        model: str,
        api_key_env: str,
        base_url: str,
        timeout_seconds: float,
    ) -> None:
        self._model = model
        self._api_key_env = api_key_env
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds

    def generate(self, job: Job) -> WorkerResult:
        key = os.environ.get(self._api_key_env)
        if not key:
            raise WorkerError(
                f"credential environment variable is not populated: {self._api_key_env}"
            )
        content: list[dict[str, Any]] = [{"type": "text", "text": compose_prompt(job)}]
        for image_path in job.image_paths:
            media_type = "image/png" if image_path.suffix.lower() == ".png" else "image/jpeg"
            encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{media_type};base64,{encoded}"},
                }
            )
        schema = json.loads(job.schema_path.read_text(encoding="utf-8"))
        request_body = json.dumps(
            {
                "model": self._model,
                "messages": [{"role": "user", "content": content}],
                "temperature": 0,
                "max_tokens": 8192,
                "stream": False,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": job.response_model,
                        "schema": schema,
                        "strict": True,
                    },
                },
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self._base_url}/chat/completions",
            data=request_body,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[-2000:]
            if exc.code == 429:
                raise WorkerRateLimitError(
                    f"OpenRouter route rate-limited: {detail}",
                    retry_after=_retry_after(exc.headers),
                ) from exc
            raise WorkerError(f"OpenRouter request failed ({exc.code}): {detail}") from exc
        except (TimeoutError, urllib.error.URLError) as exc:
            raise WorkerError(f"OpenRouter transport failed: {exc}") from exc

        choices = payload.get("choices") or []
        text = (choices[0].get("message") or {}).get("content") if choices else None
        if not isinstance(text, str) or not text.strip():
            raise WorkerError("OpenRouter returned no text content")
        return WorkerResult(
            text=text.strip(),
            upstream={
                "provider": "openrouter",
                "model": payload.get("model", self._model),
                "request_id": payload.get("id"),
                "usage": payload.get("usage"),
            },
        )


class CodexBackend:
    def __init__(self, *, executable: str, model: str, timeout_seconds: float) -> None:
        resolved = shutil.which(executable) or executable
        if not Path(resolved).is_file():
            raise WorkerError(f"Codex executable not found: {executable}")
        self._executable = resolved
        self._model = model
        self._timeout = timeout_seconds

    def generate(self, job: Job) -> WorkerResult:
        output_path = job.answer_path.with_name(f".{job.cache_key}.codex-output.json")
        schema_path = job.answer_path.with_name(f".{job.cache_key}.codex-schema.json")
        schema = json.loads(job.schema_path.read_text(encoding="utf-8"))
        atomic_write_text(
            schema_path,
            json.dumps(to_strict_openai_schema(schema), separators=(",", ":")),
        )
        command = [
            self._executable,
            "exec",
            "--ephemeral",
            "--sandbox",
            "read-only",
            "--color",
            "never",
            "--json",
            "--model",
            self._model,
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            str(output_path),
        ]
        for image_path in job.image_paths:
            command.extend(("--image", str(image_path)))
        command.append("-")
        try:
            completed = subprocess.run(
                command,
                input=compose_prompt(job),
                text=True,
                encoding="utf-8",
                errors="strict",
                env=utf8_subprocess_environment(),
                capture_output=True,
                timeout=self._timeout,
                check=False,
            )
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout).strip()[-2000:]
                if "429" in detail or "rate limit" in detail.lower():
                    raise WorkerRateLimitError(f"Codex route rate-limited: {detail}")
                raise WorkerError(f"Codex exited {completed.returncode}: {detail}")
            if not output_path.is_file():
                raise WorkerError("Codex wrote no output-last-message file")
            text = output_path.read_text(encoding="utf-8").strip()
        finally:
            output_path.unlink(missing_ok=True)
            schema_path.unlink(missing_ok=True)
        if not text:
            raise WorkerError("Codex returned an empty last message")
        return WorkerResult(
            text=text,
            upstream={
                "provider": "codex",
                "model": self._model,
                "usage": codex_usage(completed.stdout),
            },
        )


class ClaudeBackend:
    """Use authenticated Claude Code as an isolated, read-only review transport."""

    _SYSTEM_PROMPT = (
        "You are a read-only structured-output worker for Dendro Inspector. Use only the "
        "Read tool. Never edit a file, run a command, delegate, access the network through "
        "a tool, or change repository state. Inspect the named local images, follow the "
        "complete Dendro prompt, and return exactly one object matching the supplied JSON "
        "Schema. Do not invent observations. Uncertainty and abstention are valid."
    )

    def __init__(self, *, executable: str, model: str, timeout_seconds: float) -> None:
        resolved = shutil.which(executable) or executable
        if not Path(resolved).is_file():
            raise WorkerError(f"Claude executable not found: {executable}")
        self._executable = resolved
        self._model = model
        self._timeout = timeout_seconds

    def generate(self, job: Job) -> WorkerResult:
        state_dir = job.prompt_path.parents[1]
        schema = json.loads(job.schema_path.read_text(encoding="utf-8"))
        image_list = "\n".join(f"- {path}" for path in job.image_paths) or "- none"
        task = (
            f"Read the complete Dendro prompt from {job.prompt_path}.\n"
            f"Read the required JSON Schema from {job.schema_path}.\n"
            f"Inspect these image files with the Read tool:\n{image_list}\n"
            "Return only the required structured object."
        )
        command = [
            self._executable,
            "--print",
            "--output-format",
            "json",
            "--no-session-persistence",
            "--disable-slash-commands",
            "--strict-mcp-config",
            "--tools",
            "Read",
            "--allowedTools",
            "Read",
            "--permission-mode",
            "auto",
            "--model",
            self._model,
            "--system-prompt",
            self._SYSTEM_PROMPT,
            "--json-schema",
            json.dumps(schema, separators=(",", ":")),
            task,
        ]
        completed = subprocess.run(
            command,
            cwd=state_dir,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="strict",
            env=utf8_subprocess_environment(),
            capture_output=True,
            timeout=self._timeout,
            check=False,
        )
        detail = (completed.stderr or completed.stdout).strip()[-2000:]
        if completed.returncode != 0:
            if "429" in detail or "rate limit" in detail.lower():
                raise WorkerRateLimitError(f"Claude route rate-limited: {detail}")
            raise WorkerError(f"Claude exited {completed.returncode}: {detail}")
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise WorkerError(f"Claude returned invalid JSON envelope: {detail}") from exc
        if payload.get("is_error") or payload.get("subtype") == "error":
            message = payload.get("result") or payload.get("error") or "unknown Claude error"
            if "429" in str(message) or "rate limit" in str(message).lower():
                raise WorkerRateLimitError(f"Claude route rate-limited: {message}")
            raise WorkerError(f"Claude returned an error result: {message}")
        structured = payload.get("structured_output")
        if isinstance(structured, dict):
            text = json.dumps(structured, separators=(",", ":"))
        else:
            result = payload.get("result")
            if not isinstance(result, str) or not result.strip():
                raise WorkerError("Claude returned no structured_output or result text")
            text = result.strip()
        return WorkerResult(
            text=text,
            upstream={
                "provider": "claude-code",
                "model": self._model,
                "session_id": payload.get("session_id"),
                "model_usage": payload.get("modelUsage"),
                "usage": payload.get("usage"),
                "total_cost_usd": payload.get("total_cost_usd"),
                "duration_ms": payload.get("duration_ms"),
            },
        )


class ClineBackend:
    """Use Cline as a CLI agent transport while Ox remains the upstream model."""

    _SYSTEM_PROMPT = (
        "You are a read-only structured-output worker for Dendro Inspector. Use file/image "
        "reading only. Never edit a file, run a command, delegate, or change repository "
        "state. Inspect the named local image files, follow the named Dendro prompt, and "
        "return exactly one JSON object matching the named schema. No Markdown fence, prose, "
        "commentary, or hidden reasoning. Uncertainty and abstention are valid."
    )

    def __init__(
        self,
        *,
        executable: str,
        provider: str,
        model: str,
        timeout_seconds: float,
    ) -> None:
        resolved = shutil.which(executable) or executable
        if not Path(resolved).is_file():
            raise WorkerError(f"Cline executable not found: {executable}")
        self._executable = resolved
        self._provider = provider
        self._model = model
        self._timeout = timeout_seconds

    def generate(self, job: Job) -> WorkerResult:
        state_dir = job.prompt_path.parents[1]
        image_list = "\n".join(f"- {path}" for path in job.image_paths) or "- none"
        task = (
            f"Read the complete Dendro prompt from {job.prompt_path}.\n"
            f"Read the required JSON Schema from {job.schema_path}.\n"
            f"Inspect these image files with the read tool:\n{image_list}\n"
            "Return only the required JSON object."
        )
        command = [
            self._executable,
            "--json",
            "--auto-approve",
            "true",
            "--cwd",
            str(state_dir),
            "--provider",
            self._provider,
            "--model",
            self._model,
            "--timeout",
            str(max(1, int(self._timeout))),
            "--system",
            self._SYSTEM_PROMPT,
            task,
        ]
        completed = subprocess.run(
            command,
            text=True,
            encoding="utf-8",
            errors="strict",
            env=utf8_subprocess_environment(),
            capture_output=True,
            timeout=self._timeout + 30,
            check=False,
        )
        run_result: dict[str, Any] | None = None
        for line in completed.stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "run_result":
                run_result = event
        if run_result is None:
            detail = (completed.stderr or completed.stdout).strip()[-2000:]
            raise WorkerError(f"Cline returned no run_result event: {detail}")
        text = run_result.get("text")
        finish_reason = run_result.get("finishReason")
        if completed.returncode != 0 or finish_reason == "error":
            detail = text if isinstance(text, str) else "unknown Cline error"
            if "429" in detail or "rate limit" in detail.lower():
                raise WorkerRateLimitError(f"Cline route rate-limited: {detail}")
            raise WorkerError(f"Cline exited {completed.returncode}: {detail}")
        if not isinstance(text, str) or not text.strip():
            raise WorkerError("Cline returned an empty run_result text")
        return WorkerResult(
            text=text.strip(),
            upstream={
                "provider": f"cline:{self._provider}",
                "model": self._model,
                "finish_reason": finish_reason,
                "iterations": run_result.get("iterations"),
                "usage": run_result.get("aggregateUsage") or run_result.get("usage"),
                "duration_ms": run_result.get("durationMs"),
            },
        )


def _record_failure(state_dir: Path, job: Job, worker_id: str, error: Exception) -> None:
    payload = {
        "worker_id": worker_id,
        "request_id": job.request_id,
        "cache_key": job.cache_key,
        "failed_at": time.time(),
        "error_type": type(error).__name__,
        "message": str(error)[:2000],
    }
    atomic_write_text(
        state_dir / "failures" / f"{job.cache_key}-{worker_id}-{int(time.time())}.json",
        json.dumps(payload, indent=2),
    )


def publish(job: Job, *, worker_id: str, result: WorkerResult, started: float) -> None:
    metadata = {
        "worker_id": worker_id,
        "route": job.route,
        "request_id": job.request_id,
        "cache_key": job.cache_key,
        "started_at": started,
        "finished_at": time.time(),
        "upstream": result.upstream,
    }
    atomic_write_text(job.answer_path.with_suffix(".meta.json"), json.dumps(metadata, indent=2))
    atomic_write_text(job.answer_path, result.text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, default=Path(".bridge"))
    parser.add_argument("--worker-id", required=True)
    parser.add_argument(
        "--capacity-group",
        help="workers sharing one upstream account/limit use the same capacity group",
    )
    parser.add_argument("--route", default="ox-factory")
    parser.add_argument(
        "--backend",
        choices=("opencode", "openrouter", "codex", "claude", "cline"),
        required=True,
    )
    parser.add_argument("--model", required=True)
    parser.add_argument("--executable", default="opencode")
    parser.add_argument("--provider", default="cline")
    parser.add_argument("--api-key-env", default="OPENROUTER_API_KEY")
    parser.add_argument("--base-url", default=DEFAULT_OPENROUTER_BASE_URL)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--claim-ttl", type=float, default=DEFAULT_CLAIM_TTL_SECONDS)
    parser.add_argument("--failure-cooldown", type=float, default=DEFAULT_FAILURE_COOLDOWN_SECONDS)
    parser.add_argument(
        "--rate-limit-cooldown", type=float, default=DEFAULT_RATE_LIMIT_COOLDOWN_SECONDS
    )
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    state_dir = args.state_dir.resolve()
    for name in ("pending", "answers", "claims", "capacity", "failures"):
        (state_dir / name).mkdir(parents=True, exist_ok=True)
    load_env_file(args.env_file.resolve())
    if args.backend == "opencode":
        backend: (
            OpenCodeBackend | OpenRouterBackend | CodexBackend | ClaudeBackend | ClineBackend
        ) = OpenCodeBackend(
            executable=args.executable,
            model=args.model,
            timeout_seconds=args.timeout,
        )
    elif args.backend == "openrouter":
        backend = OpenRouterBackend(
            model=args.model,
            api_key_env=args.api_key_env,
            base_url=args.base_url,
            timeout_seconds=args.timeout,
        )
    elif args.backend == "codex":
        backend = CodexBackend(
            executable=args.executable,
            model=args.model,
            timeout_seconds=args.timeout,
        )
    elif args.backend == "claude":
        backend = ClaudeBackend(
            executable=args.executable,
            model=args.model,
            timeout_seconds=args.timeout,
        )
    else:
        backend = ClineBackend(
            executable=args.executable,
            provider=args.provider,
            model=args.model,
            timeout_seconds=args.timeout,
        )

    print(
        f"[worker:{args.worker_id}] watching {state_dir} route={args.route} "
        f"backend={args.backend} model={args.model}",
        flush=True,
    )
    while True:
        capacity_path = claim_capacity(
            state_dir=state_dir,
            capacity_group=args.capacity_group or args.worker_id,
            worker_id=args.worker_id,
            claim_ttl_seconds=args.claim_ttl,
        )
        if capacity_path is None:
            if args.once:
                return 2
            time.sleep(POLL_SECONDS)
            continue
        job = claim_next_job(
            state_dir=state_dir,
            route=args.route,
            worker_id=args.worker_id,
            claim_ttl_seconds=args.claim_ttl,
        )
        if job is None:
            capacity_path.unlink(missing_ok=True)
            if args.once:
                return 2
            time.sleep(POLL_SECONDS)
            continue
        started = time.time()
        print(f"[worker:{args.worker_id}] claimed req-{job.request_id:03d}", flush=True)
        try:
            result = backend.generate(job)
            publish(job, worker_id=args.worker_id, result=result, started=started)
        except WorkerRateLimitError as exc:
            _record_failure(state_dir, job, args.worker_id, exc)
            job.claim_path.unlink(missing_ok=True)
            capacity_path.unlink(missing_ok=True)
            cooldown = exc.retry_after or args.rate_limit_cooldown
            print(f"[worker:{args.worker_id}] rate-limited; cooldown={cooldown}s", flush=True)
            if args.once:
                return 3
            time.sleep(cooldown)
            continue
        except (OSError, UnicodeError, subprocess.SubprocessError, WorkerError) as exc:
            _record_failure(state_dir, job, args.worker_id, exc)
            job.claim_path.unlink(missing_ok=True)
            capacity_path.unlink(missing_ok=True)
            print(f"[worker:{args.worker_id}] failed: {exc}", file=sys.stderr, flush=True)
            if args.once:
                return 4
            time.sleep(args.failure_cooldown)
            continue
        capacity_path.unlink(missing_ok=True)
        print(f"[worker:{args.worker_id}] published {job.cache_key}", flush=True)
        if args.once:
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
