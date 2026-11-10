"""Summarize dataset or upload run records, including projected API exports.

Reads <run_dir>/cases/*.json and writes <run_dir>/summary.json and <run_dir>/cases.csv:
one row per case (verdict, confidence, arbiter use, tokens, time) plus run totals.
Legacy verdict fields describe the first subject; subject_summary counts every subject
in successful cases. Each CSV row also carries subject_decisions as a JSON array.
Projected API records have no trace, so usage and model-call counts remain unknown.
Standard library only.

Usage: python scripts/batch/summarize_run.py <run_dir>
"""

from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from pathlib import Path
from statistics import median
from typing import Any

COLUMNS = (
    "case_id",
    "exit_code",
    "wall_seconds",
    "subjects",
    "selected_taxon",
    "status",
    "resolution",
    "confidence",
    "final_resolution",
    "final_confidence",
    "arbiter_used",
    "escalation_triggered",
    "model_calls",
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "error",
    "subject_decisions",
)
DECISION_COLUMNS = ("subject_id", "selected_taxon", "status", "resolution", "confidence")
TOKEN_KEYS = ("input_tokens", "cached_input_tokens", "output_tokens")


def _last_line(text: str | None) -> str:
    lines = (text or "").strip().splitlines()
    return lines[-1] if lines else ""


def case_row(record: dict[str, Any]) -> dict[str, Any]:
    """Flatten one case record into a CSV row. Missing parts stay empty."""
    row: dict[str, Any] = dict.fromkeys(COLUMNS, "")
    row.update(
        case_id=record.get("case_id", ""),
        exit_code=record.get("exit_code", ""),
        wall_seconds=record.get("wall_seconds", ""),
    )
    row["error"] = _last_line(record.get("error"))
    row["subject_decisions"] = []
    result = record.get("result") or {}
    response = result.get("response")
    if response is None:
        response = record.get("response")
    if response is None:
        return row
    decisions = response.get("decisions") or []
    first = decisions[0] if decisions else {}
    trace = result.get("trace") or {}
    calls = [
        call for event in trace.get("events", []) for call in event.get("provider_calls") or []
    ]
    row.update(
        subjects=len(decisions),
        selected_taxon=first.get("selected_taxon") or "",
        status=first.get("status") or "",
        resolution=first.get("resolution") or "",
        confidence=first.get("confidence") or "",
        final_resolution=trace.get("final_resolution") or "",
        final_confidence=trace.get("final_confidence") or "",
        arbiter_used=trace.get("arbiter_used"),
        escalation_triggered=trace.get("escalation_triggered"),
        model_calls=len(calls) if "events" in trace else None,
        subject_decisions=[
            {key: decision.get(key) for key in DECISION_COLUMNS} for decision in decisions
        ],
        **{key: _measured_total(calls, key) for key in TOKEN_KEYS},
    )
    return row


def _measured_total(records: list[dict[str, Any]], key: str) -> int | None:
    values = [record.get(key) for record in records]
    measured = [v for v in values if isinstance(v, int) and not isinstance(v, bool) and v >= 0]
    return sum(measured) if measured else None


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    done = [row for row in rows if row["exit_code"] == 0]
    walls = sorted(float(row["wall_seconds"]) for row in done if row["wall_seconds"] != "")
    decisions = [decision for row in done for decision in row.get("subject_decisions", [])]
    return {
        "cases": len(rows),
        "succeeded": len(done),
        "failed": len(rows) - len(done),
        "status": dict(Counter(row["status"] for row in done)),
        "resolution": dict(Counter(row["resolution"] for row in done)),
        "confidence": dict(Counter(row["confidence"] for row in done)),
        "top_taxa": dict(Counter(row["selected_taxon"] for row in done).most_common(15)),
        "arbiter_used": sum(1 for row in done if row["arbiter_used"] is True),
        # Legacy fields above describe the first subject of each successful case.
        # Keep all subject outcomes separately; usage and durations remain per case.
        "subject_summary": {
            "subjects": len(decisions),
            "status": dict(Counter(d.get("status") or "" for d in decisions)),
            "resolution": dict(Counter(d.get("resolution") or "" for d in decisions)),
            "confidence": dict(Counter(d.get("confidence") or "" for d in decisions)),
            "top_taxa": dict(
                Counter(d.get("selected_taxon") or "" for d in decisions).most_common(15)
            ),
        },
        # Failed cases can consume tokens too. Missing measurements remain unknown.
        "tokens": {key: _measured_total(rows, key) for key in TOKEN_KEYS},
        "token_measured_cases": {
            key: sum(_measured_total([row], key) is not None for row in rows) for key in TOKEN_KEYS
        },
        "wall_seconds": {
            "median": median(walls) if walls else None,
            "max": walls[-1] if walls else None,
        },
    }


def main(run_dir: Path) -> dict[str, Any]:
    records = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((run_dir / "cases").glob("*.json"))
    ]
    rows = [case_row(record) for record in records]
    with (run_dir / "cases.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(
            {**row, "subject_decisions": json.dumps(row["subject_decisions"], ensure_ascii=False)}
            for row in rows
        )
    summary = summarize(rows)
    if records:
        summary["run_id"] = records[0].get("run_id")
        dataset = records[0].get("dataset")
        summary["dataset"] = (
            {key: dataset.get(key) for key in ("repo", "revision")} if dataset else None
        )
        summary["models"] = records[0].get("models")
    (run_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    print(json.dumps(main(Path(sys.argv[1])), ensure_ascii=False, indent=2))
