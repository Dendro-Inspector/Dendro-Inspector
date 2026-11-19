from __future__ import annotations

import importlib.util
import json
import sys
import threading
import time
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest


def _load_script(name: str) -> ModuleType:
    path = Path(__file__).parents[2] / "scripts" / "agent-provider" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"dendro_test_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _pending_request(root: Path, *, route: str = "ox-factory") -> None:
    pending = root / "pending"
    answers = root / "answers"
    images = root / "images"
    for directory in (pending, answers, images):
        directory.mkdir(parents=True, exist_ok=True)
    (pending / "req-001-prompt.txt").write_text("inspect the image", encoding="utf-8")
    (pending / "req-001-schema.json").write_text(
        json.dumps({"type": "object", "properties": {"status": {"type": "string"}}}),
        encoding="utf-8",
    )
    image = images / "req-001-img1.jpg"
    image.write_bytes(b"not-a-real-image")
    (pending / "req-001-meta.json").write_text(
        json.dumps(
            {
                "request_id": 1,
                "requested_model": route,
                "response_model": "Probe",
                "cache_key": "abc123",
                "answer_file": str(answers / "abc123.json"),
                "images": [{"path": str(image)}],
            }
        ),
        encoding="utf-8",
    )


def test_first_worker_claims_job_exclusively(tmp_path: Path) -> None:
    worker = _load_script("worker")
    _pending_request(tmp_path)
    (tmp_path / "claims").mkdir()

    first = worker.claim_next_job(
        state_dir=tmp_path,
        route="ox-factory",
        worker_id="worker-a",
        claim_ttl_seconds=60,
    )
    second = worker.claim_next_job(
        state_dir=tmp_path,
        route="ox-factory",
        worker_id="worker-b",
        claim_ttl_seconds=60,
    )

    assert first is not None
    assert first.cache_key == "abc123"
    assert second is None


def test_worker_does_not_claim_another_route(tmp_path: Path) -> None:
    worker = _load_script("worker")
    _pending_request(tmp_path, route="sol-judge")
    (tmp_path / "claims").mkdir()

    job = worker.claim_next_job(
        state_dir=tmp_path,
        route="ox-factory",
        worker_id="ox-worker",
        claim_ttl_seconds=60,
    )

    assert job is None


def test_workers_sharing_account_have_one_capacity_lease(tmp_path: Path) -> None:
    worker = _load_script("worker")
    (tmp_path / "capacity").mkdir()

    first = worker.claim_capacity(
        state_dir=tmp_path,
        capacity_group="openrouter-account",
        worker_id="direct",
        claim_ttl_seconds=60,
    )
    second = worker.claim_capacity(
        state_dir=tmp_path,
        capacity_group="openrouter-account",
        worker_id="cline",
        claim_ttl_seconds=60,
    )

    assert first is not None
    assert second is None


def test_capacity_lease_released_between_checks_does_not_crash(tmp_path: Path, monkeypatch) -> None:
    """Two workers in one capacity group race on the same lease file; the loser must poll on."""
    worker = _load_script("worker")
    (tmp_path / "capacity").mkdir()
    lease = tmp_path / "capacity" / "claude-code.json"
    lease.write_text("{}", encoding="utf-8")

    real_stat = Path.stat
    real_exists = Path.exists

    def stale_exists(self: Path, **kwargs):
        return True if self == lease else real_exists(self, **kwargs)

    def vanishing_stat(self: Path, *args, **kwargs):
        if self == lease:
            raise FileNotFoundError(2, "lease released between checks")
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "exists", stale_exists)
    monkeypatch.setattr(Path, "stat", vanishing_stat)

    assert (
        worker.claim_capacity(
            state_dir=tmp_path,
            capacity_group="claude-code",
            worker_id="opus-judge",
            claim_ttl_seconds=0.0,
        )
        is None
    )


def test_capacity_lease_pending_deletion_does_not_crash(tmp_path: Path, monkeypatch) -> None:
    """Windows reports a delete-pending lease as EACCES; that is a lost race, not a fault."""
    worker = _load_script("worker")
    (tmp_path / "capacity").mkdir()

    def denied_open(*args, **kwargs):
        raise PermissionError(13, "delete pending")

    monkeypatch.setattr(worker.os, "open", denied_open)

    assert (
        worker.claim_capacity(
            state_dir=tmp_path,
            capacity_group="claude-code",
            worker_id="opus-judge",
            claim_ttl_seconds=60,
        )
        is None
    )


