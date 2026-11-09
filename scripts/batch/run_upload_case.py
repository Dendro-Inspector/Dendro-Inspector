"""Run one uploaded set of photographs (one tree) through `dendro inspect`.

Built for a single-pod Kubernetes Job. The control API writes the photos and a manifest to
RESULTS_DIR/<RUN_ID>/inputs/; this script checks every photo against the SHA-256 in the
manifest, passes them all to one `dendro inspect` call (one --image per photo) and writes
one record to RESULTS_DIR/<RUN_ID>/cases/<case_id>.json, in the same format as
run_dataset_case.py. Standard library only.

Manifest (inputs/manifest.json):
    {"case_id": "oak-garden", "lang": "en", "note": "optional free text",
     "photos": [{"file_name": "photo-1.jpg", "sha256": "<hex>"}, ...]}

Environment:
    RUN_ID       identifier for this run
    RESULTS_DIR  where run directories are written (default: /results)
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

from run_dataset_case import (
    RECORD_SCHEMA,
    SAFE_ID,
    SHA256,
    CaseError,
    _now,
    already_done,
    configured_models,
    write_atomic,
)

LANG = ("en", "uk")
MAX_PHOTOS = 12


def load_manifest(inputs: Path) -> dict[str, Any]:
    manifest = json.loads((inputs / "manifest.json").read_text(encoding="utf-8"))
    if not SAFE_ID.match(str(manifest.get("case_id", ""))):
        raise CaseError("unusable case id in manifest")
    photos = manifest.get("photos") or []
    if not 1 <= len(photos) <= MAX_PHOTOS:
        raise CaseError(f"manifest must list 1-{MAX_PHOTOS} photos")
    for photo in photos:
        if not SAFE_ID.match(str(photo.get("file_name", ""))):
            raise CaseError("unusable photo file name in manifest")
        if not SHA256.match(str(photo.get("sha256", ""))):
            raise CaseError(f"missing sha256 for {photo.get('file_name')}")
        payload = (inputs / photo["file_name"]).read_bytes()
        if hashlib.sha256(payload).hexdigest() != photo["sha256"]:
            raise CaseError(f"sha256 mismatch for {photo['file_name']}")
    if manifest.get("lang", "en") not in LANG:
        raise CaseError("lang must be en or uk")
    return manifest


def run(env: dict[str, str]) -> int:
    run_id = env["RUN_ID"]
    if not SAFE_ID.match(run_id):
        raise CaseError("RUN_ID may hold only letters, digits, '.', '_' and '-'")
    run_dir = Path(env.get("RESULTS_DIR", "/results")) / run_id
    inputs = run_dir / "inputs"
    manifest = load_manifest(inputs)
    case_id = manifest["case_id"]
    target = run_dir / "cases" / f"{case_id}.json"
    if already_done(target):
        print(f"{case_id}: already done, skipping")
        return 0

    record: dict[str, Any] = {
        "schema": RECORD_SCHEMA,
        "run_id": run_id,
        "case_id": case_id,
        "index": 0,
        "upload": {
            "photos": [p["file_name"] for p in manifest["photos"]],
            "note": manifest.get("note", ""),
        },
        "models": configured_models(env),
        "started_at": _now(),
    }
    argv = ["dendro", "inspect", "--case-id", case_id, "--lang", manifest.get("lang", "en")]
    for photo in manifest["photos"]:
        argv += ["--image", str(inputs / photo["file_name"])]
    if manifest.get("note"):
        argv += ["--text", str(manifest["note"])[:2000]]
    if manifest.get("location"):
        argv += ["--location", str(manifest["location"])[:200]]
    if manifest.get("claim"):
        argv += ["--claim", str(manifest["claim"])[:200]]
    argv.append("--json")
    started = time.monotonic()
    completed = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", check=False)
    record["wall_seconds"] = round(time.monotonic() - started, 1)
    record["finished_at"] = _now()
    record["exit_code"] = completed.returncode
    if completed.returncode == 0:
        record["result"] = json.loads(completed.stdout)
    else:
        record["error"] = completed.stderr[-4000:]
    write_atomic(target, record)
    print(
        f"{case_id}: {len(manifest['photos'])} photos, exit {completed.returncode} "
        f"in {record['wall_seconds']}s"
    )
    return completed.returncode


if __name__ == "__main__":
    try:
        sys.exit(run(dict(os.environ)))
    except (CaseError, KeyError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)
