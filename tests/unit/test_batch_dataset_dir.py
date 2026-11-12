"""The batch worker reading a dataset from the results volume instead of Hugging Face."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[2]
IMAGE = b"\xff\xd8\xff synthetic image bytes"


def _worker() -> ModuleType:
    path = REPO / "scripts" / "batch" / "run_dataset_case.py"
    spec = importlib.util.spec_from_file_location("_batch_dataset_dir", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _dataset(results: Path, image: bytes = IMAGE) -> str:
    folder = results / "_datasets" / "trunks"
    folder.mkdir(parents=True)
    (folder / "u0001.jpg").write_bytes(image)
    record = {"id": "u0001", "file_name": "u0001.jpg", "sha256": hashlib.sha256(IMAGE).hexdigest()}
    metadata = (json.dumps(record) + "\n").encode()
    (folder / "metadata.jsonl").write_bytes(metadata)
    return hashlib.sha256(metadata).hexdigest()


def _env(results: Path, revision: str) -> dict[str, str]:
    return {
        "DATASET_REPO": "share/trunks",
        "DATASET_DIR": "trunks",
        "DATASET_REVISION": revision,
        "RUN_ID": "r1",
        "RESULTS_DIR": str(results),
        "JOB_COMPLETION_INDEX": "0",
    }


def _fake_cli(monkeypatch, worker: ModuleType, *, returncode: int = 0) -> list[list[str]]:
    calls: list[list[str]] = []

    def run(argv, **_):
        calls.append(argv)
        # The photograph must exist, unchanged, when the engine is called.
        assert Path(argv[argv.index("--image") + 1]).read_bytes() == IMAGE
        return subprocess.CompletedProcess(
            argv, returncode, stdout=json.dumps({"response": {}}), stderr="engine log line\n"
        )

    monkeypatch.setattr(worker.subprocess, "run", run)
    monkeypatch.setattr(
        worker, "_fetch", lambda *_a, **_k: pytest.fail("a volume dataset must not be fetched")
    )
    return calls


def test_a_volume_dataset_runs_without_any_download_and_keeps_its_log(tmp_path, monkeypatch):
    worker = _worker()
    revision = _dataset(tmp_path)
    calls = _fake_cli(monkeypatch, worker)

    assert worker.run(_env(tmp_path, revision)) == 0

    assert len(calls) == 1
    record = json.loads((tmp_path / "r1" / "cases" / "u0001.json").read_text(encoding="utf-8"))
    assert record["exit_code"] == 0
    assert record["dataset"] == {
        "repo": "share/trunks",
        "revision": revision,
        "sha256": hashlib.sha256(IMAGE).hexdigest(),
    }
    assert (tmp_path / "r1" / "logs" / "u0001.log").read_text(encoding="utf-8") == (
        "engine log line\n"
    )


def test_a_failed_case_keeps_its_log_too(tmp_path, monkeypatch):
    worker = _worker()
    revision = _dataset(tmp_path)
    _fake_cli(monkeypatch, worker, returncode=1)

    assert worker.run(_env(tmp_path, revision)) == 1
    assert (tmp_path / "r1" / "logs" / "u0001.log").exists()


def test_a_changed_index_is_a_different_dataset(tmp_path, monkeypatch):
    worker = _worker()
    _dataset(tmp_path)
    calls = _fake_cli(monkeypatch, worker)

    with pytest.raises(worker.CaseError, match="sha256 mismatch"):
        worker.run(_env(tmp_path, "0" * 64))
    assert calls == []


def test_a_changed_image_is_refused_before_the_engine_sees_it(tmp_path, monkeypatch):
    worker = _worker()
    revision = _dataset(tmp_path, image=IMAGE + b"tampered")
    calls: list[list[str]] = []
    monkeypatch.setattr(worker.subprocess, "run", lambda argv, **_: calls.append(argv))

    with pytest.raises(worker.CaseError, match="sha256 mismatch"):
        worker.run(_env(tmp_path, revision))
    assert calls == []


@pytest.mark.parametrize("name", ["../other", "a/b", ""])
def test_a_dataset_directory_cannot_leave_the_datasets_folder(tmp_path, monkeypatch, name):
    worker = _worker()
    revision = _dataset(tmp_path)
    env = {**_env(tmp_path, revision), "DATASET_DIR": name}
    if name:
        with pytest.raises(worker.CaseError, match="DATASET_DIR"):
            worker.run(env)
    else:
        # Empty means "not a volume dataset": the Hugging Face rules apply to the revision.
        with pytest.raises(worker.CaseError, match="commit SHA"):
            worker.run(env)
