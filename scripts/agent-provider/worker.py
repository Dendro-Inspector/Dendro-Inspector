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
import re
import shutil
import subprocess
import sys
import tempfile
import threading
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


class WorkerTerminalError(WorkerError):
    """The current request cannot succeed by retrying the same upstream state."""


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
    terminal_path = state_dir / "failures" / f"req-{request_id:03d}-terminal.json"
    if terminal_path.is_file():
        try:
            terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            terminal = {}
        if terminal.get("cache_key") == cache_key:
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


def describe_images(paths: tuple[Path, ...]) -> str:
    """``WxH bytes`` for each image handed to the agent, so logs show what it received."""
    parts = []
    for path in paths:
        try:
            size = path.stat().st_size
        except OSError:
            parts.append(f"{path.name}=missing")
            continue
        dims = "?x?"
        with contextlib.suppress(Exception):
            from PIL import Image

            with Image.open(path) as image:
                dims = f"{image.width}x{image.height}"
        parts.append(f"{path.name}={dims} {size}B")
    return ", ".join(parts)


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
    def __init__(
        self,
        *,
        executable: str,
        model: str,
        timeout_seconds: float,
        enable_features: tuple[str, ...] = (),
    ) -> None:
        resolved = shutil.which(executable) or executable
        if not Path(resolved).is_file():
            raise WorkerError(f"Codex executable not found: {executable}")
        self._executable = resolved
        self._model = model
        self._timeout = timeout_seconds
        # e.g. image_detail_original: send attached images without Codex's own downscaling,
        # on models that support it.
        self._enable_features = enable_features

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
        for feature in self._enable_features:
            command.extend(("--enable", feature))
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
                # With --json the reason (e.g. model_not_found) is an event on stdout, while
                # stderr holds only progress lines, so keep the tail of both.
                detail = f"{completed.stderr.strip()}\n{completed.stdout.strip()}".strip()[-2000:]
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
            # Claude Code prints its JSON result on a failed run too; read all of it, since
            # the tail kept for the log can cut the envelope in half.
            terminal = _claude_terminal_quota_error(detail, _claude_envelope(completed.stdout))
            if terminal is not None:
                raise terminal
            if "429" in detail or "rate limit" in detail.lower():
                raise WorkerRateLimitError(f"Claude route rate-limited: {detail}")
            raise WorkerError(f"Claude exited {completed.returncode}: {detail}")
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise WorkerError(f"Claude returned invalid JSON envelope: {detail}") from exc
        if payload.get("is_error") or payload.get("subtype") == "error":
            message = payload.get("result") or payload.get("error") or "unknown Claude error"
            terminal = _claude_terminal_quota_error(
                str(message), payload if isinstance(payload, dict) else None
            )
            if terminal is not None:
                raise terminal
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


#: Every Vibe 3.1.3 built-in tool that takes a permission, apart from read_file.
_VIBE_REFUSED_TOOLS = (
    "ask_user_question",
    "bash",
    "bash_log_file",
    "bash_output",
    "bash_sessions",
    "bash_stdin",
    "edit",
    "git_bash",
    "git_bash_log_file",
    "git_bash_output",
    "git_bash_sessions",
    "git_bash_stdin",
    "powershell",
    "powershell_log_file",
    "powershell_output",
    "powershell_sessions",
    "powershell_stdin",
    "skill",
    "todo",
    "web_fetch",
    "web_search",
    "write_file",
)

