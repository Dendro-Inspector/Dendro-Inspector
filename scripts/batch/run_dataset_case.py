"""Run one photograph from an image dataset through `dendro inspect`.

Built for a Kubernetes Indexed Job: each pod handles the case at JOB_COMPLETION_INDEX and
writes one JSON record to RESULTS_DIR/<RUN_ID>/cases/<case_id>.json and the engine's log
to RESULTS_DIR/<RUN_ID>/logs/<case_id>.log. The dataset revision must be pinned, and every
image is checked against the SHA-256 in the dataset's metadata.jsonl before it is sent
anywhere. Standard library only.

A dataset is either a Hugging Face repository or a directory on the results volume,
RESULTS_DIR/_datasets/<DATASET_DIR>, holding metadata.jsonl and the images beside it.

Environment:
    DATASET_REPO          e.g. Dendro-Inspector/dendro-golden-100
    DATASET_REVISION      Hugging Face: full commit SHA of the dataset (no branch names).
                          Results volume: SHA-256 of that directory's metadata.jsonl.
    DATASET_DIR           optional; a directory name under RESULTS_DIR/_datasets. When set,
                          nothing is fetched from Hugging Face.
    JOB_COMPLETION_INDEX  0-based case index (set by Kubernetes)
    RUN_ID                identifier for this run
    RESULTS_DIR           where run directories are written (default: /results)
    DENDRO_LANG           output language passed to inspect (default: en)
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RECORD_SCHEMA = 1
HF_BASE = "https://huggingface.co/datasets"
REVISION = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
LOCAL_DATASETS = "_datasets"
ROLES = ("PRIMARY", "REVIEWER", "ARBITER")


class CaseError(RuntimeError):
    """The requested case cannot be run as specified."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _fetch(url: str, *, attempts: int = 4) -> bytes:
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                return bytes(response.read())
        except OSError:
            if attempt == attempts:
                raise
            time.sleep(2**attempt)
    raise AssertionError("unreachable")


def select_case(metadata_jsonl: str, index: int) -> dict[str, Any]:
    """Return the metadata record at ``index``, checked before any part reaches a path."""
    records = [json.loads(line) for line in metadata_jsonl.splitlines() if line.strip()]
    if not 0 <= index < len(records):
        msg = f"index {index} outside dataset of {len(records)} records"
        raise CaseError(msg)
    record = dict(records[index])
    if not SAFE_ID.match(str(record.get("id", ""))):
        msg = f"unusable case id at index {index}"
        raise CaseError(msg)
    if not SAFE_ID.match(str(record.get("file_name", ""))):
        msg = f"unusable file name at index {index}"
        raise CaseError(msg)
    if not SHA256.match(str(record.get("sha256", ""))):
        msg = f"missing sha256 at index {index}"
        raise CaseError(msg)
    return record


def verify_digest(payload: bytes, expected: str) -> None:
    actual = hashlib.sha256(payload).hexdigest()
    if actual != expected:
        msg = f"sha256 mismatch: expected {expected}, got {actual}"
        raise CaseError(msg)


def write_log(path: Path, text: str) -> None:
    """Keep the engine's own log beside the record. Never fails the case it describes."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        print(f"log not saved: {exc}", file=sys.stderr)


def configured_models(env: dict[str, str]) -> dict[str, str]:
    return {
        role.lower(): (
            f"{env.get(f'DENDRO_{role}_PROVIDER', '')}:{env.get(f'DENDRO_{role}_MODEL', '')}"
        )
        for role in ROLES
    }


def write_atomic(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def already_done(path: Path) -> bool:
    if not path.exists():
        return False
    return bool(json.loads(path.read_text(encoding="utf-8")).get("exit_code") == 0)


def _failure_record(stdout: str) -> dict[str, Any] | None:
    """The CLI's JSON failure report (error + partial trace), when it printed one.

    A failed case used to keep only a stderr tail, which held the end of a traceback and
    none of the per-call validation errors that explained it.
    """
    try:
        payload = json.loads(stdout)
    except ValueError:
        return None
    if isinstance(payload, dict) and "error" in payload and "trace" in payload:
        return payload
    return None


def run(env: dict[str, str]) -> int:
    repo = env["DATASET_REPO"]
    revision = env["DATASET_REVISION"]
    run_id = env["RUN_ID"]
    local = env.get("DATASET_DIR", "")
    if not SAFE_ID.match(run_id):
        msg = "RUN_ID may hold only letters, digits, '.', '_' and '-'"
        raise CaseError(msg)
    index = int(env["JOB_COMPLETION_INDEX"])
    results = Path(env.get("RESULTS_DIR", "/results"))
    out_dir = results / run_id / "cases"

    if local:
        if not SAFE_ID.match(local):
            msg = "DATASET_DIR may hold only letters, digits, '.', '_' and '-'"
            raise CaseError(msg)
        folder = results / LOCAL_DATASETS / local
        metadata = (folder / "metadata.jsonl").read_bytes()
        # The pin for a directory is the digest of its own index: a changed file list or a
        # changed image digest is a different dataset.
        verify_digest(metadata, revision)

        def load(name: str) -> bytes:
            return (folder / name).read_bytes()
    else:
        if not REVISION.match(revision):
            msg = "DATASET_REVISION must be a full 40-character commit SHA"
            raise CaseError(msg)
        base = f"{HF_BASE}/{repo}/resolve/{revision}"
        metadata = _fetch(f"{base}/metadata.jsonl")

        def load(name: str) -> bytes:
            return _fetch(f"{base}/{name}")

    case = select_case(metadata.decode("utf-8"), index)
    target = out_dir / f"{case['id']}.json"
    if already_done(target):
        print(f"{case['id']}: already done, skipping")
        return 0

    image = load(case["file_name"])
    verify_digest(image, case["sha256"])

    record: dict[str, Any] = {
        "schema": RECORD_SCHEMA,
        "run_id": run_id,
        "case_id": case["id"],
        "index": index,
        "dataset": {"repo": repo, "revision": revision, "sha256": case["sha256"]},
        "models": configured_models(env),
        "started_at": _now(),
    }
    with tempfile.TemporaryDirectory() as workdir:
        photo = Path(workdir) / case["file_name"]
        photo.write_bytes(image)
        started = time.monotonic()
        argv = [
            "dendro",
            "inspect",
            "--image",
            str(photo),
            "--case-id",
            case["id"],
            "--lang",
            env.get("DENDRO_LANG", "en"),
            "--json",
        ]
        completed = subprocess.run(
            argv, capture_output=True, text=True, encoding="utf-8", check=False
        )
    record["wall_seconds"] = round(time.monotonic() - started, 1)
    record["finished_at"] = _now()
    record["exit_code"] = completed.returncode
    write_log(results / run_id / "logs" / f"{case['id']}.log", completed.stderr)
    if completed.returncode == 0:
        record["result"] = json.loads(completed.stdout)
    else:
        record["error"] = completed.stderr[-4000:]
        failure = _failure_record(completed.stdout)
        if failure is not None:
            record["failure"] = failure
    write_atomic(target, record)
    print(f"{case['id']}: exit {completed.returncode} in {record['wall_seconds']}s")
    return completed.returncode


if __name__ == "__main__":
    try:
        sys.exit(run(dict(os.environ)))
    except (CaseError, KeyError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)
