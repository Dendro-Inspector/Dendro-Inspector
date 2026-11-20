"""Explicit subject binding over a finished arm V run (experimental, opt-in).

Arm V names nothing when a one-genus answer meets several organisms (``unbound_subjects``).
This driver measures dendro_inspector.evaluation.subject_binding on the records an arm V run
already holds: the answers and the evidence stay that run's own, unchanged, so the binding
step is measured alone. Arm V itself, and every production default, are left as they are.

Three modes:

* Feasibility (the default; no provider is built, nothing is called or written): for every
  planned record of ``--from DIR``, whether a binding call would be made and why not, and per
  eligible unit what binding each organism would reach, and whether the strongest reply a
  binder could give for it passes verification. Both are upper bounds, not a measurement of
  any binder. ``--labels FILE`` (``{"unit": "genus"}``) grades
  arm V and the per-organism bounds with the graph's scorer over all subjects; units without
  a label are listed but not graded.
* ``--live`` (paid model calls): asks the configured primary model once per eligible unit,
  verifies each reply in code and writes ``<out>/cases/<unit>.json`` (arm
  ``validated_bound``) for every planned unit, and ``plan.json``. ``--units`` is the unit
  list the arm V run used; each photograph is checked against the SHA-256 its record holds.
  Resumable: a reused record is never asked again; a failed call or unit is; records of
  another run refuse the run. ``--rounds N`` repeats the whole run N times independently,
  into ``<out>/r<k>/``.
* ``--replay DIR``: re-decides every planned bound record from its recorded reply, with no
  provider. Exit 1 if any decision differs or a record was made under other rules or inputs,
  2 if nothing was replayed.

Usage:
  bind_subjects.py --from DIR [--labels FILE]
  bind_subjects.py --from DIR --units U --out DIR --live [--rounds 1] [--concurrency 4]
  bind_subjects.py --replay DIR
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

# The arm M script's unit loader, so every arm reads units and photographs the same way.
from model_alone import ROOT, commit_sha, load_units


def _knowledge() -> Any:
    from dendro_inspector.config import load_config
    from dendro_inspector.knowledge.loader import KnowledgeBase

    return KnowledgeBase(load_config().knowledge, root=ROOT)


def _grade(decisions: list[dict[str, Any]], genus: str | None, knowledge: Any) -> str:
    from dendro_inspector.evaluation import model_alone, validated
    from dendro_inspector.evaluation.label_scoring import ReferenceLabel, grade_record

    label = model_alone.reference_with_family(knowledge, ReferenceLabel(genus, None, "all"))
    stored = {"result": {"response": {"decisions": decisions}}}
    return grade_record(validated.subject_answers(stored), label).outcome.value


def strongest_reply(answer: Any, packet: Any, root: str) -> Any:
    """The best-supported reply a binder could give for ``root``: every photograph the answer
    cites that shows it, and every observation of it made in those photographs."""
    from dendro_inspector.evaluation import subject_binding, validated

    members = set(validated.organisms(packet)[root])
    shown = subject_binding.organism_images(packet, members)
    answered = {
        i for s in subject_binding.named_subjects(answer) for e in s.evidence for i in e.image_ids
    }
    return subject_binding.BindingReply(
        status=subject_binding.BindingStatus.BOUND,
        subject_id=root,
        supporting_image_ids=tuple(sorted(shown & answered)),
        supporting_observation_ids=tuple(
            o.observation_id
            for o in packet.observations
            if o.subject_id in members and o.image_id in answered
        ),
        distinguishing_evidence="feasibility check",
        abstention_reason=None,
    )


def feasibility(args: argparse.Namespace) -> int:
    from dendro_inspector.evaluation import subject_binding, validated

    knowledge = _knowledge()
    labels = json.loads(args.labels.read_text(encoding="utf-8")) if args.labels else {}
    source = validated.planned_records(args.source)
    reasons: Counter[str] = Counter()
    baseline: Counter[str] = Counter()
    best: Counter[str] = Counter()
    eligible = 0
    for unit, stored in source.records.items():
        if stored is None or "result" not in stored:
            reasons["arm V failed or not run"] += 1
            continue
        record = subject_binding.record(stored, knowledge=knowledge)
        binding = record["result"]["binding"]
        decisions = stored["result"]["response"]["decisions"]
        label = labels.get(unit)
        before = _grade(decisions, label, knowledge) if label else "unlabelled"
        if not binding["eligible"]:
            reasons[binding["ineligible_reason"]] += 1
            baseline[before] += 1
            best[before] += 1
            continue
        eligible += 1
        reasons["eligible"] += 1
        baseline[before] += 1
        answer, packet, image_ids, _ = subject_binding.inputs_of(stored)
        print(f"{unit}: eligible, label {label or 'none'}, arm V {before}")
        reached = []
        for root, decision in subject_binding.oracle(answer, packet, knowledge).items():
            bound = [
                decision.model_dump(mode="json") if d["subject_id"] == root else d
                for d in decisions
            ]
            after = _grade(bound, label, knowledge) if label else "unlabelled"
            reached.append(after)
            said = decision.selected_taxon or f"abstain:{decision.abstention_reason}"
            check, _ = subject_binding.verify(
                strongest_reply(answer, packet, root), packet, answer, image_ids
            )
            print(f"  bound to {root}: {said}  -> {after}; strongest reply verifies as {check}")
        best[next((o for o in ("correct", "wrong") if o in reached), before)] += 1
    print(f"\n{args.source}: {len(source.records)} planned units")
    for reason, count in sorted(reasons.items()):
        print(f"  {reason:<28} {count}")
    print(f"binding calls a live run would make: {eligible} (one per eligible unit)")
    if labels:
        print("graded over all subjects (labels name no target subject):")
        print(f"  arm V as recorded           {dict(sorted(baseline.items()))}")
        print(f"  oracle binding upper bound  {dict(sorted(best.items()))}")
    return 0


def replay(directory: Path) -> int:
    from dendro_inspector.evaluation import subject_binding, validated

    knowledge = _knowledge()
    planned = validated.planned_records(directory)
    print(f"plan: {dict(sorted(subject_binding.tally(planned.records).items()))}")
    found: Counter[str] = Counter()
    for unit, stored in planned.records.items():
        if stored is None or stored.get("arm") != subject_binding.ARM or "result" not in stored:
            continue
        status = subject_binding.replay_status(stored, knowledge)
        found[status.value] += 1
        if status is not subject_binding.ReplayStatus.IDENTICAL:
            print(f"{unit}: {status.value}", flush=True)
    checked = sum(found.values())
    print(f"replayed {checked} bound records with no provider: {dict(sorted(found.items()))}")
    if not checked:
        return 2
    return 0 if found["identical"] == checked else 1


async def live(args: argparse.Namespace) -> int:
    from dendro_inspector.config import Role, load_config
    from dendro_inspector.evaluation import subject_binding, validated
    from dendro_inspector.providers.base import ImageInput
    from dendro_inspector.providers.registry import ProviderRegistry

    source = validated.planned_records(args.source)
    units = {unit["unit_id"]: unit for unit in load_units(args, check=False)}
    config = load_config()
    knowledge = _knowledge()
    registry = ProviderRegistry.from_config(config, root=ROOT)
    provider = registry.get(Role.PRIMARY)
    max_edge = config.graph.image_max_edge_px
    retries = config.provider_for(Role.PRIMARY).max_structured_retries
    plan_path = args.source / validated.PLAN
    identity = subject_binding.run_identity(
        # A run written before plans existed has none; its records still name their commit.
        arm_v=(
            json.loads(plan_path.read_text(encoding="utf-8"))["run_identity"]
            if plan_path.exists()
            else None
        ),
        source=str(args.source.resolve()),
        binder=f"{provider.adapter_name}:{provider.model}",
        code_commit_sha=commit_sha(),
        image_max_edge_px=max_edge,
    )
    gate = asyncio.Semaphore(max(1, args.concurrency))

    def images_for(unit_id: str, stored: dict[str, Any]) -> list[Any]:
        unit = units.get(unit_id)
        if unit is None:
            msg = f"{unit_id} is not in {args.units}"
            raise ValueError(msg)
        recorded = [photo["sha256"] for photo in stored["result"][validated.ARM]["photos"]]
        current = [
            hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "missing"
            for p in unit["photos"]
        ]
        if current != recorded:
            msg = "the photographs differ from the arm V record's"
            raise ValueError(msg)
        return [
            ImageInput(image_id=f"img-{n}", path=path, max_edge_px=max_edge)
            for n, path in enumerate(unit["photos"], start=1)
        ]

    async def one(unit_id: str, stored: dict[str, Any] | None, out: Path) -> None:
        if stored is None or "result" not in stored:
            bound = subject_binding.source_failure(unit_id, stored, identity)
        else:
            arm_v: dict[str, Any] = stored
            async with gate:
                bound = await subject_binding.bind_unit(
                    arm_v,
                    knowledge=knowledge,
                    provider=provider,
                    images=lambda: images_for(unit_id, arm_v),
                    max_retries=retries,
                    identity=identity,
                )
        (out / f"{unit_id}.json").write_text(
            json.dumps(bound, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
        )
        binding = (bound.get("result") or {}).get("binding")
        if binding is None:
            print(f"{out.parent.name}/{unit_id}: FAILED {bound.get('error')}"[:300], flush=True)
        elif binding["asked"]:
            said = [
                (d["subject_id"], d["selected_taxon"] or d["abstention_reason"])
                for d in bound["result"]["response"]["decisions"]
            ]
            print(
                f"{out.parent.name}/{unit_id}: {binding['outcome']} {binding['subject_id']} "
                f"-> {said}" + (f" ERROR {binding['error'][:160]}" if binding["error"] else ""),
                flush=True,
            )

    try:
        for k in range(1, args.rounds + 1):
            out_dir = args.out if args.rounds == 1 else args.out / f"r{k}"
            out = out_dir / "cases"
            out.mkdir(parents=True, exist_ok=True)
            todo, conflicts = [], []
            for unit_id, stored in source.records.items():
                path = out / f"{unit_id}.json"
                existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
                action, why = subject_binding.resume_action(
                    existing,
                    identity=identity,
                    source_sha256=(
                        subject_binding.record_sha256(stored) if stored is not None else None
                    ),
                )
                if action == "refuse":
                    conflicts.append(f"{unit_id}: {why}")
                elif action == "run":
                    todo.append((unit_id, stored))
            if conflicts:
                shown = "\n  ".join(conflicts[:10])
                raise SystemExit(
                    f"{out} holds records of another binding run ({len(conflicts)} units):\n"
                    f"  {shown}\nUse a new --out."
                )
            subject_binding.write_plan(out_dir, list(source.records), identity)
            print(
                f"{out_dir}: {len(source.records)} planned, {len(todo)} to bind or rebind; "
                f"binder {identity['binder']}, rules v{subject_binding.RULES_VERSION}",
                flush=True,
            )
            await asyncio.gather(*(one(u, s, out) for u, s in todo))
            counts = subject_binding.tally(validated.planned_records(out_dir).records)
            print(f"{out_dir}: {dict(sorted(counts.items()))}", flush=True)
    finally:
        await registry.aclose()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--from", dest="source", type=Path, help="the arm V run folder")
    parser.add_argument("--labels", type=Path, help='feasibility: {"unit": "genus"} JSON')
    parser.add_argument("--live", action="store_true", help="make paid binding calls")
    parser.add_argument("--units", type=Path, help="--live: the unit list arm V used")
    parser.add_argument("--out", type=Path, help="--live: the folder to write")
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--replay", type=Path, help="re-decide the bound records in DIR")
    args = parser.parse_args()
    args.manifest = None
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    if args.replay:
        return replay(args.replay)
    if not args.source:
        parser.error("--from is required unless --replay")
    if not args.live:
        return feasibility(args)
    if not (args.units and args.out):
        parser.error("--live needs --units and --out")
    return asyncio.run(live(args))


if __name__ == "__main__":
    raise SystemExit(main())