#: Settings for the Vibe home a Vibe worker owns, rewritten at every worker start. In headless
#: mode Vibe 3.1.3 still offers write_file, edit, bash, skill and run_typescript whatever
#: ``enabled_tools`` and ``disabled_tools`` say; a ``never`` permission is what refuses a
#: call, so every tool but read_file gets one. run_typescript takes no permission; with
#: connectors and MCP off it has no tool of its own to reach.
_VIBE_CONFIG = (
    "# Written by Dendro Inspector's scripts/agent-provider/worker.py when it starts.\n"
    "enable_telemetry = false\n"
    "enable_update_checks = false\n"
    "enable_auto_update = false\n"
    "enable_notifications = false\n"
    "enable_pr_monitoring = false\n"
    "enable_connectors = false\n"
    "enable_mcp = false\n"
    "enable_subagents = false\n"
    "enable_background_processes = false\n"
    "enable_auto_approve = false\n"
    "enable_file_autocomplete = false\n"
    "include_project_context = false\n"
    "include_commit_signature = false\n"
    'outside_workdir = "deny"\n'
    'enabled_tools = ["read_file"]\n'
    'disabled_tools = ["bash", "write_file", "edit", "skill", "run_typescript", '
    '"search_tool_functions"]\n'
    "\n[session_logging]\nenabled = false\n"
    '\n[tools.read_file]\noutside_workdir = "deny"\n'
    + "".join(f'\n[tools.{tool}]\npermission = "never"\n' for tool in _VIBE_REFUSED_TOOLS)
)

#: How a Vibe failure reads when it is a short rate limit, and when no retry can succeed
#: (no key, a revoked key, an account without credit). Vibe 3.1.3 without a key says
#: "Missing MISTRAL_API_KEY environment variable for mistral provider" and exits 1.
_VIBE_RATE_LIMIT_TEXT = re.compile(r"\b429\b|rate[ _-]?limit", re.IGNORECASE)
_VIBE_TERMINAL_TEXT = re.compile(
    r"\b40[123]\b|unauthori[sz]ed|forbidden|invalid api[ _]key|missing \S*api[ _]key"
    r"|no api[ _]key|insufficient (?:credit|fund|balance)|payment required",
    re.IGNORECASE,
)


def _vibe_output(stdout: str) -> tuple[str, frozenset[str]]:
    """The last assistant text in Vibe's ``--output json`` array, and the file names its
    ``read_file`` calls opened successfully."""
    try:
        entries = json.loads(stdout)
    except ValueError as exc:
        raise WorkerError(f"Vibe printed no JSON message array: {stdout.strip()[-500:]}") from exc
    if not isinstance(entries, list):
        raise WorkerError("Vibe's JSON output is not a message array")
    answer = ""
    opened: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if entry.get("type") == "message" and entry.get("role") == "assistant":
            text = "".join(
                part["text"]
                for part in entry.get("content") or []
                if isinstance(part, dict)
                and part.get("type") == "text"
                and isinstance(part.get("text"), str)
            ).strip()
            if text:
                answer = text
        elif entry.get("type") == "effect":
            detail = entry.get("detail") or {}
            state = entry.get("state") or {}
            tool = detail.get("toolName") if isinstance(detail, dict) else None
            if (
                isinstance(tool, str)
                and tool.endswith("read_file")
                and isinstance(state, dict)
                and state.get("status") == "completed"
            ):
                path = (detail.get("input") or {}).get("filePath")
                if isinstance(path, str):
                    opened.add(Path(path).name)
    return answer, frozenset(opened)


#: The fastest thinking each Vibe 3.1.3 model alias accepts (its own thinking_levels).
_VIBE_FASTEST_THINKING = {"mistral-large-4-0": "low", "mistral-medium-3.5": "off"}
_PIL_FORMATS = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".webp": "WEBP"}


def vibe_thinking(model: str, requested: str | None) -> str | None:
    """The level to pass as ``--thinking``, or None to keep the model's own default."""
    if not requested or requested == "default":
        return None
    if requested == "fastest":
        return _VIBE_FASTEST_THINKING.get(model)
    return requested


def copy_photo(source: Path, target: Path, max_edge: int) -> None:
    """Copy a photograph, downscaled to ``max_edge`` on its longest side when larger.

    The format follows the file name, so Vibe reads the copy as the type it is named. Without
    Pillow, or for a format it cannot write, the photograph is copied as received.
    """
    form = _PIL_FORMATS.get(source.suffix.lower())
    if max_edge > 0 and form:
        try:
            from PIL import Image, ImageOps

            with Image.open(source) as opened:
                if max(opened.size) > max_edge:
                    image = ImageOps.exif_transpose(opened)
                    image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
                    if form == "JPEG":
                        image.convert("RGB").save(target, form, quality=90)
                    else:
                        image.save(target, form)
                    return
        except (ImportError, OSError, ValueError):
            pass
    shutil.copyfile(source, target)


