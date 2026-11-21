"""Daily sample on the OpenAI allowance: random labelled units until the day's tokens run out.

The control function starts this once a day (timer `openai_daily`, 00:30 UTC, just after
OpenAI resets the free daily allowance) as a single-pod Job on its "openai" route, so the
pod holds the token ledger. For each entry of DAILY_PLAN, in order, it takes the dataset's
units in a random order seeded by the day and the model, and starts a unit only when the
ledger's remaining allowance for that model, less the entry's `keep`, covers the unit's
estimated cost (`calls` model calls of PER_CALL_TOKENS plus PER_PHOTO_TOKENS a photo). A
unit that does not fit is passed over for a smaller one; the entry ends when nothing fits or
`max_units` ran. The ledger still refuses any call past the cap, so a unit that costs more
than estimated stops there and is recorded as stopped by the cap.

Each unit runs through run_upload_case.py as its own run, od<yyyymmdd>-<tag>-<unit>, so its
record is an ordinary upload record (RESULTS_DIR/<run>/cases/<unit>.json) that the results
archive copies into Azure SQL. One line per unit is appended to
RESULTS_DIR/_daily/history.jsonl and the day's summary is RESULTS_DIR/<RUN_ID>/daily.json.
`outcome` there is a rough genus check against the dataset's label (correct, other_name,
no_name, failed, stopped_by_cap); the database's scorer is the reference.

Dataset, RESULTS_DIR/_datasets/<DAILY_DATASET>/units.json:
    [{"unit_id": "birch-1", "genus": "betula", "field_context": true,
      "photos": [{"file": "birch-1/u0963.jpg", "sha256": "<hex>"}, ...]}, ...]

Environment:
    RUN_ID                   the day's run, od<yyyymmdd> (set by the control function)
    RESULTS_DIR              default /results
    DAILY_DATASET            directory under _datasets (default known-units)
    DAILY_PLAN               JSON list, default DEFAULT_PLAN
    DENDRO_OPENAI_BUDGET_DB  the token ledger (set by the openai route); required
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dendro_inspector.providers.daily_budget import LIMITS, remaining

PER_CALL_TOKENS = 20_000
PER_PHOTO_TOKENS = 2_000
# gpt-5.4: one unit, estimated at a single pass (6 calls); a second pass is cut by the cap.
# gpt-5.4-mini: as many units as fit, each estimated with a correction pass (12 calls), and
# 500,000 tokens kept for inspections started by hand that day.
DEFAULT_PLAN: list[dict[str, Any]] = [
    {"model": "gpt-5.4", "tag": "f", "max_units": 1, "calls": 6, "keep": 0},
    {"model": "gpt-5.4-mini", "tag": "m", "max_units": 0, "calls": 12, "keep": 500_000},
]
SAFE_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/ -]{0,200}$")
CAP_REFUSAL = "Daily token limit reached"


@dataclass(frozen=True)
class Step:
    model: str
    tag: str
    max_units: int
    calls: int
    keep: int


def parse_plan(raw: str | None) -> list[Step]:
    entries = json.loads(raw) if raw else DEFAULT_PLAN
    steps = []
    for entry in entries:
        step = Step(
            model=str(entry["model"]),
            tag=str(entry["tag"]),
            max_units=int(entry.get("max_units", 0)),
            calls=int(entry.get("calls", 12)),
            keep=int(entry.get("keep", 0)),
        )
        if step.model not in LIMITS:
            raise ValueError(f"{step.model} is not an allowed model")
        if not re.fullmatch(r"[a-z0-9]{1,4}", step.tag) or step.calls < 1 or step.keep < 0:
            raise ValueError(f"bad plan entry: {entry}")
        steps.append(step)
    return steps


def estimate(unit: dict[str, Any], calls: int) -> int:
    return calls * (PER_CALL_TOKENS + PER_PHOTO_TOKENS * len(unit["photos"]))


def unit_order(units: list[dict[str, Any]], day: str, model: str) -> list[dict[str, Any]]:
    order = list(units)
    random.Random(f"{day}:{model}").shuffle(order)
    return order


def unit_run_id(day: str, tag: str, unit_id: str) -> str:
    """od<yyyymmdd>-<tag>-<unit>, lower case, at most 41 characters (the control API's run id)."""
    slug = re.sub(r"[^a-z0-9-]+", "-", unit_id.lower()).strip("-")
    return f"od{day}-{tag}-{slug}"[:41].rstrip("-")


def genus_of(taxon: str) -> str:
    return taxon.split("_", 1)[0].lower()


def outcome(record: dict[str, Any], genus: str | None) -> tuple[str, list[str]]:
    if record.get("exit_code") != 0:
        failure = json.dumps(record.get("failure") or record.get("error") or "")
        return ("stopped_by_cap" if CAP_REFUSAL in failure else "failed"), []
    response = (record.get("result") or {}).get("response") or {}
    names = [
        d["selected_taxon"] for d in response.get("decisions") or () if d.get("selected_taxon")
    ]
    if not names:
        return "no_name", names
    if genus and genus.lower() in {genus_of(name) for name in names}:
        return "correct", names
    return "other_name", names


def load_units(dataset: Path) -> list[dict[str, Any]]:
    units: list[dict[str, Any]] = json.loads((dataset / "units.json").read_text(encoding="utf-8"))
    for unit in units:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,60}", str(unit.get("unit_id", ""))):
            raise ValueError(f"unusable unit id: {unit.get('unit_id')!r}")
        if not 1 <= len(unit.get("photos") or ()) <= 12:
            raise ValueError(f"{unit['unit_id']}: 1-12 photos")
        for photo in unit["photos"]:
            if not SAFE_FILE.match(photo["file"]) or ".." in photo["file"]:
                raise ValueError(f"{unit['unit_id']}: unusable file {photo['file']!r}")
    return units


