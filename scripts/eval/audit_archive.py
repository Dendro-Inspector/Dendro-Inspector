"""Audit archived run results offline: no model calls, no cost.

    python scripts/eval/audit_archive.py <dir-or-zip> [...] [--json out.json]
        [--input-price 2.0 --cached-price 0.2 --output-price 10.0]

Reads every case record under the given directories or zip files (the control function's
`GET /runs/{id}/cases/{case}` shape, with or without `?full=1`) and reports what can be
checked without ground truth:

* completion and failure classes (structured-output format vs semantic vs other),
  with the failing fields;
* answer/decision consistency, using the same structural checks the pipeline now runs,
  plus the two legacy contradictions seen in the Golden-100 Sonnet 5.5 archive;
* high-confidence policy: every high/95-100 claim, the evidence tier it stood on, and
  whether its nearest alternative was left without a recorded reason;
* resolution, status and abstention distributions;
* latency, tokens and estimated cost, where the archive carries them.

Botanical accuracy is deliberately absent: these photographs have no independent reference
labels, and agreement between two models is not one.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import statistics
import sys
import zipfile
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

BAND_DECISIVE = "95–100/100"
BARK_TIER = 3
FRUIT_SEED_TIER = 7

#: Sentences the pre-fix pipeline rendered deterministically and that deny a 95+ claim.
_DENIAL_OPENERS = ("not claim 95+", "не бив 95+")
_BARK_DENIAL = "cannot confirm one"
_NO_CLAIM_STATUSES = frozenset({"insufficient_evidence", "abstained", "not_a_tree"})

_PYDANTIC_ERROR = re.compile(
    r"^(?P<loc>[A-Za-z0-9_.]+)\s*\n\s+(?P<msg>[^\n]*?)\s*\[type=(?P<type>[a-z_]+)", re.M
)
_STATUS_LINE = re.compile(
    r"case_(?:succeeded|failed) [^\\\"]*?case_id=(?P<case>[A-Za-z0-9_.-]+)[^\\\"]*"
)
_TOKENS = re.compile(r"\b(input_tokens|cached_input_tokens|output_tokens)=([0-9.]+)")


@dataclass
class CaseAudit:
    case_id: str
    source: str
    completed: bool
    failure_class: str | None = None
    failure_fields: tuple[str, ...] = ()
    wall_seconds: float | None = None
    tokens: dict[str, float] = field(default_factory=dict)
    decisions: list[dict[str, Any]] = field(default_factory=list)
    contradictions: list[str] = field(default_factory=list)
    policy_flags: list[str] = field(default_factory=list)


def _token_events(files: dict[str, bytes]) -> dict[str, dict[str, float]]:
    """case_id -> tokens from App Insights `dendro.case_succeeded` events, if archived."""
    tokens: dict[str, dict[str, float]] = {}
    for name, data in files.items():
        if not (name.endswith("events.json") or name.endswith("customEvents.json")):
            continue
        try:
            payload = json.loads(data)
        except ValueError:
            continue
        tables = payload.get("tables") if isinstance(payload, dict) else None
        for table in tables or []:
            columns = [column["name"] for column in table.get("columns", [])]
            if "name" not in columns or "customDimensions" not in columns:
                continue
            for row in table.get("rows", []):
                item = dict(zip(columns, row, strict=False))
                if item.get("name") != "dendro.case_succeeded":
                    continue
                # Strings live in customDimensions, numbers (tokens) in customMeasurements.
                dims: dict[str, Any] = {}
                for column in ("customDimensions", "customMeasurements"):
                    value = item.get(column)
                    if isinstance(value, str) and value:
                        try:
                            value = json.loads(value)
                        except ValueError:
                            value = None
                    if isinstance(value, dict):
                        dims.update(value)
                if "case_id" not in dims:
                    continue
                found: dict[str, float] = {}
                for key in ("input_tokens", "cached_input_tokens", "output_tokens"):
                    try:
                        found[key] = float(dims[key])
                    except (KeyError, TypeError, ValueError):
                        continue
                if found:
                    tokens[str(dims["case_id"])] = found
    return tokens


def _records(
    paths: Iterable[Path],
) -> Iterator[tuple[str, dict[str, Any], str, dict[str, dict[str, float]]]]:
    """(source, record, pod_logs_text, token_events) for every case record found."""
    for path in paths:
        if path.is_dir():
            files = {p.relative_to(path).as_posix(): p.read_bytes() for p in path.rglob("*.json")}
        elif zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as archive:
                files = {
                    name: archive.read(name)
                    for name in archive.namelist()
                    if name.endswith(".json")
                }
        else:
            continue
        logs = {
            name.rsplit("/", 1)[0]: data.decode("utf-8", "replace")
            for name, data in files.items()
            if name.endswith("pod-logs.json")
        }
        all_logs = "\n".join(logs.values())
        chosen: dict[str, tuple[str, dict[str, Any]]] = {}
        for name, data in sorted(files.items()):
            try:
                record = json.loads(data)
            except ValueError:
                continue
            if not isinstance(record, dict) or "case_id" not in record:
                continue
            if "response" not in record and "result" not in record and "error" not in record:
                continue
            # One case per folder: an archive may hold several runs of the same photograph.
            folder = name.rsplit("/", 1)[0] if "/" in name else ""
            key = f"{path.name}:{folder}:{record['case_id']}"
            # Prefer the fuller record when an archive holds both views of one case.
            if key not in chosen or name.endswith(".full.json"):
                chosen[key] = (f"{path.name}/{name}", record)
        events = _token_events(files)
        for source, record in chosen.values():
            yield source, record, all_logs, events


def _response_of(record: dict[str, Any]) -> dict[str, Any] | None:
    if isinstance(record.get("response"), dict):
        return record["response"]
    result = record.get("result")
    if isinstance(result, dict) and isinstance(result.get("response"), dict):
        return result["response"]
    return None


def _classify_failure(error: str) -> tuple[str, tuple[str, ...]]:
    found = [(m["loc"], m["type"]) for m in _PYDANTIC_ERROR.finditer(error)]
    if not found:
        return ("other", ())
    kinds = {kind for _, kind in found}
    klass = "semantic" if kinds <= {"value_error", "assertion_error"} else "format"
    return (klass, tuple(f"{loc}:{kind}" for loc, kind in found))


def _tokens_for(case_id: str, logs: str) -> dict[str, float]:
    tokens: dict[str, float] = {}
    for line in _STATUS_LINE.finditer(logs):
        if line["case"] != case_id or "case_succeeded" not in line.group(0):
            continue
        tokens = {name: float(value) for name, value in _TOKENS.findall(line.group(0))}
    return tokens


def audit_case(
    source: str,
    record: dict[str, Any],
    logs: str,
    token_events: dict[str, dict[str, float]] | None = None,
) -> CaseAudit:
    case_id = str(record["case_id"])
    response = _response_of(record)
    completed = record.get("exit_code") == 0 and response is not None
    audit = CaseAudit(
        case_id=case_id,
        source=source,
        completed=completed,
        wall_seconds=record.get("wall_seconds"),
        tokens=_tokens_for(case_id, logs) or (token_events or {}).get(case_id, {}),
    )
    if not completed:
        audit.failure_class, audit.failure_fields = _classify_failure(
            str(record.get("error") or "")
        )
        return audit

    assert response is not None
    text = response.get("human_readable", "")
    results = {result["subject"]: result for result in response.get("results", [])}
    for decision in response.get("decisions", []):
        subject = decision["subject_id"]
        result = results.get(subject, {})
        audit.decisions.append(decision)
        decisive = decision.get("confidence_band") == BAND_DECISIVE
        high = decision.get("confidence") == "high"

        # Consistency between the structured decision and what the reader is shown.
        if not result:
            audit.contradictions.append(f"decision_without_result:{subject}")
        else:
            for key, other in (
                ("resolution", "taxonomic_resolution"),
                ("confidence", "confidence"),
                ("confidence_band", "confidence_band"),
            ):
                if decision.get(key) != result.get(other):
                    audit.contradictions.append(f"{key}_mismatch:{subject}")
            no_claim = decision.get("status") in _NO_CLAIM_STATUSES
            if not no_claim and decision.get("confidence_band") not in result.get("verdict", ""):
                audit.contradictions.append(f"verdict_omits_band:{subject}")
            if high and any(_BARK_DENIAL in line for line in result.get("limitations", [])):
                audit.contradictions.append(f"bark_denial_on_high_confidence:{subject}")
        if decisive and any(phrase in text for phrase in _DENIAL_OPENERS):
            audit.contradictions.append(f"restraint_voice_with_decisive_band:{subject}")
        audit.contradictions.extend(
            code
            for code in response.get("consistency_issues", [])
            if code not in audit.contradictions
        )

        # High-confidence policy. Flags are prompts for review, not verdicts.
        tier = int(decision.get("evidence_tier") or 0)
        if high and tier <= BARK_TIER:
            audit.policy_flags.append(f"high_from_bark_tier:{subject}")
        if decisive and tier < FRUIT_SEED_TIER:
            audit.policy_flags.append(f"decisive_band_below_fruit_tier:{subject}")
        unaddressed = [
            line for line in result.get("ruled_out", []) if line.endswith(": none recorded")
        ]
        if high and unaddressed:
            audit.policy_flags.append(f"high_with_unaddressed_alternative:{subject}")
        if decision.get("status") in ("identified", "probable") and not decision.get(
            "supporting_evidence"
        ):
            audit.policy_flags.append(f"claim_without_supporting_evidence:{subject}")
    return audit


def summarise(
    audits: list[CaseAudit], *, input_price: float, cached_price: float, output_price: float
) -> dict[str, Any]:
    decisions = [d for audit in audits for d in audit.decisions]
    completed = [audit for audit in audits if audit.completed]
    walls = [audit.wall_seconds for audit in completed if audit.wall_seconds]
    tokens = Counter()
    for audit in completed:
        tokens.update(audit.tokens)
    cost = (
        tokens["input_tokens"] * input_price
        + tokens["cached_input_tokens"] * cached_price
        + tokens["output_tokens"] * output_price
    ) / 1_000_000
    with_tokens = sum(1 for audit in completed if audit.tokens)
    return {
        "cases": len(audits),
        "completed": len(completed),
        "completion_rate": round(len(completed) / len(audits), 3) if audits else None,
        "failures_by_class": dict(Counter(a.failure_class for a in audits if not a.completed)),
        "failing_fields": dict(Counter(f.split(":")[-1] for a in audits for f in a.failure_fields)),
        "decisions": len(decisions),
        "status": dict(Counter(d.get("status") for d in decisions)),
        "resolution": dict(Counter(d.get("resolution") for d in decisions)),
        "confidence": dict(Counter(d.get("confidence") for d in decisions)),
        "abstention_rate": round(
            sum(1 for d in decisions if d.get("status") == "insufficient_evidence")
            / len(decisions),
            3,
        )
        if decisions
        else None,
        "contradictions": dict(
            Counter(code.split(":")[0] for a in audits for code in a.contradictions)
        ),
        "cases_with_contradictions": sum(1 for a in audits if a.contradictions),
        "policy_flags": dict(
            Counter(flag.split(":")[0] for a in audits for flag in a.policy_flags)
        ),
        "median_wall_seconds": statistics.median(walls) if walls else None,
        "tokens": dict(tokens),
        "cases_with_token_counts": with_tokens,
        "estimated_cost_usd": round(cost, 2) if with_tokens else None,
        "estimated_cost_per_completed_case_usd": round(cost / with_tokens, 3)
        if with_tokens
        else None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("archives", nargs="+", type=Path)
    parser.add_argument("--json", type=Path, help="Also write the full audit as JSON.")
    parser.add_argument("--input-price", type=float, default=0.0, help="USD per 1M input tokens")
    parser.add_argument("--cached-price", type=float, default=0.0, help="USD per 1M cached tokens")
    parser.add_argument("--output-price", type=float, default=0.0, help="USD per 1M output tokens")
    args = parser.parse_args(argv)

    audits = [audit_case(*item) for item in _records(args.archives)]
    summary = summarise(
        audits,
        input_price=args.input_price,
        cached_price=args.cached_price,
        output_price=args.output_price,
    )
    out = io.StringIO()
    json.dump(summary, out, indent=2, ensure_ascii=False)
    sys.stdout.write(out.getvalue() + "\n")
    if args.json:
        detail = {
            "summary": summary,
            "cases": [
                {
                    "case_id": a.case_id,
                    "source": a.source,
                    "completed": a.completed,
                    "failure_class": a.failure_class,
                    "failure_fields": list(a.failure_fields),
                    "contradictions": a.contradictions,
                    "policy_flags": a.policy_flags,
                    "wall_seconds": a.wall_seconds,
                    "tokens": a.tokens,
                }
                for a in audits
            ],
        }
        args.json.write_text(json.dumps(detail, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