def json_object_text(text: str) -> str:
    """The JSON object in a model's answer, without a Markdown fence or prose around it.

    Vibe's last message is free text: with low thinking Mistral Large 4 sometimes wraps the
    object in ```json ... ```, which the bridge's Anthropic dialect passes on unparsed (oak-9,
    2026-11-19: three fenced packets, the case failed). Only the wrapping goes; the object is
    unchanged and still meets the node's Pydantic contract. Text with no parseable object is
    returned as it was, for the repair retry to see.
    """
    stripped = text.strip()
    try:
        json.loads(stripped)
        return stripped
    except ValueError:
        pass
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", stripped, re.DOTALL)
    candidates = [fenced.group(1)] if fenced else []
    start, end = stripped.find("{"), stripped.rfind("}")
    if 0 <= start < end:
        candidates.append(stripped[start : end + 1])
    for candidate in candidates:
        try:
            if isinstance(json.loads(candidate), dict):
                return candidate
        except ValueError:
            continue
    return stripped


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


class VibeBackend:
    """Use Mistral Vibe's headless mode as a read-only vision transport.

    Vibe has no image flag, and an ``@path`` in a headless prompt reaches the model as plain
    text. Its ``read_file`` tool returns a photograph as an image, which Vibe's Mistral backend
    sends on as an image part. So the worker copies the job's photographs into an empty folder,
    confines Vibe to it, and asks it to open each one. An answer given without opening every
    photograph is refused: it cannot be about them.

    The worker owns a Vibe root: ``<root>/.vibe`` is the Vibe home and its settings, and each
    job runs in ``<root>/jobs/<cache key>``. Vibe looks for project settings in the job folder
    and its parents up to the home's parent, which is the root, so a ``.vibe`` folder elsewhere
    on the machine cannot loosen the settings.
    """

    _SYSTEM_PROMPT = (
        "You are a read-only structured-output worker for Dendro Inspector. Use only the "
        "read_file tool, and only on the photographs the request names. Never edit a file, "
        "run a command, delegate, or reach the network through a tool. Open every named "
        "photograph, follow the complete Dendro prompt, and return exactly one JSON object "
        "matching the required schema, with no Markdown fence, prose or commentary. Do not "
        "invent observations. Uncertainty and abstention are valid."
    )

    def __init__(
        self,
        *,
        executable: str,
        model: str,
        timeout_seconds: float,
        root: Path,
        max_price: float | None = None,
        thinking: str | None = None,
        max_edge: int = 0,
    ) -> None:
        resolved = shutil.which(executable) or executable
        if not Path(resolved).is_file():
            raise WorkerError(f"Vibe executable not found: {executable}")
        self._executable = resolved
        self._model = model
        self._timeout = timeout_seconds
        self._max_price = max_price
        self._thinking = vibe_thinking(model, thinking)
        self._max_edge = max_edge
        self._home = root / ".vibe"
        self._jobs = root / "jobs"
        self._home.mkdir(parents=True, exist_ok=True)
        self._jobs.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self._home / "config.toml", _VIBE_CONFIG)
        self._system_prompt = root / "system-prompt.md"
        atomic_write_text(self._system_prompt, self._SYSTEM_PROMPT)

    def _prompt(self, job: Job, photos: list[Path]) -> str:
        listing = "\n".join(f"- {path}" for path in photos) or "- none"
        return (
            "The photographs for this request are files in your working directory. Nothing "
            "is attached to this message: open every one of them with read_file before you "
            f"answer.\n{listing}\n\n{compose_prompt(job)}"
        )

    def generate(self, job: Job) -> WorkerResult:
        workdir = self._jobs / job.cache_key
        export_dir = self._jobs / f"{job.cache_key}.export"
        for stale in (workdir, export_dir):
            shutil.rmtree(stale, ignore_errors=True)
        workdir.mkdir(parents=True)
        photos = []
        for image_path in job.image_paths:
            copy = workdir / image_path.name
            copy_photo(image_path, copy, self._max_edge)
            photos.append(copy)
        command = [
            self._executable,
            "-p",
            "--output",
            "json",
            "--trust",
            "--workdir",
            str(workdir),
            "--model",
            self._model,
            # One turn per photograph if the model opens them one at a time, plus the answer
            # and a repair of a refused tool call.
            "--max-turns",
            str(len(photos) + 4),
            # Stop Vibe itself before the subprocess timeout, so it still writes export.json.
            "--time-limit",
            str(max(30, int(self._timeout) - 30)),
            "--system-prompt-file",
            str(self._system_prompt),
            "--output-dir",
            str(export_dir),
            "--enabled-tools",
            "read_file",
        ]
        if self._max_price is not None:
            command.extend(("--max-price", f"{self._max_price:g}"))
        if self._thinking:
            command.extend(("--thinking", self._thinking))
        environment = utf8_subprocess_environment()
        environment["VIBE_HOME"] = str(self._home)
        try:
            completed = subprocess.run(
                command,
                input=self._prompt(job, photos),
                cwd=workdir,
                text=True,
                encoding="utf-8",
                errors="strict",
                env=environment,
                capture_output=True,
                timeout=self._timeout,
                check=False,
            )
            export = _read_json_object(export_dir / "export.json")
        finally:
            for scratch in (workdir, export_dir):
                shutil.rmtree(scratch, ignore_errors=True)

        error = export.get("error")
        if completed.returncode != 0:
            detail = " ".join(
                part
                for part in (
                    json.dumps(error) if error else "",
                    str(export.get("stop_reason") or ""),
                    completed.stderr.strip()[-1500:],
                )
                if part
            )[-2000:]
            if _VIBE_RATE_LIMIT_TEXT.search(detail):
                raise WorkerRateLimitError(f"Vibe route rate-limited: {detail}")
            if _VIBE_TERMINAL_TEXT.search(detail):
                raise WorkerTerminalError(f"Vibe route cannot authenticate or pay: {detail}")
            raise WorkerError(f"Vibe exited {completed.returncode}: {detail}")
        answer, opened = _vibe_output(completed.stdout)
        answer = json_object_text(answer)
        if not answer:
            raise WorkerError("Vibe finished without an answer")
        unopened = sorted({photo.name for photo in photos} - opened)
        if unopened:
            raise WorkerError(f"Vibe answered without opening {', '.join(unopened)}")
        return WorkerResult(
            text=answer,
            upstream={
                "provider": "mistral-vibe",
                "model": self._model,
                "vibe_version": export.get("vibe_version"),
                "session_id": export.get("session_id"),
                "usage": export.get("usage"),
                "cost_usd": export.get("cost_usd"),
                "steps": export.get("steps"),
                "outcome": export.get("outcome"),
                "thinking": self._thinking or "model default",
                "image_max_edge": self._max_edge or None,
                "images_opened": len(photos),
                "warnings": export.get("warnings"),
            },
        )


