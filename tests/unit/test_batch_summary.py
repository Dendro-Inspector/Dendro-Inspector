from __future__ import annotations

import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "batch_summary", Path(__file__).parents[2] / "scripts/batch/summarize_run.py"
)
assert spec is not None and spec.loader is not None
summary = importlib.util.module_from_spec(spec)
spec.loader.exec_module(summary)


def record(calls, *, exit_code=0, seconds=1):
    return {
        "exit_code": exit_code,
        "wall_seconds": seconds,
        "result": {
            "response": {"decisions": []},
            "trace": {"events": [{"provider_calls": calls}]},
        },
    }


def test_missing_counters_stay_unknown_and_measured_zero_is_preserved():
    missing = summary.case_row(record([{"input_tokens": 20}]))
    zero = summary.case_row(record([{"input_tokens": 5, "cached_input_tokens": 0}]))
    assert missing["cached_input_tokens"] is None
    assert zero["cached_input_tokens"] == 0
    assert summary.summarize([missing])["tokens"]["cached_input_tokens"] is None
    combined = summary.summarize([missing, zero])
    assert combined["tokens"]["input_tokens"] == 25
    assert combined["tokens"]["cached_input_tokens"] == 0
    assert combined["token_measured_cases"]["cached_input_tokens"] == 1


def test_failed_case_usage_is_included_without_counting_it_as_success():
    rows = [
        summary.case_row(record([{"input_tokens": 10}], seconds=2)),
        summary.case_row(record([{"input_tokens": 30}], exit_code=1, seconds=100)),
    ]
    result = summary.summarize(rows)
    assert result["tokens"]["input_tokens"] == 40
    assert result["succeeded"] == 1
    assert result["failed"] == 1
    assert result["wall_seconds"]["median"] == 2


def test_even_batch_duration_uses_arithmetic_median():
    rows = [summary.case_row(record([], seconds=n)) for n in (1, 3, 7, 20)]
    assert summary.summarize(rows)["wall_seconds"]["median"] == 5
    assert summary.summarize(rows)["tokens"]["input_tokens"] is None
    assert summary.summarize([])["wall_seconds"]["median"] is None


def mixed_record():
    item = record([{"input_tokens": 12}], seconds=4)
    item.update(case_id="sample", run_id="upload-sample", upload={"photos": ["sample.jpg"]})
    item["result"]["response"]["decisions"] = [
        {
            "subject_id": "pile",
            "status": "insufficient_evidence",
            "resolution": "unknown",
            "confidence": "low",
            "selected_taxon": None,
        },
        {
            "subject_id": "piece",
            "status": "probable",
            "resolution": "genus",
            "confidence": "low",
            "selected_taxon": "pinus",
        },
    ]
    return item


def test_subject_summary_counts_all_subjects_without_multiplying_usage():
    item = mixed_record()
    row = summary.case_row(item)
    failed = summary.case_row({**item, "exit_code": 1})
    result = summary.summarize([row, failed])
    assert result["subject_summary"] == {
        "subjects": 2,
        "status": {"insufficient_evidence": 1, "probable": 1},
        "resolution": {"unknown": 1, "genus": 1},
        "confidence": {"low": 2},
        "top_taxa": {"": 1, "pinus": 1},
    }
    assert result["status"] == {"insufficient_evidence": 1}
    assert result["cases"] == 2
    assert result["tokens"]["input_tokens"] == 24
    assert row["subject_decisions"] == item["result"]["response"]["decisions"]


def test_projected_api_record_retains_decisions_but_not_invented_usage():
    item = mixed_record()
    item["response"] = item.pop("result")["response"]
    row = summary.case_row(item)
    assert row["subjects"] == 2
    assert row["status"] == "insufficient_evidence"
    assert row["input_tokens"] is None
    assert row["model_calls"] is None
    assert summary.summarize([row])["subject_summary"]["subjects"] == 2


def test_upload_summary_writes_csv_and_null_dataset(tmp_path):
    import csv
    import json

    (tmp_path / "cases").mkdir()
    item = mixed_record()
    (tmp_path / "cases" / "sample.json").write_text(json.dumps(item), encoding="utf-8")
    result = summary.main(tmp_path)
    assert result["dataset"] is None
    assert result["run_id"] == "upload-sample"
    with (tmp_path / "cases.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert json.loads(rows[0]["subject_decisions"]) == item["result"]["response"]["decisions"]
    assert json.loads((tmp_path / "summary.json").read_text())["subject_summary"]["subjects"] == 2


def test_dataset_summary_preserves_metadata(tmp_path):
    import json

    (tmp_path / "cases").mkdir()
    item = mixed_record()
    item["dataset"] = {"repo": "example/trees", "revision": "abc", "index": 3}
    (tmp_path / "cases" / "sample.json").write_text(json.dumps(item), encoding="utf-8")
    assert summary.main(tmp_path)["dataset"] == {"repo": "example/trees", "revision": "abc"}


def test_empty_summary_has_no_subjects():
    assert summary.summarize([])["subject_summary"]["subjects"] == 0
