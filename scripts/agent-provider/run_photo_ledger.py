"""Run private photo-ledger cases sequentially through the local provider bridge.

The manifest is the experiment's canonical data. This helper verifies the recorded image
digest before transmission, runs one image at a time, and appends a new immutable run record
only after the process exits. It never rewrites or merges an existing run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

CONFIGURATION_ID = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*_v[1-9][0-9]*$")
BATCH_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")
DATASET_VERSION = 1


class LedgerError(RuntimeError):
    """The ledger or requested append violates an experiment invariant."""


def load_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if payload.get("dataset_version") != DATASET_VERSION:
        raise LedgerError(
            f"unsupported dataset_version={payload.get('dataset_version')!r}; "
            f"expected {DATASET_VERSION}"
        )
    photos = payload.get("photos")
    if not isinstance(photos, list):
        raise LedgerError("manifest.photos must be an array")
    return payload


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def append_run(path: Path, *, photo_id: str, run: dict[str, Any]) -> None:
    """Append one run, refusing duplicate IDs anywhere in the ledger."""
    configuration = run.get("configuration")
    if not isinstance(configuration, str) or not CONFIGURATION_ID.fullmatch(configuration):
        raise LedgerError("configuration must be a stable versioned ID such as claude_ox_sol_v1")
    run_id = run.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise LedgerError("run_id must be a non-empty string")

    manifest = load_manifest(path)
    all_runs = [
        existing
        for photo in manifest["photos"]
        for existing in photo.get("runs", [])
        if isinstance(existing, dict)
    ]
    if any(existing.get("run_id") == run_id for existing in all_runs):
        raise LedgerError(f"run_id already exists and is immutable: {run_id}")

    matches = [photo for photo in manifest["photos"] if photo.get("id") == photo_id]
    if len(matches) != 1:
        raise LedgerError(f"expected exactly one photo with id={photo_id!r}; found {len(matches)}")
    runs = matches[0].get("runs")
    if not isinstance(runs, list):
        raise LedgerError(f"photo {photo_id!r} has a non-array runs field")
    runs.append(run)
    atomic_write_json(path, manifest)


def _table_text(value: Any) -> str:
    if value is None or value == "":
        return "—"
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def render_markdown(manifest: dict[str, Any]) -> str:
    lines = [
        "# Top 100 experiment ledger",
        "",
        "> Generated from manifest.json. Read-only. Do not edit manually.",
        "",
        (
            "| Photo | Filename | Capture group | Reference | Runs | Latest result | "
            "Escalated | Time (s) | Cost (USD) | Notes |"
        ),
        "|---|---|---|---|---:|---|---|---:|---:|---|",
    ]
    for photo in manifest["photos"]:
        runs = photo.get("runs", [])
        latest = runs[-1] if runs else None
        result = latest.get("result", {}) if latest else {}
        taxon = result.get("taxon") if result else None
        decision = result.get("decision") if result else None
        latest_result = (
            " / ".join(_table_text(value) for value in (taxon, decision) if value is not None)
            or "—"
        )
        reference = photo.get("reference_label", {})
        reference_text = reference.get("taxon") or reference.get("status") or "—"
        notes = [*photo.get("notes", []), *(latest.get("notes", []) if latest else [])]
        notes_text = "; ".join(str(note) for note in notes) if notes else None
        lines.append(
            "| "
            + " | ".join(
                (
                    _table_text(photo.get("id")),
                    _table_text(photo.get("filename")),
                    _table_text(photo.get("capture_group")),
                    _table_text(reference_text),
                    str(len(runs)),
                    _table_text(latest_result),
                    _table_text(latest.get("escalated") if latest else None),
                    _table_text(latest.get("duration_seconds") if latest else None),
                    _table_text(latest.get("estimated_cost_usd") if latest else None),
                    _table_text(notes_text),
                )
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def regenerate_markdown(manifest_path: Path, markdown_path: Path) -> None:
    markdown_path.write_text(
        render_markdown(load_manifest(manifest_path)),
        encoding="utf-8",
    )


def select_photo_records(
    manifest: dict[str, Any], photo_id: str | None
) -> list[tuple[int, dict[str, Any]]]:
    indexed = list(enumerate(manifest["photos"], start=1))
    if photo_id is None:
        return indexed
    selected = [(index, photo) for index, photo in indexed if photo.get("id") == photo_id]
    if len(selected) != 1:
        raise LedgerError(f"expected exactly one photo with id={photo_id!r}; found {len(selected)}")
    return selected


def verify_image(manifest_dir: Path, photo: dict[str, Any]) -> Path:
    filename = photo.get("filename")
    expected = photo.get("sha256")
    if not isinstance(filename, str) or not isinstance(expected, str):
        raise LedgerError(f"photo {photo.get('id')!r} lacks filename or sha256")
    root = manifest_dir.resolve()
    image = (root / filename).resolve()
    if image.parent != root:
        raise LedgerError(f"photo path escapes manifest directory: {filename!r}")
    if not image.is_file():
        raise LedgerError(f"photo file does not exist: {image}")
    actual = hashlib.sha256(image.read_bytes()).hexdigest()
    if actual != expected.lower():
        raise LedgerError(f"sha256 mismatch for {filename!r}")
    return image


def require_bridge(port: int) -> None:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            return
    except OSError as exc:
        raise LedgerError(f"bridge is not listening on 127.0.0.1:{port}") from exc


def model_call_count(trace: dict[str, Any]) -> int:
    return sum(
        int(call.get("attempts", 1))
        for event in trace.get("events", [])
        for call in event.get("provider_calls", [])
    )


def structured_repair_count(trace: dict[str, Any]) -> int:
    return sum(
        int(call.get("validation_failures", 0))
        for event in trace.get("events", [])
        for call in event.get("provider_calls", [])
    )


def reviewer_disagreement(trace: dict[str, Any]) -> bool | None:
    """Project only what the deterministic trace actually distinguishes."""
    reasons = {str(reason) for reason in trace.get("escalation_reasons", [])}
    if "reviewer_disagreement" in reasons:
        return True
    if "reviewers_disagree_or_critical_finding" in reasons:
        return None
    return False


def completed_run(
    *,
    run_id: str,
    configuration: str,
    payload: dict[str, Any],
    trace_path: str,
    elapsed: float,
) -> dict[str, Any]:
    response = payload.get("response", {})
    decisions = response.get("decisions", [])
    decision = decisions[0] if decisions else {}
    trace = payload.get("trace", {})
    notes: list[str] = []
    if len(decisions) > 1:
        notes.append(
            f"System returned {len(decisions)} subjects; result summarizes the first subject."
        )
    disagreement = reviewer_disagreement(trace)
    if disagreement is None:
        notes.append(
            "Trace combines reviewer disagreement with critical findings; "
            "reviewer disagreement alone is unknown."
        )
    duration_ms = trace.get("duration_ms")
    duration = (
        round(float(duration_ms) / 1000.0, 3) if duration_ms is not None else round(elapsed, 3)
    )
    taxon = decision.get("selected_taxon")
    return {
        "run_id": run_id,
        "configuration": configuration,
        "status": "completed",
        "result": {
            "taxon": taxon,
            "rank": decision.get("resolution"),
            "confidence": decision.get("confidence"),
            "decision": decision.get("status"),
        },
        "escalated": bool(trace.get("escalation_triggered")),
        "abstained": taxon is None,
        "reviewer_disagreement": disagreement,
        "repair_count": structured_repair_count(trace),
        "duration_seconds": duration,
        "model_calls": model_call_count(trace),
        "estimated_cost_usd": None,
        "trace_path": trace_path,
        "notes": notes,
    }


def failed_run(
    *, run_id: str, configuration: str, elapsed: float, exit_code: int
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "configuration": configuration,
        "status": "failed",
        "result": {"taxon": None, "rank": None, "confidence": None, "decision": None},
        "escalated": None,
        "abstained": None,
        "reviewer_disagreement": None,
        "repair_count": 0,
        "duration_seconds": round(elapsed, 3),
        "model_calls": None,
        "estimated_cost_usd": None,
        "trace_path": None,
        "notes": [f"Dendro exited with code {exit_code}; inspect the local run directory."],
    }


def run_photo(
    *,
    root: Path,
    manifest_path: Path,
    photo: dict[str, Any],
    run_id: str,
    configuration: str,
    port: int,
    timeout: float,
    lang: str,
    season: str,
    object_type: str,
) -> dict[str, Any]:
    image = verify_image(manifest_path.parent, photo)
    run_dir = manifest_path.parent / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    executable = root / ".venv" / "Scripts" / "dendro.exe"
    if not executable.is_file():
        raise LedgerError(f"Dendro executable does not exist: {executable}")

    environment = os.environ.copy()
    environment.update(
        {
            "DENDRO_PRIMARY_PROVIDER": "anthropic",
            "DENDRO_REVIEWER_PROVIDER": "anthropic",
            "DENDRO_ARBITER_PROVIDER": "anthropic",
            "DENDRO_PRIMARY_MODEL": "claude-main",
            "DENDRO_REVIEWER_MODEL": "ox-factory",
            "DENDRO_ARBITER_MODEL": "sol-judge",
            "DENDRO_STRUCTURED_RETRIES": "2",
            "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{port}",
            "ANTHROPIC_API_KEY": "bridge-local-placeholder",
            "ANTHROPIC_TIMEOUT_SECONDS": "3600",
        }
    )
    command = [
        str(executable),
        "inspect",
        "--case-id",
        run_id,
        "--image",
        str(image),
        "--lang",
        lang,
        "--season",
        season,
        "--object-type",
        object_type,
        "--trace-out",
        str(run_dir),
        "--json",
    ]
    started = time.monotonic()
    completed = subprocess.run(
        command,
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    elapsed = time.monotonic() - started
    (run_dir / "stdout.json").write_text(completed.stdout, encoding="utf-8")
    (run_dir / "stderr.log").write_text(completed.stderr, encoding="utf-8")
    if completed.returncode != 0:
        return failed_run(
            run_id=run_id,
            configuration=configuration,
            elapsed=elapsed,
            exit_code=completed.returncode,
        )

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise LedgerError(f"Dendro returned non-JSON stdout for {run_id}") from exc
    trace_file = run_dir / f"{run_id}.trace.json"
    if not trace_file.is_file():
        raise LedgerError(f"Dendro did not write the expected trace: {trace_file}")
    trace_path = trace_file.relative_to(manifest_path.parent).as_posix()
    return completed_run(
        run_id=run_id,
        configuration=configuration,
        payload=payload,
        trace_path=trace_path,
        elapsed=elapsed,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--configuration", default="claude_ox_sol_v1")
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--photo-id", help="Run only this manifest photo ID.")
    parser.add_argument("--port", type=int, default=8799)
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=7200.0)
    parser.add_argument("--lang", default="en")
    parser.add_argument("--season", default="unknown")
    parser.add_argument("--object-type", default="unknown")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not CONFIGURATION_ID.fullmatch(args.configuration):
        raise LedgerError("--configuration must be a stable versioned ID")
    if not BATCH_ID.fullmatch(args.batch_id):
        raise LedgerError("--batch-id must contain only lowercase letters, digits, and hyphens")
    if args.limit < 1:
        raise LedgerError("--limit must be at least 1")

    root = Path(__file__).resolve().parents[2]
    manifest_path = args.manifest.resolve()
    markdown_path = manifest_path.with_suffix(".md")
    require_bridge(args.port)
    manifest = load_manifest(manifest_path)
    processed = 0
    for index, photo in select_photo_records(manifest, args.photo_id):
        run_id = f"{args.batch_id}-{index:03d}"
        existing_ids = {
            run.get("run_id")
            for candidate in manifest["photos"]
            for run in candidate.get("runs", [])
            if isinstance(run, dict)
        }
        if run_id in existing_ids:
            continue
        print(f"START {photo.get('id')} {photo.get('filename')} run_id={run_id}", flush=True)
        run = run_photo(
            root=root,
            manifest_path=manifest_path,
            photo=photo,
            run_id=run_id,
            configuration=args.configuration,
            port=args.port,
            timeout=args.timeout,
            lang=args.lang,
            season=args.season,
            object_type=args.object_type,
        )
        append_run(manifest_path, photo_id=str(photo["id"]), run=run)
        regenerate_markdown(manifest_path, markdown_path)
        print(
            f"RECORDED {photo.get('id')} status={run['status']} "
            f"duration={run['duration_seconds']}s",
            flush=True,
        )
        processed += 1
        if run["status"] != "completed":
            print("STOP failed run recorded; remaining photos were not sent", file=sys.stderr)
            return 1
        if processed >= args.limit:
            break
        manifest = load_manifest(manifest_path)
    print(f"FINISHED processed={processed}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (LedgerError, OSError, subprocess.SubprocessError) as exc:
        print(f"ledger runner: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