def test_worker_prompt_carries_original_prompt_and_schema(tmp_path: Path) -> None:
    worker = _load_script("worker")
    _pending_request(tmp_path)
    (tmp_path / "claims").mkdir()
    job = worker.claim_next_job(
        state_dir=tmp_path,
        route="ox-factory",
        worker_id="worker-a",
        claim_ttl_seconds=60,
    )
    assert job is not None

    prompt = worker.compose_prompt(job)

    assert "inspect the image" in prompt
    assert '"status"' in prompt
    assert "Return exactly one JSON object" in prompt


def test_codex_usage_reads_only_completed_turn_counters() -> None:
    worker = _load_script("worker")
    stdout = "\n".join(
        (
            json.dumps({"type": "thread.started", "usage": {"input_tokens": 999}}),
            json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {
                        "input_tokens": 120,
                        "cached_input_tokens": 80,
                        "cache_write_input_tokens": 7,
                        "output_tokens": 15,
                        "reasoning_output_tokens": 4,
                    },
                }
            ),
        )
    )

    assert worker.codex_usage(stdout) == {
        "input_tokens": 120,
        "cached_input_tokens": 80,
        "cache_write_input_tokens": 7,
        "output_tokens": 15,
        "reasoning_output_tokens": 4,
    }


def test_codex_backend_requests_json_events_and_records_usage(tmp_path: Path, monkeypatch) -> None:
    worker = _load_script("worker")
    _pending_request(tmp_path, route="sol-all")
    (tmp_path / "claims").mkdir()
    job = worker.claim_next_job(
        state_dir=tmp_path,
        route="sol-all",
        worker_id="codex-sol-all",
        claim_ttl_seconds=60,
    )
    assert job is not None
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        output = Path(command[command.index("--output-last-message") + 1])
        output.write_text(json.dumps({"status": "CODEX_OK"}), encoding="utf-8")
        return SimpleNamespace(
            returncode=0,
            stderr="",
            stdout=json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {
                        "input_tokens": 12,
                        "cached_input_tokens": 3,
                        "output_tokens": 4,
                    },
                }
            ),
        )

    monkeypatch.setattr(worker.subprocess, "run", fake_run)
    backend = worker.CodexBackend(
        executable=sys.executable,
        model="gpt-5.6-sol",
        timeout_seconds=60,
    )

    result = backend.generate(job)

    command = captured["command"]
    assert isinstance(command, list)
    assert "--json" in command
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["encoding"] == "utf-8"
    assert kwargs["errors"] == "strict"
    assert kwargs["env"]["PYTHONUTF8"] == "1"
    assert kwargs["env"]["PYTHONIOENCODING"] == "utf-8"
    assert json.loads(result.text) == {"status": "CODEX_OK"}
    assert result.upstream["usage"] == {
        "input_tokens": 12,
        "cached_input_tokens": 3,
        "output_tokens": 4,
    }


def test_bridge_cache_separates_model_routes() -> None:
    bridge = _load_script("bridge")

    ox_key = bridge.cache_key("prompt", ["status"], ["digest"], "ox-factory")
    sol_key = bridge.cache_key("prompt", ["status"], ["digest"], "sol-judge")

    assert ox_key != sol_key