#: How Claude Code words an exhausted subscription or spend limit. It arrives with HTTP 429,
#: like a short rate limit, but no retry can succeed before the reset, which may be days
#: away: "You've hit your 5-hour limit · resets 8:20am (UTC)", "You've hit your weekly
#: limit · resets Nov 17, 2am (UTC)". The limit's own name varies, so the phrase around it
#: is matched instead of a list of names.
_CLAUDE_QUOTA_TEXT = re.compile(
    r"monthly spend limit|session limit resets|usage[ _]limit[ _]reached"
    r"|hit your [^\"\n]{0,60}?limit",
    re.IGNORECASE,
)
#: The same condition as a machine-readable field of the JSON result, where present.
_CLAUDE_QUOTA_API_ERRORS = frozenset({"usage_limit_reached"})


def _claude_envelope(stdout: str) -> dict[str, Any] | None:
    """The JSON result object Claude Code printed, or None when stdout is not one."""
    try:
        payload = json.loads(stdout)
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _claude_terminal_quota_error(
    detail: str, envelope: dict[str, Any] | None = None
) -> WorkerTerminalError | None:
    """Classify account/session exhaustion separately from a short rate limit.

    ``envelope`` is Claude Code's JSON result when it could be parsed. Its ``api_error``
    field settles the question on its own; otherwise the wording decides, read from the
    result text when there is one and from the raw ``detail`` when there is not.
    """
    result = envelope.get("result") if envelope is not None else None
    reported = result.strip() if isinstance(result, str) else ""
    structured = envelope is not None and envelope.get("api_error") in _CLAUDE_QUOTA_API_ERRORS
    if not structured and not _CLAUDE_QUOTA_TEXT.search(reported or detail):
        return None
    message = "Claude route quota exhausted; retry after the account spend or session limit resets."
    if reported:
        # Which limit and when it resets: the only part an operator can act on.
        message += f" Claude reported: {reported[:300]}"
    return WorkerTerminalError(message)