def stage(dataset: Path, unit: dict[str, Any], run_dir: Path) -> None:
    """Copy the unit's photographs into <run>/inputs, checked, with an upload manifest."""
    inputs = run_dir / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    photos = []
    for number, photo in enumerate(unit["photos"], start=1):
        source = dataset / photo["file"]
        name = f"photo-{number}{Path(photo['file']).suffix.lower()}"
        if hashlib.sha256(source.read_bytes()).hexdigest() != photo["sha256"]:
            raise ValueError(f"{source} does not match its SHA-256 in units.json")
        shutil.copyfile(source, inputs / name)
        photos.append({"file_name": name, "sha256": photo["sha256"], "original": photo["file"]})
    manifest = {
        "case_id": unit["unit_id"],
        "lang": "en",
        "photos": photos,
        "field_context": unit.get("field_context") is True,
    }
    (inputs / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def tokens_used(record: dict[str, Any]) -> int:
    total = 0
    for event in ((record.get("result") or record.get("failure") or {}).get("trace") or {}).get(
        "events"
    ) or ():
        for call in event.get("provider_calls") or ():
            total += (call.get("input_tokens") or 0) + (call.get("output_tokens") or 0)
    return total


def run_unit(
    results: Path, dataset: Path, unit: dict[str, Any], step: Step, run_id: str
) -> dict[str, Any]:
    run_dir = results / run_id
    target = run_dir / "cases" / f"{unit['unit_id']}.json"
    reused = target.exists()  # the day's Job started again: keep the unit's first record
    if not reused:
        stage(dataset, unit, run_dir)
        env = dict(os.environ, RUN_ID=run_id)
        for role in ("PRIMARY", "REVIEWER", "ARBITER"):
            env[f"DENDRO_{role}_PROVIDER"] = "openai"
            env[f"DENDRO_{role}_MODEL"] = step.model
        worker = Path(__file__).with_name("run_upload_case.py")
        subprocess.run([sys.executable, str(worker)], env=env, check=False)
    record = (
        json.loads(target.read_text(encoding="utf-8"))
        if target.exists()
        else {"exit_code": None, "error": "no record written"}
    )
    result, names = outcome(record, unit.get("genus"))
    return {
        "run_id": run_id,
        "unit_id": unit["unit_id"],
        "model": step.model,
        "label_genus": unit.get("genus"),
        "outcome": result,
        "names": names,
        "photos": len(unit["photos"]),
        "tokens": tokens_used(record),
        "wall_seconds": record.get("wall_seconds"),
        "exit_code": record.get("exit_code"),
        "reused": reused,
    }


def main(env: dict[str, str]) -> int:
    results = Path(env.get("RESULTS_DIR", "/results"))
    ledger = Path(env["DENDRO_OPENAI_BUDGET_DB"])  # no ledger, no run: never uncapped
    day_run = env["RUN_ID"]
    day = datetime.now(UTC).strftime("%Y%m%d")
    dataset = results / "_datasets" / env.get("DAILY_DATASET", "known-units")
    units = load_units(dataset)
    plan = parse_plan(env.get("DAILY_PLAN"))
    summary: dict[str, Any] = {
        "run_id": day_run,
        "day": day,
        "dataset": dataset.name,
        "code_commit_sha": env.get("DENDRO_CODE_COMMIT_SHA"),
        "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "plan": [step.__dict__ for step in plan],
        "units": [],
        "stops": [],
    }
    history = results / "_daily" / "history.jsonl"
    history.parent.mkdir(parents=True, exist_ok=True)
    for step in plan:
        ran = 0
        for unit in unit_order(units, day, step.model):
            if step.max_units and ran >= step.max_units:
                break
            left = remaining(ledger, step.model) - step.keep
            if estimate(unit, step.calls) > left:
                continue  # a smaller unit may still fit
            line = run_unit(
                results, dataset, unit, step, unit_run_id(day, step.tag, unit["unit_id"])
            )
            line["day"] = day
            summary["units"].append(line)
            with history.open("a", encoding="utf-8") as out:
                out.write(json.dumps(line) + "\n")
            print(
                f"{step.model} {unit['unit_id']}: {line['outcome']} {line['names']} "
                f"{line['tokens']} tokens",
                flush=True,
            )
            ran += 1
            if line["outcome"] == "stopped_by_cap":
                break
        left = remaining(ledger, step.model)
        summary["stops"].append({"model": step.model, "units": ran, "remaining_tokens": left})
        print(f"{step.model}: {ran} units, {left} tokens left today", flush=True)
    summary["finished_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    (results / day_run).mkdir(parents=True, exist_ok=True)
    (results / day_run / "daily.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(dict(os.environ)))
    except (KeyError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)