@pytest.mark.parametrize(
    ("dialect", "expected"),
    (
        (
            "openai",
            {
                "usage": {
                    "prompt_tokens": 120,
                    "completion_tokens": 15,
                    "prompt_tokens_details": {"cached_tokens": 80},
                }
            },
        ),
        (
            "ollama",
            {"prompt_eval_count": 120, "eval_count": 15},
        ),
        (
            "anthropic",
            {
                "usage": {
                    "input_tokens": 40,
                    "cache_read_input_tokens": 80,
                    "output_tokens": 15,
                },
                "dendro_usage": {
                    "input_tokens": 120,
                    "cached_input_tokens": 80,
                    "output_tokens": 15,
                    "cache_write_input_tokens": None,
                    "reasoning_output_tokens": None,
                },
            },
        ),
        (
            "gemini",
            {
                "usageMetadata": {
                    "promptTokenCount": 120,
                    "cachedContentTokenCount": 80,
                    "candidatesTokenCount": 15,
                }
            },
        ),
    ),
)
def test_bridge_projects_cached_worker_usage_into_provider_envelopes(
    tmp_path: Path,
    dialect: str,
    expected: dict[str, object],
) -> None:
    bridge = _load_script("bridge")
    state = bridge.State(tmp_path, wait_timeout=1)
    cached = state.cache / "abc123.json"
    cached.write_text('{"status":"OK"}', encoding="utf-8")
    cached.with_suffix(".meta.json").write_text(
        json.dumps(
            {
                "worker_id": "codex-sol-all",
                "upstream": {
                    "provider": "codex",
                    "usage": {
                        "input_tokens": 120,
                        "cached_input_tokens": 80,
                        "output_tokens": 15,
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    handler = object.__new__(bridge.Handler)
    handler.state = state

    answer, source, usage = handler._answer(
        1,
        dialect,
        "prompt",
        {"type": "object"},
        [],
        "abc123",
        "Probe",
        "sol-all",
    )
    envelope = handler._envelope(dialect, "sol-all", answer, usage)

    assert source == "cache:codex-sol-all"
    for key, value in expected.items():
        assert envelope[key] == value


def test_claude_backend_uses_read_only_structured_output(tmp_path: Path, monkeypatch) -> None:
    worker = _load_script("worker")
    _pending_request(tmp_path, route="claude-main")
    (tmp_path / "claims").mkdir()
    job = worker.claim_next_job(
        state_dir=tmp_path,
        route="claude-main",
        worker_id="claude-main",
        claim_ttl_seconds=60,
    )
    assert job is not None
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return SimpleNamespace(
            returncode=0,
            stderr="",
            stdout=json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "structured_output": {"status": "CLAUDE_OK"},
                    "modelUsage": {"claude-opus": {"inputTokens": 1}},
                }
            ),
        )

    monkeypatch.setattr(worker.subprocess, "run", fake_run)
    backend = worker.ClaudeBackend(
        executable=sys.executable,
        model="opus",
        timeout_seconds=60,
    )

    result = backend.generate(job)

    command = captured["command"]
    assert isinstance(command, list)
    assert command[command.index("--tools") + 1] == "Read"
    assert command[command.index("--allowedTools") + 1] == "Read"
    assert command[command.index("--permission-mode") + 1] == "auto"
    assert "--strict-mcp-config" in command
    assert "--json-schema" in command
    assert "--no-session-persistence" in command
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["stdin"] is worker.subprocess.DEVNULL
    assert kwargs["encoding"] == "utf-8"
    assert kwargs["errors"] == "strict"
    assert kwargs["env"]["PYTHONUTF8"] == "1"
    assert kwargs["env"]["PYTHONIOENCODING"] == "utf-8"
    assert json.loads(result.text) == {"status": "CLAUDE_OK"}
    assert result.upstream["provider"] == "claude-code"


def test_claude_backend_treats_account_quota_as_terminal(tmp_path: Path, monkeypatch) -> None:
    worker = _load_script("worker")
    _pending_request(tmp_path, route="claude-main")
    (tmp_path / "claims").mkdir()
    job = worker.claim_next_job(
        state_dir=tmp_path,
        route="claude-main",
        worker_id="claude-main",
        claim_ttl_seconds=60,
    )
    assert job is not None

    quota_message = "You've hit your monthly spend limit; "
    quota_message += "your session limit resets later today"
    monkeypatch.setattr(
        worker.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stderr="",
            stdout=json.dumps(
                {
                    "api_error_status": 429,
                    "is_error": True,
                    "result": quota_message,
                }
            ),
        ),
    )
    backend = worker.ClaudeBackend(
        executable=sys.executable,
        model="opus",
        timeout_seconds=60,
    )

    with pytest.raises(worker.WorkerTerminalError, match="quota exhausted"):
        backend.generate(job)


def _claude_limit_envelope(result: str, **fields: Any) -> str:
    """Claude Code's JSON result for a refused call, as it prints it on a non-zero exit."""
    return json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": True,
            "api_error_status": 429,
            "result": result,
            "usage": {"input_tokens": 0, "output_tokens": 0},
            **fields,
        }
    )


