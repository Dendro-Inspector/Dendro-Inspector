"""An uploaded photo set keeps the engine's log beside its record, as a dataset case does.

The record holds only the last 4000 characters of a failure's stderr. When an upload failed
on something that happened earlier in the run, what the engine had logged about it was gone
with the pod.
"""

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
    path = REPO / "scripts" / "batch" / "run_upload_case.py"
    spec = importlib.util.spec_from_file_location("_batch_upload_log", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _upload(results: Path) -> None:
    inputs = results / "t1" / "inputs"
    inputs.mkdir(parents=True)
    (inputs / "photo-1.jpg").write_bytes(IMAGE)
    manifest = {
        "case_id": "tree",
        "lang": "en",
        "photos": [{"file_name": "photo-1.jpg", "sha256": hashlib.sha256(IMAGE).hexdigest()}],
    }
    (inputs / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


# 4 is the engine's exit code for a content-filter refusal; the runner passes it through.
@pytest.mark.parametrize("returncode", [0, 1, 4])
def test_an_uploaded_case_keeps_its_log_and_the_engine_exit_code(tmp_path, monkeypatch, returncode):
    worker = _worker()
    _upload(tmp_path)
    monkeypatch.setattr(
        worker.subprocess,
        "run",
        lambda argv, **_: subprocess.CompletedProcess(
            argv, returncode, stdout=json.dumps({"response": {}}), stderr="engine log line\n"
        ),
    )

    assert worker.run({"RUN_ID": "t1", "RESULTS_DIR": str(tmp_path)}) == returncode

    log = tmp_path / "t1" / "logs" / "tree.log"
    assert log.read_text(encoding="utf-8") == "engine log line\n"
    record = json.loads((tmp_path / "t1" / "cases" / "tree.json").read_text(encoding="utf-8"))
    assert record["exit_code"] == returncode
