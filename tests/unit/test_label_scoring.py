"""Synthetic references exercise scoring without fitting rules to golden photographs."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from dendro_inspector.cli import app
from dendro_inspector.evaluation import coverage_report
from dendro_inspector.evaluation.coverage_report import build_reports, render_json, render_text
from dendro_inspector.evaluation.label_scoring import (
    Grade,
    Outcome,
    ReferenceLabel,
    Scope,
    SubjectAnswer,
    grade_record,
    grade_subject,
    invalid_image_references,
    load_labels,
    parse_names,
    summarise_grades,
    wilson_interval,
)

LABEL = ReferenceLabel("fagus", "fagaceae")


@pytest.mark.parametrize(
    ("text", "genera", "broader"),
    [
        ("Fagus / Carpinus (beech or hornbeam)", ("fagus", "carpinus"), ()),
        ("Fagus and Carpinus", ("fagus", "carpinus"), ()),
        ("Fagus and/or Carpinus", ("fagus", "carpinus"), ()),
        ("Quercus robur or Q. petraea", ("quercus",), ()),
        ("Betulaceae", (), ("betulaceae",)),
        ("cf. Fagus sylvatica", ("fagus",), ()),
        ("n/a", (), ()),
        (None, (), ()),
    ],
)
def test_names_are_whole_taxa_and_alternatives_stay_ambiguous(text, genera, broader):
    assert parse_names(text) == (genera, broader)


@pytest.mark.parametrize(
    ("name", "outcome"),
    [
        ("Fagus sylvatica", Outcome.CORRECT),
        ("Carpinus betulus", Outcome.WRONG),
        ("Fagus / Carpinus", Outcome.AMBIGUOUS_WITH_LABEL),
        ("Fagus / Betulaceae", Outcome.AMBIGUOUS_WITH_LABEL),
        ("Carpinus / Quercus", Outcome.AMBIGUOUS_WITHOUT_LABEL),
        ("Fagaceae", Outcome.BROADER_ONLY),
        ("Betulaceae", Outcome.WRONG_BROADER),
        ("unknown", Outcome.NO_NAME),
    ],
)
def test_genus_accuracy_does_not_credit_ambiguous_or_family_answers(name, outcome):
    genera, broader = parse_names(name)
    assert grade_subject(SubjectAnswer("target", genera, broader), LABEL) is outcome


def test_background_names_do_not_change_the_target_grade():
    reference = replace(LABEL, target_subject_ids=("target",))
    subjects = (SubjectAnswer("background", ("quercus",)), SubjectAnswer("target", ("fagus",)))
    for ordered in (subjects, subjects[::-1]):
        grade = grade_record(ordered, reference)
        assert grade.outcome is Outcome.CORRECT
        assert grade.scope is Scope.DECLARED_TARGET
        assert grade.graded_subject_ids == ("target",)
        assert grade.context_names == (("background", "quercus"),)


@pytest.mark.parametrize(
    "subjects",
    [
        (),
        (SubjectAnswer("background", ("fagus",)),),
        (SubjectAnswer("target", ("fagus",)), SubjectAnswer("background", ("fagus",))),
    ],
)
def test_missing_declared_targets_never_fall_back_to_background_or_partial_answers(subjects):
    reference = replace(LABEL, target_subject_ids=("target", "other_target"))
    grade = grade_record(subjects, reference)
    assert grade.outcome is Outcome.MISSING_TARGET
    assert "other_target" in grade.missing_target_ids
    assert not grade.graded_subject_ids


def test_undeclared_mixed_subjects_report_possible_outcomes_instead_of_picking_first():
    grade = grade_record(
        (SubjectAnswer("one", ("fagus",)), SubjectAnswer("two", ("quercus",))), LABEL
    )
    assert grade.outcome is Outcome.UNSCOPED
    assert set(grade.possible_outcomes) == {Outcome.CORRECT, Outcome.WRONG}


def test_unanimous_subjects_do_not_require_guessing_a_target():
    grade = grade_record(
        (SubjectAnswer("one", ("fagus",)), SubjectAnswer("two", ("fagus",))), LABEL
    )
    assert grade.outcome is Outcome.CORRECT
    assert grade.scope is Scope.UNDECLARED


def test_all_subjects_in_a_declared_single_lot_cannot_hide_a_wrong_name():
    grade = grade_record(
        (SubjectAnswer("one", ("fagus",)), SubjectAnswer("two", ("quercus",))),
        replace(LABEL, target_subject_ids="all"),
    )
    assert grade.outcome is Outcome.WRONG
    assert grade.scope is Scope.ALL_SUBJECTS


def test_summary_keeps_coverage_accuracy_and_label_visibility_separate():
    grades = [
        ("a", Grade(Outcome.CORRECT, Scope.SINGLE_SUBJECT), replace(LABEL, label_visible=True)),
        ("b", Grade(Outcome.WRONG, Scope.SINGLE_SUBJECT), replace(LABEL, label_visible=False)),
        ("c", Grade(Outcome.WRONG_BROADER, Scope.SINGLE_SUBJECT), LABEL),
        ("d", Grade(Outcome.AMBIGUOUS_WITH_LABEL, Scope.SINGLE_SUBJECT), LABEL),
    ]
    summary = summarise_grades(grades)
    overall = summary["all"]
    assert overall["records"] == overall["distinct_cases"] == 4
    assert overall["single_genus_names"] == 2
    assert overall["coverage"] == overall["conditional_accuracy"] == 0.5
    assert overall["wrong_genus_rate"] == 0.5
    assert overall["wrong_genus_rate_interval_95"] == [0.095, 0.905]
    assert summary["label_not_visible"]["records"] == 1
    assert summary["label_not_visible"]["wrong_genus_rate"] == 1
    assert summary["label_visibility_unknown"] == 2


def test_repeated_runs_do_not_tighten_the_confidence_interval():
    grade = Grade(Outcome.CORRECT, Scope.SINGLE_SUBJECT)
    summary = summarise_grades([("same-photo", grade, LABEL)] * 4)["all"]
    assert summary["records"] == 4
    assert summary["distinct_cases"] == 1
    assert summary["wrong_genus_rate_interval_95"] is None
    assert summary["interval_unavailable_reason"] == "repeated_case_ids"


def test_empty_and_unnamed_samples_do_not_report_perfect_accuracy():
    for pairs in ([], [("one", Grade(Outcome.NO_NAME, Scope.SINGLE_SUBJECT), LABEL)]):
        summary = summarise_grades(pairs)["all"]
        assert summary["conditional_accuracy"] is None
        assert summary["wrong_genus_rate_interval_95"] is None
    assert wilson_interval(0, 40) == pytest.approx((0.0, 0.08762453925))


def test_image_reference_check_reports_only_unsupplied_ids_once():
    assert invalid_image_references(["img-1", "img-9", "img-9"], ["img-1"]) == ("img-9",)


def test_flat_and_structured_reference_labels_preserve_scope_and_unknown_visibility(tmp_path):
    path = tmp_path / "labels.json"
    path.write_text(
        json.dumps(
            {
                "labels": {
                    "flat": " Fagus ",
                    "scoped": {
                        "genus": "Fagus",
                        "family": "Fagaceae",
                        "target_subject_ids": ["tree"],
                        "label_visible": False,
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    labels = load_labels(path)
    assert labels["flat"] == ReferenceLabel("fagus")
    assert labels["scoped"] == ReferenceLabel("fagus", "fagaceae", ("tree",), False)


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"labels": []},
        {"a": {}},
        {"a": None},
        {"a": {"genus": "Fagus sylvatica"}},
        {"a": {"genus": "fagus", "target_subject_ids": "tree"}},
        {"a": {"genus": "fagus", "target_subject_ids": [1]}},
        {"a": {"genus": "fagus", "label_visible": "false"}},
    ],
)
def test_malformed_reference_metadata_fails_before_scoring(tmp_path, payload):
    path = tmp_path / "labels.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        load_labels(path)


def _archive(tmp_path, name, decisions):
    path = tmp_path / f"{name}.json"
    path.write_text(
        json.dumps(
            {
                "case_id": name,
                "result": {
                    "trace": {"evidence_observations": []},
                    "response": {"decisions": decisions},
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def _decision(subject, taxon, resolution="genus"):
    return {"subject_id": subject, "selected_taxon": taxon, "resolution": resolution}


def test_coverage_reports_grade_every_record_with_targets_and_inferred_families(
    tmp_path, knowledge, config, repo_root
):
    paths = [
        _archive(
            tmp_path, "targeted", [_decision("background", "quercus"), _decision("target", "fagus")]
        ),
        _archive(tmp_path, "ambiguous", [_decision("target", "Fagus / Carpinus")]),
        _archive(tmp_path, "family", [_decision("target", "fagaceae", "family")]),
        _archive(tmp_path, "wrong-family", [_decision("target", "betulaceae", "family")]),
        _archive(tmp_path, "unknown", [_decision("target", "unknown", "unknown")]),
    ]
    labels = {
        path.stem: replace(LABEL, family=None, target_subject_ids=("target",)) for path in paths
    }
    reports = asyncio.run(
        build_reports(paths, knowledge, config=config, root=repo_root, labels=labels)
    )
    payload = json.loads(render_json(reports))
    assert [r["stored_grade"]["outcome"] for r in payload["records"]] == [
        "correct",
        "ambiguous_with_label",
        "broader_only",
        "wrong_broader",
        "no_name",
    ]
    for report in reports:
        assert report.reference is not None
        assert report.reference.family == "fagaceae"
    assert "context: background -> quercus" in render_text(reports)
    summary = payload["summary"]["labelled_stored"]["grading"]["all"]
    assert summary["single_genus_names"] == 1
    assert summary["coverage"] == 0.2
    assert summary["wrong_genus_rate"] == 0
    assert payload["summary"]["labelled_stored"]["records"] == {"correct": 1, "abstained": 4}


def test_cli_rejects_bad_labels_with_a_parameter_error(tmp_path):
    labels = tmp_path / "labels.json"
    labels.write_text('{"a": {"genus": "fagus", "label_visible": "false"}}', encoding="utf-8")
    result = CliRunner().invoke(app, ["coverage-audit", str(tmp_path), "--labels", str(labels)])
    assert result.exit_code == 2
    assert "label_visible" in result.output


def test_completed_replay_without_decisions_still_counts_in_the_denominator(
    tmp_path, monkeypatch, knowledge, config, repo_root
):
    async def empty_replay(*args, **kwargs):
        return SimpleNamespace(decisions=(), routing_assessment=None)

    monkeypatch.setattr(coverage_report, "replay", empty_replay)
    path = _archive(tmp_path, "empty", [])
    reports = asyncio.run(
        build_reports(
            [path],
            knowledge,
            config=config,
            root=repo_root,
            labels={"empty": LABEL},
            with_replay=True,
        )
    )
    payload = json.loads(render_json(reports))
    assert payload["records"][0]["replayed_grade"]["outcome"] == "no_name"
    assert payload["summary"]["labelled_replayed"]["grading"]["all"]["records"] == 1