@pytest.mark.parametrize(
    "result",
    [
        "You've hit your 5-hour limit · resets 8:20am (UTC)",
        "You've hit your weekly limit · resets Nov 17, 2am (UTC)",
        "You've hit your Opus limit · resets 3pm (UTC)",
    ],
)
def test_claude_backend_treats_a_subscription_usage_limit_as_terminal(
    tmp_path: Path, monkeypatch, result: str
) -> None:
    """A usage limit arrives as HTTP 429 but cannot clear before its reset.

    Classified as a short rate limit, the worker asked again every cooldown for hours: the
    run was never paused and its cases died when their uploaded photographs expired.
    """
    worker = _load_script("worker")
    _pending_request(tmp_path, route="claude-main")
    (tmp_path / "claims").mkdir()
    job = worker.claim_next_job(
        state_dir=tmp_path,
        route="claude-main",
        worker_id="claude-main",
        claim_ttl_seconds=60,
    )
    assert job is not None

    monkeypatch.setattr(
        worker.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stderr="",
            stdout=_claude_limit_envelope(result, api_error="usage_limit_reached"),
        ),
    )
    backend = worker.ClaudeBackend(executable=sys.executable, model="opus", timeout_seconds=60)

    with pytest.raises(worker.WorkerTerminalError, match="quota exhausted") as raised:
        backend.generate(job)
    # The operator needs to see which limit it was and when it resets.
    assert result in str(raised.value)


def test_claude_usage_limit_is_recognised_from_either_signal() -> None:
    worker = _load_script("worker")
    wording = "You've hit your 5-hour limit · resets 8:20am (UTC)"

    # The wording alone, as older Claude Code versions report it.
    by_text = worker._claude_envelope(_claude_limit_envelope(wording))
    assert worker._claude_terminal_quota_error("", by_text) is not None
    # The machine-readable field alone, whatever the text says.
    by_field = worker._claude_envelope(
        _claude_limit_envelope("Request refused.", api_error="usage_limit_reached")
    )
    assert worker._claude_terminal_quota_error("", by_field) is not None
    # No parseable result: the raw tail still carries the field name.
    assert worker._claude_terminal_quota_error('…"api_error":"usage_limit_reached"') is not None


def test_claude_short_rate_limit_is_not_a_usage_limit() -> None:
    worker = _load_script("worker")
    short = worker._claude_envelope(
        _claude_limit_envelope(
            "API Error: 429 rate_limit_error: this request would exceed your rate limit",
            api_error="rate_limit_error",
        )
    )

    assert worker._claude_terminal_quota_error("", short) is None
    assert worker._claude_envelope("not json") is None
    assert worker._claude_envelope("[1, 2]") is None


def test_claude_backend_keeps_transient_rate_limit_retryable(tmp_path: Path, monkeypatch) -> None:
    worker = _load_script("worker")
    _pending_request(tmp_path, route="claude-main")
    (tmp_path / "claims").mkdir()
    job = worker.claim_next_job(
        state_dir=tmp_path,
        route="claude-main",
        worker_id="claude-main",
        claim_ttl_seconds=60,
    )
    assert job is not None

    monkeypatch.setattr(
        worker.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stderr="HTTP 429 rate limit; retry shortly",
            stdout="",
        ),
    )
    backend = worker.ClaudeBackend(
        executable=sys.executable,
        model="opus",
        timeout_seconds=60,
    )

    with pytest.raises(worker.WorkerRateLimitError):
        backend.generate(job)


def test_terminal_failure_removes_request_from_worker_queue(tmp_path: Path) -> None:
    worker = _load_script("worker")
    _pending_request(tmp_path, route="claude-main")
    for directory in ("claims", "failures"):
        (tmp_path / directory).mkdir()
    job = worker.claim_next_job(
        state_dir=tmp_path,
        route="claude-main",
        worker_id="claude-main",
        claim_ttl_seconds=60,
    )
    assert job is not None

    worker.record_terminal_failure(
        tmp_path,
        job,
        "claude-main",
        worker.WorkerTerminalError("Claude route quota exhausted"),
    )
    job.claim_path.unlink()

    assert (
        worker.claim_next_job(
            state_dir=tmp_path,
            route="claude-main",
            worker_id="claude-main",
            claim_ttl_seconds=60,
        )
        is None
    )


