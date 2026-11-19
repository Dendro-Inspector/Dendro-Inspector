"""Rows for the results database come out the same whichever writer stored the case."""

from __future__ import annotations

import importlib.util
import json
import re
from datetime import datetime
from pathlib import Path

import pytest

BATCH = Path(__file__).parents[2] / "scripts/batch"
spec = importlib.util.spec_from_file_location("results_rows", BATCH / "results_rows.py")
assert spec is not None and spec.loader is not None
rows = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rows)


def _trace(**extra):
    return {
        "code_commit_sha": "3c10767" + "0" * 33,
        "code_dirty": False,
        "started_at": "2026-11-19T19:31:54Z",
        "duration_ms": 4000,
        "runtime_identity": {
            "package_version": "1.0.0a1",
            "config_sha256": "c" * 64,
            "knowledge_sha256": "k" * 64,
            "config": {
                "graph": {"review_authority": "evidence_bound"},
                "escalation": {"selective_arbitration": True},
            },
        },
        "events": [
            {
                "provider_calls": [
                    {
                        "role": "primary",
                        "model": "vibe-main",
                        "response_model": "mistral-large-4-0",
                        "input_tokens": 100,
                        "output_tokens": 10,
                        "cached_input_tokens": 0,
                        "reported_cost_usd": 0.01,
                    },
                    {
                        "role": "arbiter",
                        "model": "vibe-main",
                        "input_tokens": 50,
                        "output_tokens": 5,
                        "reported_cost_usd": 0.02,
                    },
                ]
            },
            {"provider_calls": []},
        ],
        **extra,
    }


def _response():
    return {
        "target_subject_ids": ["tree"],
        "decisions": [
            {
                "subject_id": "tree",
                "selected_taxon": "acer",
                "resolution": "genus",
                "confidence": "medium",
                "status": "probable",
                "visual_hypothesis": {"taxon": "acer"},
                "best_next_photo": {"target": "leaf_upper_macro"},
            },
            {
                "subject_id": "background",
                "selected_taxon": None,
                "resolution": "unknown",
                "confidence": "low",
                "status": "abstained",
                "best_next_photo": None,
            },
        ],
    }


def cluster_record():
    return {
        "run_id": "t20261119-193145",
        "case_id": "tree",
        "models": {"primary": "openai:gpt-5-mini", "arbiter": "openai:gpt-5-mini"},
        "upload": {"photos": [{}, {}, {}], "note": "", "field_context": False},
        "started_at": "2026-11-19T19:31:54+00:00",
        "wall_seconds": 125.2,
        "exit_code": 0,
        "result": {"response": _response(), "trace": _trace()},
    }


def local_record():
    return {
        "unit": "ACER",
        "photos": 3,
        "exit_code": 0,
        "vibe_cost_usd": 0.3434,
        "result": {"response": _response(), "trace": _trace()},
    }


def test_a_cluster_case_is_keyed_by_its_run_and_case():
    row = rows.case_row(
        cluster_record(), source="cluster", run_id="t20261119-193145", case_id="tree"
    )
    assert row["case_key"] == "cluster:t20261119-193145/tree"
    assert set(row) == set(rows.CASE_COLUMNS)
    assert row["succeeded"] is True
    assert json.loads(row["models"])["primary"] == "openai:gpt-5-mini"
    assert row["photos"] == 3
    assert row["started_at"] == datetime(2026, 11, 19, 19, 31, 54)
    assert row["wall_seconds"] == 125.2
    assert (row["input_tokens"], row["output_tokens"], row["model_calls"]) == (150, 15, 2)
    assert row["cached_input_tokens"] == 0
    assert row["review_authority"] == "evidence_bound"
    assert row["selective_arbitration"] is True
    assert row["target_subject_id"] == "tree"
    assert json.loads(row["record"])["run_id"] == "t20261119-193145"


