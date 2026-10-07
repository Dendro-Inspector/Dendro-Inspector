"""Summarise run traces: where a run spends its time, and what it spent.

Reads the `RunTrace` JSON that `dendro inspect --trace-out <dir>` writes, so it needs no
credentials and makes no model calls. The tables it prints are the ones
`docs/specs/latency-and-cost.md` is argued from; re-run it after any change that claims to
make the graph faster.

    python scripts/bench/trace_stats.py <directory> [--glob '*.trace.json']

Every rate is printed beside the count it came from. A median over four runs is a statement
about four runs, and a table that hides its denominator invites the reader to forget that.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Any

MS_PER_S = 1000.0


def load_traces(root: Path, pattern: str) -> list[dict[str, Any]]:
    """Every readable trace under ``root``. An unparseable file is named, not skipped silently."""
    traces: list[dict[str, Any]] = []
    for path in sorted(root.rglob(pattern)):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"  ! unreadable: {path} ({exc})", file=sys.stderr)
            continue
        if isinstance(payload, dict) and "events" in payload:
            traces.append(payload)
    return traces


def percentile(values: Sequence[float], fraction: float) -> float:
    """Nearest-rank percentile. Empty input is a caller error, not a zero."""
    ordered = sorted(values)
    return ordered[int(fraction * (len(ordered) - 1))]


def _seconds(value: float | None) -> float:
    return (value or 0.0) / MS_PER_S


def node_table(traces: Sequence[dict[str, Any]]) -> None:
    durations: dict[str, list[float]] = defaultdict(list)
    for trace in traces:
        for event in trace["events"]:
            if event.get("duration_ms") is not None:
                durations[event["node"]].append(_seconds(event["duration_ms"]))
    if not durations:
        return

    totals = [_seconds(trace.get("duration_ms")) for trace in traces]
    median_run = statistics.median(totals) or 1.0
    print(f"\n{'node':30} {'n':>4} {'med':>8} {'p90':>8} {'max':>9}   share")
    for node, values in sorted(durations.items(), key=lambda item: -statistics.median(item[1])):
        median = statistics.median(values)
        print(
            f"{node:30} {len(values):4} {median:8.1f} {percentile(values, 0.9):8.1f} "
            f"{max(values):9.1f}   {100 * median / median_run:5.1f}%"
        )


def run_table(traces: Sequence[dict[str, Any]]) -> None:
    totals = [_seconds(trace.get("duration_ms")) for trace in traces]
    critical = [
        _seconds(trace["critical_path_ms"])
        for trace in traces
        if trace.get("critical_path_ms") is not None
    ]
    calls = [
        sum(len(event.get("provider_calls", [])) for event in trace["events"]) for trace in traces
    ]
    attempts = [
        sum(
            call.get("attempts", 1)
            for event in trace["events"]
            for call in event.get("provider_calls", [])
        )
        for trace in traces
    ]

    print(f"\nruns: {len(traces)}")
    print(
        f"  wall time s          med {statistics.median(totals):8.1f}  "
        f"p90 {percentile(totals, 0.9):8.1f}"
    )
    if critical:
        print(
            f"  critical path s      med {statistics.median(critical):8.1f}  "
            f"p90 {percentile(critical, 0.9):8.1f}   [{len(critical)} of {len(traces)} traces]"
        )
    else:
        print("  critical path s      not recorded in these traces")
    print(f"  model calls          med {statistics.median(calls):8.1f}  max {max(calls):8.1f}")
    print(
        f"  provider attempts    med {statistics.median(attempts):8.1f}  max {max(attempts):8.1f}"
    )
    arbitrated = sum(1 for trace in traces if trace.get("arbiter_used"))
    retried = sum(1 for trace in traces if trace.get("retries"))
    print(f"  arbiter used         {arbitrated} of {len(traces)}")
    print(f"  retried              {retried} of {len(traces)}")


def token_table(traces: Sequence[dict[str, Any]]) -> None:
    """Only meaningful once adapters report usage; silent rather than zero when they do not."""
    by_node: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trace in traces:
        for event in trace["events"]:
            for call in event.get("provider_calls", []):
                if call.get("output_tokens") is not None or call.get("input_tokens") is not None:
                    by_node[event["node"]].append(call)
    if not by_node:
        print("\nno provider call reported token usage in these traces")
        return

    print(f"\n{'node':30} {'n':>4} {'in':>9} {'cached':>9} {'out':>9} {'cost usd':>9}")
    for node, calls in sorted(by_node.items(), key=lambda item: -len(item[1])):
        print(
            f"{node:30} {len(calls):4} {_format_median(calls, 'input_tokens', 9)} "
            f"{_format_median(calls, 'cached_input_tokens', 9)} "
            f"{_format_median(calls, 'output_tokens', 9)} "
            f"{_format_median(calls, 'reported_cost_usd', 9, precision=3)}"
        )


def _median_field(rows: Sequence[dict[str, Any]], field: str) -> float | None:
    """Median of a reported field, or ``None`` when nobody reported it."""
    values = [row[field] for row in rows if row.get(field) is not None]
    return statistics.median(values) if values else None


def _format_median(
    rows: Sequence[dict[str, Any]], field: str, width: int, *, precision: int = 0
) -> str:
    value = _median_field(rows, field)
    return f"{'n/a':>{width}}" if value is None else f"{value:{width}.{precision}f}"


def escalation_table(traces: Sequence[dict[str, Any]]) -> None:
    reasons: dict[str, int] = defaultdict(int)
    for trace in traces:
        for reason in trace.get("escalation_reasons", []):
            reasons[reason] += 1
    if not reasons:
        return
    print(f"\nescalation reasons, over {len(traces)} runs")
    for reason, count in sorted(reasons.items(), key=lambda item: -item[1]):
        print(f"  {reason:40} {count:4}")


def arbiter_value_table(traces: Sequence[dict[str, Any]]) -> None:
    """Report whether arbitration changed the verdict, grouped by every trigger that fired."""
    fields = ("status", "taxon", "resolution", "confidence")
    by_reason: dict[str, list[dict[str, bool]]] = defaultdict(list)
    for trace in traces:
        raw_changes = {field: trace.get(f"arbiter_changed_{field}") for field in fields}
        if not all(isinstance(value, bool) for value in raw_changes.values()):
            continue
        changes = {field: bool(raw_changes[field]) for field in fields}
        for reason in trace.get("escalation_reasons", []):
            by_reason[reason].append(changes)

    if not by_reason:
        print("\nno traces recorded an arbiter verdict comparison")
        return

    print(
        f"\n{'arbiter verdict changes by trigger':40} {'n':>4} {'any':>5} "
        f"{'rate':>7} {'status':>7} {'taxon':>7} {'res':>5} {'conf':>5}"
    )
    for reason, rows in sorted(by_reason.items(), key=lambda item: (-len(item[1]), item[0])):
        changed = sum(any(row.values()) for row in rows)
        per_field = {field: sum(row[field] for row in rows) for field in fields}
        print(
            f"{reason:40} {len(rows):4} {changed:5} {100 * changed / len(rows):6.1f}% "
            f"{per_field['status']:7} {per_field['taxon']:7} "
            f"{per_field['resolution']:5} {per_field['confidence']:5}"
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="directory of traces, searched recursively")
    parser.add_argument("--glob", default="*.trace.json", help="trace filename pattern")
    args = parser.parse_args(argv)

    if not args.directory.is_dir():
        print(f"not a directory: {args.directory}", file=sys.stderr)
        return 2

    traces = load_traces(args.directory, args.glob)
    if not traces:
        print(f"no traces matching {args.glob!r} under {args.directory}", file=sys.stderr)
        return 1

    run_table(traces)
    node_table(traces)
    token_table(traces)
    escalation_table(traces)
    arbiter_value_table(traces)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