def test_bridge_returns_terminal_worker_failure_without_waiting(tmp_path: Path) -> None:
    bridge = _load_script("bridge")
    state = bridge.State(tmp_path, wait_timeout=30)
    marker = {
        "request_id": 1,
        "cache_key": "abc123",
        "error_type": "WorkerTerminalError",
        "message": "Claude route quota exhausted",
    }
    handler = object.__new__(bridge.Handler)
    handler.state = state

    def write_terminal_marker() -> None:
        pending = state.pending / "req-001-meta.json"
        while not pending.is_file():
            time.sleep(0.01)
        (state.failures / "req-001-terminal.json").write_text(json.dumps(marker), encoding="utf-8")

    publisher = threading.Thread(target=write_terminal_marker)
    publisher.start()

    started = time.perf_counter()
    try:
        with pytest.raises(bridge.DialectRejectionError) as caught:
            handler._answer(
                1,
                "anthropic",
                "prompt",
                {"type": "object"},
                [],
                "abc123",
                "Probe",
                "claude-main",
            )
    finally:
        publisher.join(timeout=1)

    assert caught.value.status == 424
    assert "quota exhausted" in caught.value.body["error"]["message"]
    assert time.perf_counter() - started < 2


def _anthropic_request(bridge: ModuleType, headers: dict[str, str]) -> tuple[Any, dict[str, Any]]:
    from email.message import Message

    message = Message()
    for name, value in headers.items():
        message[name] = value
    handler = object.__new__(bridge.Handler)
    handler.headers = message
    schema = {"title": "Probe", "type": "object", "properties": {"status": {"type": "string"}}}
    body = {
        "model": "claude-main",
        "max_tokens": 64,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"inspect\n## Required output\n{json.dumps(schema)}"}
                ],
            }
        ],
    }
    return handler, body


@pytest.mark.parametrize(
    "credential",
    ({"authorization": "Bearer bridge-local-placeholder"}, {"x-api-key": "placeholder"}),
)
def test_bridge_accepts_anthropic_key_as_bearer_or_header(credential: dict[str, str]) -> None:
    bridge = _load_script("bridge")
    handler, body = _anthropic_request(bridge, {"anthropic-version": "2023-06-01", **credential})

    dialect, _, _, schema, model, wanted = handler._parse("/v1/messages", body)

    assert (dialect, model, wanted) == ("anthropic", "claude-main", "Probe")
    assert schema["title"] == "Probe"


@pytest.mark.parametrize(
    "credential", ({}, {"authorization": "Bearer "}, {"authorization": "Basic x"})
)
def test_bridge_still_rejects_anthropic_request_without_a_key(credential: dict[str, str]) -> None:
    bridge = _load_script("bridge")
    handler, body = _anthropic_request(bridge, {"anthropic-version": "2023-06-01", **credential})

    with pytest.raises(bridge.DialectRejectionError) as caught:
        handler._parse("/v1/messages", body)

    assert caught.value.status == 401
    assert caught.value.body["error"]["type"] == "authentication_error"


def test_codex_failure_keeps_the_reason_from_stdout(tmp_path: Path, monkeypatch) -> None:
    worker = _load_script("worker")
    _pending_request(tmp_path, route="sol-all")
    (tmp_path / "claims").mkdir()
    job = worker.claim_next_job(
        state_dir=tmp_path,
        route="sol-all",
        worker_id="codex-sol-all",
        claim_ttl_seconds=60,
    )
    assert job is not None

    def fake_run(command, **kwargs):
        return SimpleNamespace(
            returncode=1,
            stderr="reading prompt from stdin",
            stdout=json.dumps({"type": "error", "message": "model_not_found: gpt-0"}),
        )

    monkeypatch.setattr(worker.subprocess, "run", fake_run)
    backend = worker.CodexBackend(executable=sys.executable, model="gpt-0", timeout_seconds=60)

    with pytest.raises(worker.WorkerError) as caught:
        backend.generate(job)

    assert "model_not_found" in str(caught.value)
    assert "reading prompt from stdin" in str(caught.value)


