"""Load the case records kept under runs/ into the results database, with labels and grades.

The control function archives cluster cases on its own (every six hours, or on
``POST /api/archive``). This script adds what only a workstation has: records of local runs,
the labels, which case ran which unit in which comparison, and the engine's grade of each
labelled case. A cluster record found here gets the same key the function gives it, so
loading it twice replaces one row.

Layout read: ``<runs>/<folder>/.../cases/<unit>.json``. The collection is the path from
``<runs>`` to the ``cases`` folder (``vibe-foundry-pr62-20261119/foundry``), the unit is the
file name, and the arm is the collection's last folder. A unit is graded against the labels
file in its run folder (``labels*.json``), else against ``--default-labels`` when that file
labels it, else not at all.

Signs in with Microsoft Entra (``Authentication=ActiveDirectoryDefault``: ``az login`` is
enough). Needs the ``results-sql`` extra: ``pip install -e ".[results-sql]"``.

Usage:
  sync_results_sql.py --server S --database D [--runs runs] [--only FOLDER] [--refresh] [--regrade]
  sync_results_sql.py --dry-run [--runs runs]           counts only, no connection
  sync_results_sql.py --server S --database D --apply-schema [--grant-function NAME]
  sync_results_sql.py --server S --database D --report  outcomes per collection
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
import results_rows  # noqa: E402

DEFAULT_LABELS = "labeled-full-7aed1a0-20261116/labels-all.json"
SCHEMA = HERE / "results-schema.sql"
#: A managed identity's name, as CREATE USER ... FROM EXTERNAL PROVIDER takes it.
IDENTITY_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
LABEL_COLUMNS = ("label_set", "unit_id", "genus", "family", "target_subject_ids", "label_visible")
UNIT_COLUMNS = ("collection", "unit_id", "case_key", "arm", "label_set")
GRADE_COLUMNS = ("case_key", "label_set", "unit_id", "outcome", "scope", "scorer")


@dataclass(frozen=True, slots=True)
class Found:
    path: Path
    collection: str
    unit: str
    source: str
    run_id: str
    case_id: str

    @property
    def key(self) -> str:
        return results_rows.case_key(self.source, self.run_id, self.case_id)


def find_records(runs: Path) -> Iterator[tuple[Found, dict[str, Any]]]:
    """Every case record under ``runs``, with where it belongs."""
    for path in sorted(runs.glob("**/cases/*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError):
            print(f"skipped, not JSON: {path}", file=sys.stderr)
            continue
        if not isinstance(record, dict) or not ({"result", "failure", "exit_code"} & set(record)):
            continue
        collection = path.parent.parent.relative_to(runs).as_posix()
        if record.get("run_id") and record.get("case_id"):
            source, run_id, case_id = "cluster", str(record["run_id"]), str(record["case_id"])
        else:
            source, run_id, case_id = "local", collection, path.stem
        yield Found(path, collection, path.stem, source, run_id, case_id), record


def label_sets(runs: Path) -> dict[str, dict[str, Any]]:
    """Every labels file under the run folders, by ``<folder>/<file>``."""
    from dendro_inspector.evaluation.label_scoring import load_labels

    found = {}
    for path in sorted(runs.glob("*/labels*.json")):
        found[path.relative_to(runs).as_posix()] = load_labels(path)
    return found


def label_set_for(found: Found, sets: dict[str, dict[str, Any]], default: str) -> str | None:
    folder = found.collection.split("/", 1)[0]
    own = sorted(name for name in sets if name.startswith(f"{folder}/"))
    for name in (*own, default):
        if found.unit in sets.get(name, {}):
            return name
    return None


def grade(found: Found, label: Any, knowledge: Any, config: Any) -> tuple[str, str] | None:
    """The engine's stored-answer grade of one record, as (outcome, scope).

    A graph record goes through the coverage audit; an arm M record (the model alone, no
    graph trace) is graded from its answer by the same scorer.
    """
    from dendro_inspector.evaluation import model_alone
    from dendro_inspector.evaluation.coverage_report import build_reports
    from dendro_inspector.evaluation.trace_replay import load_record

    stored = load_record(found.path)
    if stored is None:
        record = json.loads(found.path.read_text(encoding="utf-8"))
        if not ((record.get("result") or {}).get("model_alone") or {}).get("answer"):
            return None
        graded = model_alone.grade(record, label, knowledge)
        return graded.outcome.value, graded.scope.value
    reports = asyncio.run(
        build_reports(
            [found.path], knowledge, config=config, root=ROOT, labels={stored.case_id: label}
        )
    )
    result = reports[0].stored_grade if reports else None
    return (result.outcome.value, result.scope.value) if result else None


def connect(server: str, database: str) -> Any:
    import mssql_python

    # The login timeout goes to connect(timeout=): mssql-python refuses it in the string.
    return mssql_python.connect(
        f"Server={server};Database={database};Authentication=ActiveDirectoryDefault;"
        "Encrypt=yes;TrustServerCertificate=no",
        timeout=90,
    )


def apply_schema(connection: Any, grant: str | None) -> None:
    cursor = connection.cursor()
    batches = re.split(r"^\s*GO\s*$", SCHEMA.read_text(encoding="utf-8"), flags=re.M)
    for batch in batches:
        if batch.strip():
            cursor.execute(batch)
    if grant:
        if not IDENTITY_NAME.match(grant):
            raise SystemExit(f"not an identity name: {grant!r}")
        cursor.execute(
            f"IF USER_ID(N'{grant}') IS NULL CREATE USER [{grant}] FROM EXTERNAL PROVIDER; "
            f"ALTER ROLE db_datareader ADD MEMBER [{grant}]; "
            f"ALTER ROLE db_datawriter ADD MEMBER [{grant}];"
        )
    connection.commit()
    print(
        f"schema applied ({len([b for b in batches if b.strip()])} batches)"
        + (f"; {grant} may read and write" if grant else "")
    )


def report(connection: Any) -> None:
    cursor = connection.cursor()
    cursor.execute(
        "SELECT collection, units, correct, wrong, broader_only, no_name, other, ungraded, "
        "wrong_rate_upper_95, tokens, reported_cost_usd FROM dendro.collection_summary "
        "ORDER BY first_started, collection"
    )
    print(
        f"{'collection':<52} {'units':>5} {'corr':>5} {'wrong':>5} {'broad':>5} "
        f"{'none':>5} {'other':>5} {'ungr':>5} {'wrong<':>7} {'tokens':>11} {'$':>8}"
    )
    for row in cursor.fetchall():
        upper = f"{row[8]:.0%}" if row[8] is not None else "-"
        cost = f"{row[10]:.2f}" if row[10] is not None else "-"
        print(
            f"{row[0][:52]:<52} {row[1]:>5} {row[2]:>5} {row[3]:>5} {row[4]:>5} {row[5]:>5} "
            f"{row[6]:>5} {row[7]:>5} {upper:>7} {row[9]:>11,} {cost:>8}"
        )


def sync(args: argparse.Namespace) -> None:
    from dendro_inspector import __version__
    from dendro_inspector.config import load_config
    from dendro_inspector.knowledge.loader import KnowledgeBase

    runs = args.runs.resolve()
    sets = label_sets(runs)
    if args.default_labels not in sets:
        raise SystemExit(f"default labels {args.default_labels} not found under {runs}")
    found = [
        item
        for item in find_records(runs)
        if not args.only or any(item[0].collection.startswith(prefix) for prefix in args.only)
    ]
    config = load_config()
    knowledge = KnowledgeBase(config.knowledge, root=ROOT)

    connection = None if args.dry_run else connect(args.server, args.database)
    cursor = connection.cursor() if connection else None
    existing: set[str] = set()
    graded: set[tuple[str, str]] = set()
    if cursor:
        cursor.execute("SELECT case_key FROM dendro.cases")
        existing = {row[0] for row in cursor.fetchall()}
        cursor.execute("SELECT case_key, label_set FROM dendro.grades")
        graded = {(row[0], row[1]) for row in cursor.fetchall()}
        for name, labels in sets.items():
            for unit, label in labels.items():
                targets = label.target_subject_ids
                cursor.execute(
                    results_rows.merge_sql("dendro.labels", LABEL_COLUMNS, LABEL_COLUMNS[:2]),
                    [
                        name,
                        unit,
                        label.genus,
                        label.family,
                        json.dumps(targets if targets == "all" else list(targets)),
                        label.label_visible,
                    ],
                )
        connection.commit()

    counts: Counter[str] = Counter()
    for item, record in found:
        key = item.key
        label_set = label_set_for(item, sets, args.default_labels)
        if key not in existing or args.refresh:
            row = results_rows.case_row(
                record, source=item.source, run_id=item.run_id, case_id=item.case_id
            )
            if cursor:
                results_rows.write_case(cursor, row, results_rows.decision_rows(record, key))
            existing.add(key)
            counts["cases written"] += 1
        if cursor:
            cursor.execute(
                results_rows.merge_sql("dendro.units", UNIT_COLUMNS, UNIT_COLUMNS[:2]),
                [item.collection, item.unit, key, item.collection.rsplit("/", 1)[-1], label_set],
            )
        counts["units"] += 1
        if label_set and ((key, label_set) not in graded or args.regrade):
            result = grade(item, sets[label_set][item.unit], knowledge, config)
            if result and cursor:
                cursor.execute(
                    results_rows.merge_sql("dendro.grades", GRADE_COLUMNS, GRADE_COLUMNS[:2]),
                    [key, label_set, item.unit, result[0], result[1], __version__],
                )
            counts["graded" if result else "not gradable"] += 1
        if connection and counts["units"] % 50 == 0:
            connection.commit()
    if connection:
        connection.commit()
    collections = len({item.collection for item, _ in found})
    print(
        f"{len(found)} records in {collections} collections; "
        + ", ".join(f"{name} {count}" for name, count in sorted(counts.items()))
        + ("" if connection else " (dry run: nothing written)")
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--server", help="e.g. sql-education-dev-sdc-001.database.windows.net")
    parser.add_argument("--database", default="sqldb-dendro-results")
    parser.add_argument("--runs", type=Path, default=ROOT / "runs")
    parser.add_argument("--default-labels", default=DEFAULT_LABELS)
    parser.add_argument("--refresh", action="store_true", help="rewrite cases already stored")
    parser.add_argument("--regrade", action="store_true", help="grade again cases already graded")
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="COLLECTION",
        help="only collections starting with this (repeatable), e.g. a new run folder",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--apply-schema", action="store_true")
    parser.add_argument("--grant-function", help="managed identity to give read/write access")
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args()
    if not args.dry_run and not args.server:
        parser.error("--server is required unless --dry-run")
    if args.apply_schema or args.report:
        connection = connect(args.server, args.database)
        if args.apply_schema:
            apply_schema(connection, args.grant_function)
        if args.report:
            report(connection)
        connection.close()
        return 0
    sync(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
