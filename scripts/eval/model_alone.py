"""Run arm M of the v1 held-out protocol (the model alone) on a set of units.

One structured call per unit through the configured primary provider, exactly as a graph
run would reach the model: DENDRO_PRIMARY_PROVIDER / DENDRO_PRIMARY_MODEL and the adapter's
own environment pick it, DENDRO_IMAGE_MAX_EDGE_PX prepares the photographs. The request and
answer schema are in dendro_inspector.evaluation.model_alone; each record names their hashes.

Units: ``--manifest heldout.yaml`` (photographs relative to the manifest; every unit, or the
release-ready ones with ``--release-only``) or ``--units units.json``, a list of
``{"unit_id", "photos": [paths], "field_context": bool}``. Every photograph's SHA-256 is
recorded; ``--units`` entries may carry ``sha256`` per photo, which is then checked first.

Writes ``<out>/cases/<unit_id>.json`` in the stored-case layout, so sync_results_sql.py loads
and grades it like any run. Resumable: a unit with a record is skipped unless ``--force``.

Usage:
  model_alone.py (--manifest M [--release-only] | --units U) --out DIR
                 [--concurrency 4] [--force] [unit_id ...]
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def load_units(args: argparse.Namespace) -> list[dict[str, Any]]:
    if args.manifest:
        from dendro_inspector.evaluation import heldout

        manifest = heldout.load_manifest(args.manifest)
        base = args.manifest.resolve().parent
        return [
            {
                "unit_id": unit.unit_id,
                "photos": [base / name for name in unit.photos],
                "field_context": False,
            }
            for unit in manifest.units
            if not args.release_only or heldout.release_ready(unit)
        ]
    units = json.loads(args.units.read_text(encoding="utf-8"))
    found = []
    for unit in units:
        photos = []
        for photo in unit["photos"]:
            path = Path(photo["path"] if isinstance(photo, dict) else photo)
            expected = photo.get("sha256") if isinstance(photo, dict) else None
            if expected and hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise SystemExit(f"{unit['unit_id']}: {path.name} is not the photograph recorded")
            photos.append(path)
        found.append(
            {
                "unit_id": unit["unit_id"],
                "photos": photos,
                "field_context": bool(unit.get("field_context")),
            }
        )
    return found


def commit_sha() -> str | None:
    try:
        done = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except OSError:
        return None
    return done.stdout.strip() or None


async def run(args: argparse.Namespace) -> int:
    from dendro_inspector.config import Role, load_config
    from dendro_inspector.evaluation import model_alone
    from dendro_inspector.prompts.library import load_domain_prompt, resolve_prompt_path
    from dendro_inspector.providers.base import ImageInput
    from dendro_inspector.providers.registry import ProviderRegistry

    units = load_units(args)
    if args.unit_ids:
        wanted = set(args.unit_ids)
        units = [unit for unit in units if unit["unit_id"] in wanted]
        missing = wanted - {unit["unit_id"] for unit in units}
        if missing:
            raise SystemExit(f"not in the unit list: {sorted(missing)}")
    out = args.out / "cases"
    out.mkdir(parents=True, exist_ok=True)
    todo = [u for u in units if args.force or not (out / f"{u['unit_id']}.json").exists()]

    config = load_config()
    domain = load_domain_prompt(resolve_prompt_path(ROOT, config.domain_prompt_path))
    registry = ProviderRegistry.from_config(config, root=ROOT)
    provider = registry.get(Role.PRIMARY)
    max_edge = config.graph.image_max_edge_px
    retries = config.provider_for(Role.PRIMARY).max_structured_retries
    sha = commit_sha()
    print(
        f"{len(units)} units, {len(todo)} to run on {provider.adapter_name}:{provider.model}, "
        f"{args.concurrency} at a time; request {model_alone.REQUEST_SHA256[:12]}, schema "
        f"{model_alone.SCHEMA_SHA256[:12]}, domain prompt {domain.sha256[:12]}",
        flush=True,
    )

    gate = asyncio.Semaphore(max(1, args.concurrency))
    failures = 0

    async def one(unit: dict[str, Any]) -> None:
        nonlocal failures
        async with gate:
            started = datetime.now(UTC).isoformat(timespec="seconds")
            photos = [
                {"name": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                for path in unit["photos"]
            ]
            images = [
                ImageInput(image_id=f"img-{number}", path=path, max_edge_px=max_edge)
                for number, path in enumerate(unit["photos"], start=1)
            ]
            answer = usage = None
            error = None
            wall = 0.0
            try:
                answer, usage, wall = await model_alone.ask(
                    provider,
                    domain_prompt=domain.text,
                    images=images,
                    field_context=unit["field_context"],
                    max_retries=retries,
                )
            except Exception as exc:  # recorded as a failed case; the others go on
                error = f"{type(exc).__name__}: {exc}"[:2000]
                failures += 1
            stored = model_alone.record(
                answer=answer,
                usage=usage,
                wall_seconds=wall,
                adapter=provider.adapter_name,
                model=provider.model,
                unit_id=unit["unit_id"],
                photos=photos,
                field_context=unit["field_context"],
                domain_prompt_sha256=domain.sha256,
                image_max_edge_px=max_edge,
                code_commit_sha=sha,
                started_at=started,
                error=error,
            )
            (out / f"{unit['unit_id']}.json").write_text(
                json.dumps(stored, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            if answer is None:
                print(f"{unit['unit_id']}: FAILED {error}"[:300], flush=True)
                return
            said = "; ".join(
                f"{d['selected_taxon'] or 'cannot be determined'} ({d['confidence_score']})"
                for d in stored["result"]["response"]["decisions"]
            )
            print(
                f"{unit['unit_id']}: {said}  {wall}s in={usage.input_tokens if usage else None}"
                f" out={usage.output_tokens if usage else None}",
                flush=True,
            )

    try:
        await asyncio.gather(*(one(unit) for unit in todo))
    finally:
        await registry.aclose()
    print(f"done: {len(todo) - failures} answered, {failures} failed", flush=True)
    return 4 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--manifest", type=Path, help="held-out manifest (YAML)")
    source.add_argument("--units", type=Path, help="JSON list of units")
    parser.add_argument(
        "--release-only",
        action="store_true",
        help="with --manifest: only reviewed or resolved units",
    )
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("unit_ids", nargs="*")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