def test_a_local_case_takes_models_from_its_calls_and_time_from_its_trace():
    row = rows.case_row(
        local_record(),
        source="local",
        run_id="vibe-foundry/vibe",
        case_id="ACER",
        keep_record=False,
    )
    assert row["case_key"] == "local:vibe-foundry/vibe/ACER"
    assert json.loads(row["models"]) == {"arbiter": "vibe-main", "primary": "mistral-large-4-0"}
    assert row["photos"] == 3
    assert row["wall_seconds"] == 4.0
    assert row["reported_cost_usd"] == 0.03  # the calls' own costs win over the script's
    assert row["record"] is None


def test_a_failed_case_without_a_response_is_still_a_row():
    record = {"exit_code": 1, "failure": {"trace": {"events": []}}}
    row = rows.case_row(record, source="local", run_id="r", case_id="u")
    assert row["succeeded"] is False
    assert row["subjects"] is None and row["input_tokens"] is None
    assert rows.decision_rows(record, row["case_key"]) == []


def test_decisions_mark_the_target_and_flatten_nested_fields():
    found = rows.decision_rows(cluster_record(), "k")
    assert [d["subject_id"] for d in found] == ["tree", "background"]
    assert found[0]["is_target"] is True and found[1]["is_target"] is False
    assert found[0]["visual_hypothesis"] == "acer"
    assert found[0]["best_next_photo"] == "leaf_upper_macro"
    assert found[1]["best_next_photo"] is None
    assert all(set(d) == set(rows.DECISION_COLUMNS) for d in found)


def test_no_designated_target_leaves_is_target_unknown():
    record = cluster_record()
    record["result"]["response"]["target_subject_ids"] = []
    assert {d["is_target"] for d in rows.decision_rows(record, "k")} == {None}


@pytest.mark.parametrize("source", ["", "remote", "Cluster"])
def test_only_known_sources_make_keys(source):
    with pytest.raises(ValueError, match="source"):
        rows.case_key(source, "r", "c")


def test_a_key_too_long_for_the_schema_is_refused():
    with pytest.raises(ValueError, match="longer"):
        rows.case_key("local", "r" * 300, "c")


def test_schema_and_rows_agree_on_columns():
    schema = (BATCH / "results-schema.sql").read_text(encoding="utf-8")

    def columns(table):
        body = re.search(rf"CREATE TABLE dendro\.{table} \((.*?)\n\);", schema, re.S)
        assert body, table
        names = re.findall(r"^\s{4}([a-z_0-9]+)\s", body.group(1), re.M)
        return [name for name in names if name != "archived_at"]

    assert columns("cases") == list(rows.CASE_COLUMNS)
    assert columns("decisions") == list(rows.DECISION_COLUMNS)


class _Cursor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, list[object]]] = []

    def execute(self, sql, params=()):
        self.calls.append(("execute", sql, list(params)))

    def executemany(self, sql, params):
        self.calls.append(("executemany", sql, [list(p) for p in params]))


def test_writing_a_case_replaces_its_decisions():
    cursor = _Cursor()
    record = cluster_record()
    row = rows.case_row(record, source="cluster", run_id="t", case_id="tree")
    rows.write_case(cursor, row, rows.decision_rows(record, row["case_key"]))
    merge, delete, insert = cursor.calls
    assert merge[1].startswith("MERGE dendro.cases WITH (HOLDLOCK)")
    assert merge[1].count("?") == len(rows.CASE_COLUMNS) == len(merge[2])
    assert delete[1] == "DELETE FROM dendro.decisions WHERE case_key = ?"
    assert insert[0] == "executemany" and len(insert[2]) == 2
    assert insert[1].count("?") == len(rows.DECISION_COLUMNS)


def test_a_case_without_decisions_inserts_none():
    cursor = _Cursor()
    record = {"exit_code": 1}
    row = rows.case_row(record, source="local", run_id="r", case_id="u")
    rows.write_case(cursor, row, [])
    assert [call[0] for call in cursor.calls] == ["execute", "execute"]
