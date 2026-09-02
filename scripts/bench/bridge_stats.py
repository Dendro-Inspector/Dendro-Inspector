"""Summarise agent-bridge runs: per-call latency, prompt size and upstream token accounting.

The bridge records one metadata file per pending request and one per answer. Joining them on
the cache key gives a per-call view the graph's own trace cannot: prompt size as sent, and
whatever the upstream agent reported about tokens and cost.

    python scripts/bench/bridge_stats.py <bridge-state-root> [--run <name>]

Reads local state only. It makes no model calls and needs no credentials. See
`docs/agent-as-provider.md` for what the bridge writes and why.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

Row = dict[str, Any]


def _load(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _cost_of(upstream: dict[str, Any]) -> float | None:
    """Sum the upstream's own per-model cost. Never derived from a price table."""
    model_usage = upstream.get("model_usage")
    if not isinstance(model_usage, dict):
        return None
    costs = [
        entry["costUSD"]
        for entry in model_usage.values()
        if isinstance(entry, dict) and isinstance(entry.get("costUSD"), (int, float))
    ]
    return sum(costs) if costs else None


def run_directories(root: Path, only: str | None) -> Iterator[Path]:
    for path in sorted(root.iterdir()):
        if not path.is_dir():
            continue
        if only is not None and path.name != only:
            continue
        if (path / "pending").is_dir() or (path / "answers").is_dir():
            yield path


def collect(run: Path) -> tuple[list[Row], int, int]:
    """Join answered requests to their metadata. Returns rows, requests seen, failures seen."""
    answers: dict[str, dict[str, Any]] = {}
    for path in sorted((run / "answers").glob("*.meta.json")):
        meta = _load(path)
        if meta is not None and isinstance(meta.get("cache_key"), str):
            answers[meta["cache_key"]] = meta

    rows: list[Row] = []
    requests = 0
    for path in sorted((run / "pending").glob("req-*-meta.json")):
        request = _load(path)
        if request is None:
            continue
        requests += 1
        meta = answers.get(request.get("cache_key", ""))
        if meta is None or "started_at" not in meta or "finished_at" not in meta:
            continue
        upstream = meta.get("upstream") or {}
        usage = upstream.get("usage") or {}
        rows.append(
            {
                "run": run.name,
                "route": meta.get("route"),
                "provider": upstream.get("provider"),
                "model": upstream.get("model"),
                "response_model": request.get("response_model"),
                "prompt_chars": request.get("prompt_chars"),
                "duration": meta["finished_at"] - meta["started_at"],
                "input_tokens": usage.get("input_tokens"),
                "cache_read": usage.get("cache_read_input_tokens"),
                "output_tokens": usage.get("output_tokens"),
                "cost": _cost_of(upstream),
            }
        )
    failures = len(list((run / "failures").glob("*.json"))) if (run / "failures").is_dir() else 0
    return rows, requests, failures


def _median(rows: Sequence[Row], field: str) -> float:
    values = [row[field] for row in rows if row.get(field) is not None]
    return statistics.median(values) if values else 0.0


def _p90(rows: Sequence[Row], field: str) -> float:
    values = sorted(row[field] for row in rows if row.get(field) is not None)
    return values[int(0.9 * (len(values) - 1))] if values else 0.0


def grouped_table(rows: Sequence[Row], key: str, title: str) -> None:
    groups: dict[Any, list[Row]] = defaultdict(list)
    for row in rows:
        groups[row.get(key)].append(row)
    print(f"\n{title}")
    header = (
        f"{'':34} {'n':>4} {'dur med':>8} {'dur p90':>8} {'prompt':>9} {'out tok':>8} {'cost':>8}"
    )
    print(header)
    for name, group in sorted(groups.items(), key=lambda item: -len(item[1])):
        print(
            f"{name!s:34} {len(group):4} {_median(group, 'duration'):8.1f} "
            f"{_p90(group, 'duration'):8.1f} {_median(group, 'prompt_chars'):9.0f} "
            f"{_median(group, 'output_tokens'):8.0f} {_median(group, 'cost'):8.3f}"
        )


def latency_fit(rows: Sequence[Row]) -> None:
    """Least-squares fit of duration against output tokens.

    The intercept is the per-call floor that no prompt change touches, and the slope is what
    one more generated token costs in wall time. Both are what make output length, rather
    than input size, the lever worth pulling.
    """
    usable = [row for row in rows if row.get("output_tokens") and row.get("duration")]
    if len(usable) < 10:
        print("\nlatency fit: not enough calls reported output tokens")
        return
    xs = [float(row["output_tokens"]) for row in usable]
    ys = [float(row["duration"]) for row in usable]
    mean_x, mean_y = statistics.mean(xs), statistics.mean(ys)
    covariance = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    variance_x = sum((x - mean_x) ** 2 for x in xs)
    variance_y = sum((y - mean_y) ** 2 for y in ys)
    if variance_x == 0 or variance_y == 0:
        return
    slope = covariance / variance_x
    correlation = covariance / ((variance_x * variance_y) ** 0.5)
    print(
        f"\nlatency fit over {len(usable)} calls: "
        f"duration ≈ {mean_y - slope * mean_x:.1f}s + {slope:.4f}s per output token "
        f"(r = {correlation:.2f})"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="bridge state root holding one directory per run")
    parser.add_argument("--run", default=None, help="restrict to one run directory by name")
    args = parser.parse_args(argv)

    if not args.root.is_dir():
        print(f"not a directory: {args.root}", file=sys.stderr)
        return 2

    rows: list[Row] = []
    print(f"{'run':46} {'requests':>9} {'joined':>7} {'failures':>9}")
    for run in run_directories(args.root, args.run):
        run_rows, requests, failures = collect(run)
        rows.extend(run_rows)
        if requests or failures:
            print(f"{run.name:46} {requests:9} {len(run_rows):7} {failures:9}")

    if not rows:
        print("\nno answered request could be joined to its metadata", file=sys.stderr)
        return 1

    grouped_table(rows, "response_model", "per response model")
    grouped_table(rows, "route", "per route")
    latency_fit(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