def test_codex_backend_passes_enabled_features(tmp_path: Path, monkeypatch) -> None:
    worker = _load_script("worker")
    _pending_request(tmp_path, route="sol-all")
    (tmp_path / "claims").mkdir()
    job = worker.claim_next_job(
        state_dir=tmp_path, route="sol-all", worker_id="codex-sol-all", claim_ttl_seconds=60
    )
    assert job is not None
    captured: dict[str, Any] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        output = Path(command[command.index("--output-last-message") + 1])
        output.write_text(json.dumps({"status": "OK"}), encoding="utf-8")
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(worker.subprocess, "run", fake_run)
    backend = worker.CodexBackend(
        executable=sys.executable,
        model="gpt-6.1-sol",
        timeout_seconds=60,
        enable_features=("image_detail_original",),
    )
    backend.generate(job)

    command = captured["command"]
    position = command.index("--enable")
    assert command[position + 1] == "image_detail_original"
    assert position < command.index("--image")


def _vibe_job(worker: ModuleType, state: Path) -> Any:
    _pending_request(state, route="vibe-main")
    (state / "claims").mkdir()
    job = worker.claim_next_job(
        state_dir=state, route="vibe-main", worker_id="vibe-main", claim_ttl_seconds=60
    )
    assert job is not None
    return job


def _vibe_output(*, opened: list[str], answer: str) -> str:
    """Vibe 3.1.3 ``--output json``: one entry per message or tool effect."""
    entries: list[dict[str, Any]] = [
        {"type": "message", "role": "user", "content": [{"type": "text", "text": "..."}]}
    ]
    for path in opened:
        entries.append(
            {
                "type": "effect",
                "detail": {"toolName": "file_system.read_file", "input": {"filePath": path}},
                "state": {"status": "completed", "output": {"content": "Image file"}},
            }
        )
    entries.append(
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": answer}],
            "outcome": {"type": "committed"},
        }
    )
    return json.dumps(entries)


def _fake_vibe(
    captured: dict[str, Any],
    *,
    returncode: int = 0,
    export: dict[str, Any] | None = None,
    stderr: str = "",
    open_photos: bool = True,
):
    def fake_run(command, **kwargs):
        workdir = Path(command[command.index("--workdir") + 1])
        captured.update(
            command=command,
            kwargs=kwargs,
            photos=sorted(path.name for path in workdir.iterdir()),
        )
        export_dir = Path(command[command.index("--output-dir") + 1])
        export_dir.mkdir(parents=True, exist_ok=True)
        (export_dir / "export.json").write_text(
            json.dumps(
                export
                or {
                    "outcome": "finished",
                    "error": None,
                    "usage": {"input_tokens": 900, "output_tokens": 40, "total_tokens": 940},
                    "cost_usd": 0.0012,
                    "steps": 2,
                    "vibe_version": "3.1.3",
                }
            ),
            encoding="utf-8",
        )
        opened = [str(path) for path in workdir.iterdir()] if open_photos else []
        return SimpleNamespace(
            returncode=returncode,
            stderr=stderr,
            stdout=_vibe_output(opened=opened, answer='{"status": "VIBE_OK"}'),
        )

    return fake_run