def _record_failure(state_dir: Path, job: Job, worker_id: str, error: Exception) -> None:
    payload = _failure_payload(job, worker_id, error)
    atomic_write_text(
        state_dir / "failures" / f"{job.cache_key}-{worker_id}-{int(time.time())}.json",
        json.dumps(payload, indent=2),
    )


def _failure_payload(job: Job, worker_id: str, error: Exception) -> dict[str, Any]:
    return {
        "worker_id": worker_id,
        "request_id": job.request_id,
        "cache_key": job.cache_key,
        "failed_at": time.time(),
        "error_type": type(error).__name__,
        "message": str(error)[:2000],
    }


def record_terminal_failure(
    state_dir: Path, job: Job, worker_id: str, error: WorkerTerminalError
) -> None:
    """Record a request-scoped marker that stops worker and bridge retry loops."""
    payload = _failure_payload(job, worker_id, error)
    atomic_write_text(
        state_dir / "failures" / f"{job.cache_key}-{worker_id}-{int(time.time())}.json",
        json.dumps(payload, indent=2),
    )
    atomic_write_text(
        state_dir / "failures" / f"req-{job.request_id:03d}-terminal.json",
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
        choices=("opencode", "openrouter", "codex", "claude", "cline", "vibe"),
        required=True,
    )
    parser.add_argument(
        "--vibe-root",
        type=Path,
        help="folder the Vibe worker owns: its Vibe home and job folders "
        "(default: <temp>/dendro-vibe-<worker id>)",
    )
    parser.add_argument(
        "--vibe-max-price",
        type=float,
        metavar="DOLLARS",
        help="stop one Vibe call once its estimated cost passes this many dollars",
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
    parser.add_argument(
        "--codex-enable",
        action="append",
        default=[],
        metavar="FEATURE",
        help="Codex feature to enable (repeatable), e.g. image_detail_original.",
    )
    parser.add_argument(
        "--vibe-thinking",
        metavar="LEVEL",
        help="Vibe thinking level: 'fastest' (the lowest the model has: low for "
        "mistral-large-4-0, off for mistral-medium-3.5), 'default' (the model's own), or a "
        "level Vibe passes on as written (off, low, medium, high)",
    )
    parser.add_argument(
        "--vibe-max-edge",
        type=int,
        default=0,
        metavar="PX",
        help="downscale the copies Vibe opens to this longest edge (0 = send as received)",
    )
    parser.add_argument(
        "--parallel",
        type=int,
        default=1,
        metavar="N",
        help="run N worker loops in this process, each with its own id <worker-id>-<i>, "
        "capacity group <group>-<i> and, for Vibe, root <vibe-root>/w<i>",
    )
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.parallel > 1 and args.once:
        parser.error("--once runs a single worker loop; drop --parallel")

    state_dir = args.state_dir.resolve()
    for name in ("pending", "answers", "claims", "capacity", "failures"):
        (state_dir / name).mkdir(parents=True, exist_ok=True)
    load_env_file(args.env_file.resolve())
    group = args.capacity_group or args.worker_id
    if args.parallel <= 1:
        return run_worker(args, state_dir, args.worker_id, group, make_backend(args, 0))
    threads = []
    for index in range(1, args.parallel + 1):
        worker_id = f"{args.worker_id}-{index}"
        thread = threading.Thread(
            target=run_worker,
            args=(args, state_dir, worker_id, f"{group}-{index}", make_backend(args, index)),
            name=worker_id,
            daemon=True,
        )
        thread.start()
        threads.append(thread)
    for thread in threads:
        thread.join()
    return 0


Backend = (
    OpenCodeBackend | OpenRouterBackend | CodexBackend | ClaudeBackend | ClineBackend | VibeBackend
)


def make_backend(args: argparse.Namespace, index: int) -> Backend:
    """The upstream for one worker loop; ``index`` is its number under --parallel, else 0."""
    if args.backend == "opencode":
        return OpenCodeBackend(
            executable=args.executable,
            model=args.model,
            timeout_seconds=args.timeout,
        )
    if args.backend == "openrouter":
        return OpenRouterBackend(
            model=args.model,
            api_key_env=args.api_key_env,
            base_url=args.base_url,
            timeout_seconds=args.timeout,
        )
    if args.backend == "codex":
        return CodexBackend(
            executable=args.executable,
            model=args.model,
            timeout_seconds=args.timeout,
            enable_features=tuple(args.codex_enable),
        )
    if args.backend == "claude":
        return ClaudeBackend(
            executable=args.executable,
            model=args.model,
            timeout_seconds=args.timeout,
        )
    if args.backend == "vibe":
        root = args.vibe_root or Path(tempfile.gettempdir()) / f"dendro-vibe-{args.worker_id}"
        if index:
            root = root / f"w{index}"
        return VibeBackend(
            executable=args.executable,
            model=args.model,
            timeout_seconds=args.timeout,
            root=root.resolve(),
            max_price=args.vibe_max_price,
            thinking=args.vibe_thinking,
            max_edge=args.vibe_max_edge,
        )
    return ClineBackend(
        executable=args.executable,
        provider=args.provider,
        model=args.model,
        timeout_seconds=args.timeout,
    )


def run_worker(
    args: argparse.Namespace,
    state_dir: Path,
    worker_id: str,
    capacity_group: str,
    backend: Backend,
) -> int:
    """Claim and answer requests for one route until stopped (or once, with --once)."""
    print(
        f"[worker:{worker_id}] watching {state_dir} route={args.route} "
        f"backend={args.backend} model={args.model}",
        flush=True,
    )
    while True:
        capacity_path = claim_capacity(
            state_dir=state_dir,
            capacity_group=capacity_group,
            worker_id=worker_id,
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
            worker_id=worker_id,
            claim_ttl_seconds=args.claim_ttl,
        )
        if job is None:
            capacity_path.unlink(missing_ok=True)
            if args.once:
                return 2
            time.sleep(POLL_SECONDS)
            continue
        started = time.time()
        print(
            f"[worker:{worker_id}] claimed req-{job.request_id:03d} "
            f"images: {describe_images(job.image_paths) or 'none'}",
            flush=True,
        )
        try:
            result = backend.generate(job)
            publish(job, worker_id=worker_id, result=result, started=started)
        except WorkerTerminalError as exc:
            record_terminal_failure(state_dir, job, worker_id, exc)
            job.claim_path.unlink(missing_ok=True)
            capacity_path.unlink(missing_ok=True)
            print(
                f"[worker:{worker_id}] terminal failure: {exc}",
                file=sys.stderr,
                flush=True,
            )
            if args.once:
                return 4
            continue
        except WorkerRateLimitError as exc:
            _record_failure(state_dir, job, worker_id, exc)
            job.claim_path.unlink(missing_ok=True)
            capacity_path.unlink(missing_ok=True)
            cooldown = exc.retry_after or args.rate_limit_cooldown
            print(f"[worker:{worker_id}] rate-limited; cooldown={cooldown}s", flush=True)
            if args.once:
                return 3
            time.sleep(cooldown)
            continue
        except (OSError, UnicodeError, subprocess.SubprocessError, WorkerError) as exc:
            _record_failure(state_dir, job, worker_id, exc)
            job.claim_path.unlink(missing_ok=True)
            capacity_path.unlink(missing_ok=True)
            print(f"[worker:{worker_id}] failed: {exc}", file=sys.stderr, flush=True)
            if args.once:
                return 4
            time.sleep(args.failure_cooldown)
            continue
        capacity_path.unlink(missing_ok=True)
        print(f"[worker:{worker_id}] published {job.cache_key}", flush=True)
        if args.once:
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
