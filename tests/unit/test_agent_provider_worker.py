from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace


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