def test_vibe_backend_opens_photos_with_read_file_in_a_confined_folder(
    tmp_path: Path, monkeypatch
) -> None:
    worker = _load_script("worker")
    job = _vibe_job(worker, tmp_path / "bridge")
    captured: dict[str, Any] = {}
    monkeypatch.setattr(worker.subprocess, "run", _fake_vibe(captured))
    root = tmp_path / "vibe"
    backend = worker.VibeBackend(
        executable=sys.executable, model="mistral-large-4-0", timeout_seconds=600, root=root
    )

    result = backend.generate(job)

    command = captured["command"]
    workdir = root / "jobs" / "abc123"
    assert command[command.index("-p") + 1] == "--output"
    assert command[command.index("--output") + 1] == "json"
    assert command[command.index("--workdir") + 1] == str(workdir)
    assert command[command.index("--model") + 1] == "mistral-large-4-0"
    assert command[command.index("--enabled-tools") + 1] == "read_file"
    assert "--trust" in command
    # The photograph is copied into the job folder, and only it.
    assert captured["photos"] == ["req-001-img1.jpg"]
    kwargs = captured["kwargs"]
    assert kwargs["cwd"] == workdir
    assert kwargs["env"]["VIBE_HOME"] == str(root / ".vibe")
    assert str(workdir / "req-001-img1.jpg") in kwargs["input"]
    assert "inspect the image" in kwargs["input"]
    config = (root / ".vibe" / "config.toml").read_text(encoding="utf-8")
    for tool in ("bash", "write_file", "edit", "powershell", "web_fetch"):
        assert f'[tools.{tool}]\npermission = "never"' in config
    assert 'outside_workdir = "deny"' in config
    assert "enable_telemetry = false" in config
    assert json.loads(result.text) == {"status": "VIBE_OK"}
    assert result.upstream["provider"] == "mistral-vibe"
    assert result.upstream["usage"]["total_tokens"] == 940
    assert result.upstream["cost_usd"] == 0.0012
    assert result.upstream["images_opened"] == 1
    assert not workdir.exists()  # the copies go when the call ends


def test_vibe_backend_refuses_an_answer_given_without_opening_the_photos(
    tmp_path: Path, monkeypatch
) -> None:
    worker = _load_script("worker")
    job = _vibe_job(worker, tmp_path / "bridge")
    monkeypatch.setattr(worker.subprocess, "run", _fake_vibe({}, open_photos=False))
    backend = worker.VibeBackend(
        executable=sys.executable, model="m", timeout_seconds=600, root=tmp_path / "vibe"
    )

    with pytest.raises(worker.WorkerError, match=r"without opening req-001-img1\.jpg"):
        backend.generate(job)


def test_vibe_backend_treats_a_missing_key_as_terminal(tmp_path: Path, monkeypatch) -> None:
    worker = _load_script("worker")
    job = _vibe_job(worker, tmp_path / "bridge")
    fake = _fake_vibe(
        {},
        returncode=1,
        export={"outcome": "failed", "error": None},
        stderr="Error: Missing MISTRAL_API_KEY environment variable for mistral provider",
    )
    monkeypatch.setattr(worker.subprocess, "run", fake)
    backend = worker.VibeBackend(
        executable=sys.executable, model="m", timeout_seconds=600, root=tmp_path / "vibe"
    )

    with pytest.raises(worker.WorkerTerminalError):
        backend.generate(job)


def test_vibe_backend_keeps_a_rate_limit_retryable(tmp_path: Path, monkeypatch) -> None:
    worker = _load_script("worker")
    job = _vibe_job(worker, tmp_path / "bridge")
    fake = _fake_vibe(
        {},
        returncode=2,
        export={"outcome": "failed", "error": {"message": "429 Too Many Requests"}},
    )
    monkeypatch.setattr(worker.subprocess, "run", fake)
    backend = worker.VibeBackend(
        executable=sys.executable, model="m", timeout_seconds=600, root=tmp_path / "vibe"
    )

    with pytest.raises(worker.WorkerRateLimitError):
        backend.generate(job)


def test_vibe_thinking_fastest_follows_the_model() -> None:
    worker = _load_script("worker")

    assert worker.vibe_thinking("mistral-large-4-0", "fastest") == "low"
    assert worker.vibe_thinking("mistral-medium-3.5", "fastest") == "off"
    assert worker.vibe_thinking("some-new-model", "fastest") is None
    assert worker.vibe_thinking("mistral-large-4-0", "default") is None
    assert worker.vibe_thinking("mistral-large-4-0", None) is None
    assert worker.vibe_thinking("mistral-large-4-0", "high") == "high"


