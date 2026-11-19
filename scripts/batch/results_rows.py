"""Rows for the results database (Azure SQL) from one stored case record.

Standard library only. One source for two writers: the control function, which copies this
file in at deploy as it copies summarize_run.py, and scripts/batch/sync_results_sql.py on a
workstation. A case archived by either therefore gets the same key and the same columns.
The tables are defined in results-schema.sql beside this file.

A record is what the cluster stores for a case (``<run_id>/cases/<case_id>.json`` on the
results share: ``run_id``, ``case_id``, ``models``, ``upload``, ``started_at``,
``wall_seconds``, ``exit_code``, ``result``) or what a local run script writes
(``exit_code``, ``wall_seconds``, ``result`` and the script's own fields, such as
``vibe_cost_usd``). Both carry ``result.response`` and ``result.trace`` when the case ran;
a failed case may carry ``failure`` instead of ``result``.

Writing uses DB-API ``?`` parameters (mssql-python, pyodbc) on a cursor the caller owns, so
the caller decides when to commit.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

SOURCES = ("cluster", "local")

CASE_COLUMNS = (
    "case_key",
    "source",
    "run_id",
    "case_id",
    "exit_code",
    "succeeded",
    "code_sha",
    "code_dirty",
    "package_version",
    "config_sha256",
    "knowledge_sha256",
    "review_authority",
    "selective_arbitration",
    "models",
    "photos",
    "subjects",
    "target_subject_id",
    "model_calls",
    "input_tokens",
    "output_tokens",
    "cached_input_tokens",
    "reported_cost_usd",
    "wall_seconds",
    "started_at",
    "record",
)

DECISION_COLUMNS = (
    "case_key",
    "subject_id",
    "is_target",
    "selected_taxon",
    "resolution",
    "confidence",
    "status",
    "visual_hypothesis",
    "best_next_photo",
)

#: Longest key the schema stores (``cases.case_key`` is nvarchar(300)).
MAX_KEY = 300


def case_key(source: str, run_id: str, case_id: str) -> str:
    """``<source>:<run_id>/<case_id>``: the cluster's run and case ids, or a local run's
    collection (its folder under runs/) and the unit."""
    if source not in SOURCES:
        raise ValueError(f"source must be one of {SOURCES}, not {source!r}")
    key = f"{source}:{run_id}/{case_id}"
    if len(key) > MAX_KEY:
        raise ValueError(f"case key longer than {MAX_KEY} characters: {key[:80]}...")
    return key


def _result(record: Mapping[str, Any]) -> Mapping[str, Any]:
    result = record.get("result") or record.get("failure") or {}
    return result if isinstance(result, Mapping) else {}


def _calls(trace: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [
        call
        for event in trace.get("events") or ()
        if isinstance(event, Mapping)
        for call in event.get("provider_calls") or ()
        if isinstance(call, Mapping)
    ]


def _int_sum(calls: Iterable[Mapping[str, Any]], field: str) -> int | None:
    values = [call.get(field) for call in calls]
    numbers = [value for value in values if isinstance(value, int | float)]
    return int(sum(numbers)) if numbers else None


def _timestamp(value: Any) -> datetime | None:
    """An ISO 8601 time as naive UTC (SQL datetime2), or None."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def _models(record: Mapping[str, Any], calls: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    """Which model answered each role: the record's own map, else the calls' models."""
    stated = record.get("models")
    if isinstance(stated, Mapping) and stated:
        return {str(role): str(model) for role, model in stated.items()}
    found: dict[str, str] = {}
    for call in calls:
        role = call.get("role")
        model = call.get("response_model") or call.get("model")
        if role and model:
            found.setdefault(str(role), str(model))
    return found


def _photos(record: Mapping[str, Any]) -> int | None:
    stated = record.get("photos")
    if isinstance(stated, int):
        return stated
    if isinstance(stated, list):
        return len(stated)
    upload = record.get("upload")
    if isinstance(upload, Mapping):
        for field in ("photos", "images", "files"):
            value = upload.get(field)
            if isinstance(value, int):
                return value
            if isinstance(value, list):
                return len(value)
    return None


def case_row(
    record: Mapping[str, Any],
    *,
    source: str,
    run_id: str,
    case_id: str,
    keep_record: bool = True,
) -> dict[str, Any]:
    """One ``dendro.cases`` row. ``keep_record`` stores the whole record as JSON."""
    result = _result(record)
    trace = result.get("trace") if isinstance(result.get("trace"), Mapping) else {}
    response = result.get("response") if isinstance(result.get("response"), Mapping) else {}
    identity = trace.get("runtime_identity") or {}
    config = identity.get("config") or {}
    calls = _calls(trace)
    models = _models(record, calls)
    decisions = response.get("decisions") or ()
    targets = response.get("target_subject_ids") or ()
    exit_code = record.get("exit_code")
    reported = [call.get("reported_cost_usd") for call in calls]
    costs = [value for value in reported if isinstance(value, int | float)]
    cost = sum(costs) if costs else record.get("vibe_cost_usd")
    wall = record.get("wall_seconds")
    if not isinstance(wall, int | float) and isinstance(trace.get("duration_ms"), int | float):
        wall = trace["duration_ms"] / 1000
    started = _timestamp(record.get("started_at")) or _timestamp(trace.get("started_at"))
    return {
        "case_key": case_key(source, run_id, case_id),
        "source": source,
        "run_id": run_id,
        "case_id": case_id,
        "exit_code": exit_code if isinstance(exit_code, int) else None,
        "succeeded": exit_code == 0 and bool(response),
        "code_sha": trace.get("code_commit_sha"),
        "code_dirty": trace.get("code_dirty"),
        "package_version": identity.get("package_version"),
        "config_sha256": identity.get("config_sha256"),
        "knowledge_sha256": identity.get("knowledge_sha256"),
        "review_authority": (config.get("graph") or {}).get("review_authority"),
        "selective_arbitration": (config.get("escalation") or {}).get("selective_arbitration"),
        "models": json.dumps(models, sort_keys=True) if models else None,
        "photos": _photos(record),
        "subjects": len(decisions) if response else None,
        "target_subject_id": str(targets[0]) if targets else None,
        "model_calls": len(calls) if trace else None,
        "input_tokens": _int_sum(calls, "input_tokens"),
        "output_tokens": _int_sum(calls, "output_tokens"),
        "cached_input_tokens": _int_sum(calls, "cached_input_tokens"),
        "reported_cost_usd": round(float(cost), 4) if isinstance(cost, int | float) else None,
        "wall_seconds": float(wall) if isinstance(wall, int | float) else None,
        "started_at": started,
        "record": json.dumps(record, ensure_ascii=False) if keep_record else None,
    }


def decision_rows(record: Mapping[str, Any], key: str) -> list[dict[str, Any]]:
    """One ``dendro.decisions`` row per subject the response decided on."""
    response = _result(record).get("response") or {}
    targets = set(response.get("target_subject_ids") or ())
    rows = []
    for decision in response.get("decisions") or ():
        if not isinstance(decision, Mapping) or not decision.get("subject_id"):
            continue
        hypothesis = decision.get("visual_hypothesis")
        photo = decision.get("best_next_photo")
        rows.append(
            {
                "case_key": key,
                "subject_id": str(decision["subject_id"]),
                "is_target": (decision["subject_id"] in targets) if targets else None,
                "selected_taxon": decision.get("selected_taxon"),
                "resolution": decision.get("resolution"),
                "confidence": decision.get("confidence"),
                "status": decision.get("status"),
                "visual_hypothesis": (
                    hypothesis.get("taxon") if isinstance(hypothesis, Mapping) else None
                ),
                "best_next_photo": photo.get("target") if isinstance(photo, Mapping) else None,
            }
        )
    return rows


def merge_sql(table: str, columns: Sequence[str], keys: Sequence[str]) -> str:
    """A T-SQL MERGE that inserts a row or replaces the one with the same keys.

    ``table``, ``columns`` and ``keys`` come from this module's constants, never from data.
    """
    source = ", ".join(f"? AS {column}" for column in columns)
    match = " AND ".join(f"t.{key} = s.{key}" for key in keys)
    update = ", ".join(f"{column} = s.{column}" for column in columns if column not in keys)
    names = ", ".join(columns)
    values = ", ".join(f"s.{column}" for column in columns)
    return (
        f"MERGE {table} WITH (HOLDLOCK) AS t USING (SELECT {source}) AS s ON {match} "
        f"WHEN MATCHED THEN UPDATE SET {update} "
        f"WHEN NOT MATCHED THEN INSERT ({names}) VALUES ({values});"
    )


def write_case(cursor: Any, row: Mapping[str, Any], decisions: Sequence[Mapping[str, Any]]) -> None:
    """Insert or replace one case and its decisions. The caller commits."""
    cursor.execute(
        merge_sql("dendro.cases", CASE_COLUMNS, ("case_key",)),
        [row[column] for column in CASE_COLUMNS],
    )
    cursor.execute("DELETE FROM dendro.decisions WHERE case_key = ?", [row["case_key"]])
    if decisions:
        placeholders = ", ".join("?" for _ in DECISION_COLUMNS)
        cursor.executemany(
            f"INSERT INTO dendro.decisions ({', '.join(DECISION_COLUMNS)}) VALUES ({placeholders})",
            [[decision[column] for column in DECISION_COLUMNS] for decision in decisions],
        )
