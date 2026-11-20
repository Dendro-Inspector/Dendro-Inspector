"""Run arm V (the model alone, checked against its card on the graph's own evidence).

Per unit, through the configured providers exactly as an inspection reaches them:

* the namer: arm M's call (dendro_inspector.evaluation.model_alone) on the primary model; or,
  with ``--answers DIR``, the answer an earlier arm M run recorded in ``DIR/cases/<unit>.json``,
  which may come from another model (V x: one model names, the primary model's evidence
  checks it). Its recorded call is copied, so its tokens are counted;
* the evidence: the graph from its entry up to candidate generation (input guard, planner,
  evidence extractor, evidence quality) on the primary model, concurrently with the namer;
* the decision: deterministic code, dendro_inspector.evaluation.validated.decide.

No candidate generation, review, arbitration, correction retry or graph fallback runs.
Production settings are whatever the environment holds; this script changes none.

Units as for model_alone.py (``--manifest`` or ``--units``). Writes ``<out>/cases/<unit_id>.json``
in the stored-case layout for every planned unit: a decided one, a failed run, or a unit
refused before it started (``failure.stage == "setup"``), so a scorer can count every unit.
Resumable: a decided unit is skipped unless ``--force``; a failed one is tried again.

``--replay DIR`` re-decides every arm V record in ``DIR/cases`` from its recorded answer and
evidence, offline and without a provider, and exits 1 if any decision differs.

Usage:
  validated.py (--manifest M [--release-only] | --units U) --out DIR [--answers DIR]
               [--concurrency 4] [--force] [unit_id ...]
  validated.py --replay DIR
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import mimetypes
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# The arm M script's unit loader, so both arms read units and photographs the same way.
from model_alone import ROOT, check_photos, commit_sha, load_units


def case_id_for(unit_id: str) -> str:
    """Inspection IDs are lower-case ``[a-z0-9_-]``; held-out unit IDs may not be."""
    case_id = re.sub(r"[^a-z0-9_-]", "-", unit_id.lower()).strip("-")
    if not case_id:
        msg = f"unit {unit_id!r} has no usable case ID"
        raise ValueError(msg)
    return case_id


def _photo_sha256(path: Path) -> str:
    """The photograph as it is now; a missing one cannot match any record."""
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "missing"


def replay(directory: Path) -> int:
    from dendro_inspector.config import load_config
    from dendro_inspector.evaluation import validated
    from dendro_inspector.knowledge.loader import KnowledgeBase

    knowledge = KnowledgeBase(load_config().knowledge, root=ROOT)
    summary = validated.replay_run(directory, knowledge)
    for name in summary.differing:
        print(f"{name}: replayed decisions differ from the record", flush=True)
    if not summary.checked:
        print(f"no arm V record with a decision in {directory / 'cases'}: nothing was replayed")
    else:
        print(
            f"replayed {summary.checked} records: "
            f"{summary.checked - len(summary.differing)} identical, {len(summary.differing)} differ"
        )
    return summary.exit_code


async def run(args: argparse.Namespace) -> int:
    from dendro_inspector.config import Role, load_config
    from dendro_inspector.evaluation import model_alone, validated
    from dendro_inspector.observability.identity import runtime_identity
    from dendro_inspector.prompts.library import load_domain_prompt, resolve_prompt_path
    from dendro_inspector.providers.base import ImageInput
    from dendro_inspector.runner import build_context
    from dendro_inspector.schemas.input import CaseInput, ImageRef

    units = load_units(args, check=False)  # photographs are checked per unit, below
    planned = [unit["unit_id"] for unit in units]  # the plan is the whole list, always
    if args.unit_ids:
        wanted = set(args.unit_ids)
        units = [unit for unit in units if unit["unit_id"] in wanted]
        missing = wanted - {unit["unit_id"] for unit in units}
        if missing:
            raise SystemExit(f"not in the unit list: {sorted(missing)}")
    out = args.out / "cases"
    out.mkdir(parents=True, exist_ok=True)

    config = load_config()
    domain = load_domain_prompt(resolve_prompt_path(ROOT, config.prompts.domain_prompt_path))
    max_edge = config.graph.image_max_edge_px
    retries = config.provider_for(Role.PRIMARY).max_structured_retries
    sha = commit_sha()
    probe = build_context(config, root=ROOT, case_id="identity")
    primary = probe.providers.get(Role.PRIMARY)
    extractor = f"{primary.adapter_name}:{primary.model}"
    await probe.providers.aclose()
    settings = runtime_identity(config, root=ROOT)
    identity = validated.run_identity(
        code_commit_sha=sha,
        config_sha256=settings.config_sha256,
        knowledge_sha256=settings.knowledge_sha256,
        namer=f"answers:{args.answers.resolve()}" if args.answers else f"live:{extractor}",
        extractor=extractor,
        domain_prompt_sha256=domain.sha256,
        image_max_edge_px=max_edge,
    )

    # Every planned unit ends with a record; a failed one is tried again on the next run. A
    # decided one is kept only if it came from this same run, so a folder never mixes runs.
    todo, conflicts = [], []
    for unit in units:
        path = out / f"{unit['unit_id']}.json"
        if args.force or not validated.decided(path):
            todo.append(unit)
            continue
        differ = validated.resume_conflicts(
            json.loads(path.read_text(encoding="utf-8")),
            identity,
            photo_sha256=[_photo_sha256(p) for p in unit["photos"]],
            field_context=unit["field_context"],
        )
        action = validated.resume_action(differ)
        if action == "rerun":  # this unit's inputs changed: run it again, alone
            print(f"{unit['unit_id']}: {', '.join(differ)} changed since its record", flush=True)
            todo.append(unit)
        elif action == "refuse":
            conflicts.append(f"{unit['unit_id']}: {', '.join(differ)}")
    if conflicts:
        shown = "\n  ".join(conflicts[:10])
        raise SystemExit(
            f"{out} holds decided records from another run ({len(conflicts)} units):\n"
            f"  {shown}\nUse a new --out, or --force to run every unit again."
        )
    validated.write_plan(args.out, planned, identity)
    outside = validated.planned_records(args.out).outside_plan
    if outside:
        print(
            f"{len(outside)} records in {out} are not in this plan; replay and scoring ignore "
            f"them: {', '.join(outside[:10])}",
            flush=True,
        )
    print(
        f"{len(units)} units, {len(todo)} to run, {args.concurrency} at a time; rules "
        f"v{validated.RULES_VERSION}; namer "
        f"{'records in ' + str(args.answers) if args.answers else 'the primary model'}",
        flush=True,
    )
    gate = asyncio.Semaphore(max(1, args.concurrency))
    failures = 0

    async def one(unit: dict[str, Any]) -> None:
        nonlocal failures
        started = datetime.now(UTC).isoformat(timespec="seconds")
        try:
            await one_unit(unit)
        except Exception as exc:  # a unit that cannot start fails alone, with its own record
            failures += 1
            error = f"{type(exc).__name__}: {exc}"[:2000]
            stored = validated.setup_failure(
                unit_id=unit["unit_id"], error=error, started_at=started, code_commit_sha=sha
            )
            (out / f"{unit['unit_id']}.json").write_text(
                json.dumps(stored, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            print(f"{unit['unit_id']}: SETUP FAILED {error}"[:300], flush=True)

    async def one_unit(unit: dict[str, Any]) -> None:
        nonlocal failures
        async with gate:
            started = datetime.now(UTC).isoformat(timespec="seconds")
            check_photos(unit)
            case_id = case_id_for(unit["unit_id"])
            photos = [
                {"name": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                for path in unit["photos"]
            ]
            case = CaseInput(
                case_id=case_id,
                images=tuple(
                    ImageRef(
                        image_id=f"img-{number}",
                        path=path,
                        media_type=mimetypes.guess_type(path.name)[0] or "image/jpeg",
                    )
                    for number, path in enumerate(unit["photos"], start=1)
                ),
                user_has_field_context=unit["field_context"],
                metadata={"locale": "en"},
            )
            recorded = None
            if args.answers:
                earlier = json.loads(
                    (args.answers / "cases" / f"{unit['unit_id']}.json").read_text(encoding="utf-8")
                )
                # Refuses (this unit fails alone) unless the earlier answer was given to these
                # photographs, in this order, under the same statement, request and prompt.
                recorded = validated.answer_from_record(
                    earlier,
                    photo_sha256=[photo["sha256"] for photo in photos],
                    field_context=unit["field_context"],
                    domain_prompt_sha256=domain.sha256,
                )
            ctx = build_context(config, root=ROOT, case_id=case_id)
            provider = ctx.providers.get(Role.PRIMARY)
            namer_call = None
            if recorded is not None:
                stored_answer = recorded.answer
                adapter, model, source = recorded.adapter, recorded.model, recorded.source
                namer_call = recorded.call

                async def namer() -> Any:
                    return stored_answer, None, 0.0

            else:
                images = [
                    ImageInput(image_id=f"img-{number}", path=path, max_edge_px=max_edge)
                    for number, path in enumerate(unit["photos"], start=1)
                ]
                adapter, model, source = provider.adapter_name, provider.model or "", "live"

                async def namer() -> Any:
                    return await model_alone.ask(
                        provider,
                        domain_prompt=domain.text,
                        images=images,
                        field_context=unit["field_context"],
                        max_retries=retries,
                    )

            try:
                stored = await validated.run_unit(
                    case,
                    ctx,
                    namer,
                    unit_id=unit["unit_id"],
                    answer_source=source,
                    namer_model=model,
                    namer_adapter=adapter,
                    namer_call=namer_call,
                    photos=photos,
                    field_context=unit["field_context"],
                    domain_prompt_sha256=domain.sha256,
                    image_max_edge_px=max_edge,
                    code_commit_sha=sha,
                    started_at=started,
                    identity=identity,
                )
            finally:
                await ctx.providers.aclose()
            (out / f"{unit['unit_id']}.json").write_text(
                json.dumps(stored, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            if "result" not in stored:
                failures += 1
                print(f"{unit['unit_id']}: FAILED {stored.get('error')}"[:300], flush=True)
                return
            said = "; ".join(
                f"{d['subject_id']}={d['selected_taxon'] or 'abstained:' + d['abstention_reason']}"
                for d in stored["result"]["response"]["decisions"]
            )
            arm = stored["result"][validated.ARM]
            print(
                f"{unit['unit_id']}: {said}  {arm['wall_seconds'].get('total')}s "
                f"tokens={arm['tokens']}",
                flush=True,
            )

    await asyncio.gather(*(one(unit) for unit in todo))
    print(f"done: {len(todo) - failures} decided, {failures} failed", flush=True)
    return 4 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--manifest", type=Path, help="held-out manifest (YAML)")
    source.add_argument("--units", type=Path, help="JSON list of units")
    source.add_argument("--replay", type=Path, help="re-decide the arm V records in DIR/cases")
    parser.add_argument(
        "--release-only",
        action="store_true",
        help="with --manifest: only reviewed or resolved units",
    )
    parser.add_argument("--out", type=Path)
    parser.add_argument(
        "--answers", type=Path, help="arm M run folder whose recorded answers name the units"
    )
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("unit_ids", nargs="*")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    if args.replay:
        return replay(args.replay)
    if args.out is None:
        parser.error("--out is required unless --replay")
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