def test_vibe_backend_passes_thinking_and_downscales_copies(tmp_path: Path, monkeypatch) -> None:
    pil_image = pytest.importorskip("PIL.Image")  # the optional `images` extra

    worker = _load_script("worker")
    job = _vibe_job(worker, tmp_path / "bridge")
    pil_image.new("RGB", (4000, 3000), (10, 120, 40)).save(job.image_paths[0], "JPEG")
    seen: dict[str, Any] = {}
    fake = _fake_vibe(seen)

    def recording_run(command, **kwargs):
        workdir = Path(command[command.index("--workdir") + 1])
        with pil_image.open(workdir / "req-001-img1.jpg") as copy:
            seen["size"] = copy.size
        return fake(command, **kwargs)

    monkeypatch.setattr(worker.subprocess, "run", recording_run)
    backend = worker.VibeBackend(
        executable=sys.executable,
        model="mistral-large-4-0",
        timeout_seconds=600,
        root=tmp_path / "vibe",
        thinking="fastest",
        max_edge=1600,
    )

    result = backend.generate(job)

    command = seen["command"]
    assert command[command.index("--thinking") + 1] == "low"
    assert max(seen["size"]) == 1600
    assert result.upstream["thinking"] == "low"
    assert result.upstream["image_max_edge"] == 1600
    with pil_image.open(job.image_paths[0]) as original:
        assert original.size == (4000, 3000)  # the bridge's own copy is untouched


def test_copy_photo_keeps_a_small_or_unknown_file_as_received(tmp_path: Path) -> None:
    pil_image = pytest.importorskip("PIL.Image")  # the optional `images` extra

    worker = _load_script("worker")
    small = tmp_path / "small.png"
    pil_image.new("RGB", (800, 600)).save(small, "PNG")
    worker.copy_photo(small, tmp_path / "small-copy.png", 1600)
    assert (tmp_path / "small-copy.png").read_bytes() == small.read_bytes()
    odd = tmp_path / "photo.heic"
    odd.write_bytes(b"not decoded here")
    worker.copy_photo(odd, tmp_path / "photo-copy.heic", 1600)
    assert (tmp_path / "photo-copy.heic").read_bytes() == b"not decoded here"


def test_describe_images_reports_size_and_missing_files(tmp_path: Path) -> None:
    worker = _load_script("worker")
    image = tmp_path / "a.jpg"
    image.write_bytes(b"x" * 10)

    described = worker.describe_images((image, tmp_path / "gone.jpg"))

    assert "a.jpg=" in described and "10B" in described
    assert "gone.jpg=missing" in described


def test_bridge_replays_persistent_answers_with_fresh_attempt_state(tmp_path: Path) -> None:
    bridge = _load_script("bridge")
    cache = tmp_path / "shared" / "run-1" / "case-0"
    first = bridge.State(tmp_path / "pod-1", wait_timeout=1, cache_dir=cache)
    handler = object.__new__(bridge.Handler)
    handler.state = first
    (first.answers / "key.json").write_text('{"status":"OK"}', encoding="utf-8")
    (first.answers / "key.meta.json").write_text(
        json.dumps({"worker_id": "claude-main", "upstream": {"usage": {"input_tokens": 12}}}),
        encoding="utf-8",
    )
    live = handler._answer(1, "anthropic", "prompt", {}, [], "key", "Probe", "claude-main")
    (first.failures / "req-001-terminal.json").write_text("{}", encoding="utf-8")
    handler.state = bridge.State(tmp_path / "pod-2", wait_timeout=0, cache_dir=cache)
    replay = handler._answer(1, "anthropic", "prompt", {}, [], "key", "Probe", "claude-main")
    assert replay[0] == live[0]
    assert replay[1] == "cache:claude-main"
    assert replay[2] == live[2]
    assert not list(handler.state.pending.iterdir())
    assert not list(handler.state.failures.iterdir())
    assert not list(handler.state.answers.iterdir())
    # A different call must still ask a worker; persistence is not an unconditional replay.
    with pytest.raises(bridge.DialectRejectionError):
        handler._answer(2, "anthropic", "changed", {}, [], "other", "Probe", "claude-main")


def test_bridge_interrupted_cache_publication_preserves_previous_answer(
    tmp_path: Path, monkeypatch
) -> None:
    bridge = _load_script("bridge")
    path = tmp_path / "answer.json"
    path.write_text("original", encoding="utf-8")

    def interrupted(self: Path, target: Path) -> None:
        raise OSError("publication interrupted")

    monkeypatch.setattr(Path, "replace", interrupted)
    with pytest.raises(OSError, match="publication interrupted"):
        bridge.Handler._publish_cache(path, "replacement")
    assert path.read_text(encoding="utf-8") == "original"
    assert list(tmp_path.iterdir()) == [path]
