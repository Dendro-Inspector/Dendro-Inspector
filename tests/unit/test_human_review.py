"""Human review: the package is the evidence unchanged, the answers are untrusted, and a
correction stays with its case.

Answers and photographs here are synthetic: a stored answer is a dict shaped like
`dendro inspect --json` output, and a photograph is a few bytes whose hash is all that matters.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from dendro_inspector import human_review as hr
from dendro_inspector.cli import app

KNOWLEDGE = Path(__file__).parents[2] / "knowledge"


def _decision(subject_id: str, taxon: str | None, **extra: Any) -> dict[str, Any]:
    return {
        "subject_id": subject_id,
        "selected_taxon": taxon,
        "selected_taxon_display_name": taxon.capitalize() if taxon else None,
        "resolution": "genus" if taxon else "unknown",
        "status": "probable" if taxon else "insufficient_evidence",
        "confidence_band": "70–84/100" if taxon else "<50/100",
        "supporting_evidence": ["bark.texture = deeply_furrowed"] if taxon else [],
        **extra,
    }


def _stored(
    decisions: list[dict[str, Any]],
    *,
    targets: list[str] | None = None,
    hypothesis: dict[str, Any] | None = None,
    cited: tuple[str, ...] = ("img-1",),
) -> dict[str, Any]:
    roles = {
        d["subject_id"]: ("target" if targets and d["subject_id"] in targets else "unspecified")
        for d in decisions
    }
    results = [
        {
            "subject": d["subject_id"],
            "subject_role": roles[d["subject_id"]],
            "verdict": f"Verdict for {d['subject_id']}.",
            "limitations": ["Only bark is visible."],
        }
        for d in decisions
    ]
    if hypothesis is not None:
        results[-1]["visual_hypothesis"] = hypothesis
    return {
        "response": {
            "schema_version": 2,
            "case_id": "oak-garden",
            "results": results,
            "decisions": decisions,
            "target_subject_ids": targets or [],
            "human_readable": "Assessment. The bark is furrowed.",
        },
        "trace": {
            "case_id": "oak-garden",
            "graph_version": "1.0",
            "trace_schema_version": 7,
            "code_commit_sha": "abc1234",
            "domain_prompt": {"version": "2026-11-01", "sha256": "f" * 64},
            "providers": {"primary": "fake"},
            "evidence_observations": [{"observation_id": "obs_1", "image_id": i} for i in cited],
        },
    }


TWO_SUBJECTS = _stored(
    [
        _decision(
            "tree_1",
            "quercus",
            nearest_alternative="fagus",
            strongest_contradiction="bark.texture = smooth_grey on the lower trunk",
        ),
        _decision("tree_2", None),
    ],
    hypothesis={
        "taxon": "betula",
        "display_name": "Betula (birch)",
        "resolution": "genus",
        "status": "evidence_supported",
        "held_back_by": "unverifiable_review",
        "explanation": "White peeling bark was read but could not be checked.",
    },
)


def _write(tmp_path: Path, stored: dict[str, Any], photos: int = 1) -> tuple[Path, list[Path]]:
    result = tmp_path / "result.json"
    result.write_text(json.dumps(stored), encoding="utf-8")
    images = []
    for number in range(1, photos + 1):
        image = tmp_path / f"original-{number}.jpg"
        image.write_bytes(f"photo {number}".encode())
        images.append(image)
    return result, images


def _export(
    tmp_path: Path, stored: dict[str, Any], photos: int = 1
) -> tuple[Path, hr.ReviewPackage]:
    result, images = _write(tmp_path, stored, photos)
    package, payloads = hr.build_package(result, images, allow_location=True)
    folder = tmp_path / "package"
    hr.write_package(package, payloads, folder)
    return folder, package


def _response(package: hr.ReviewPackage, answers: dict[str, dict[str, Any]], **reviewer: str):
    return {
        "package_id": package.package_id,
        "reviewer": {"reviewer": "F.R.", "qualification": "forester", "date": "2026-11-20"}
        | reviewer,
        "answers": [{"question_id": qid, **answer} for qid, answer in answers.items()],
    }


def _question(package: hr.ReviewPackage, kind: hr.QuestionKind, subject: str | None) -> str:
    return next(
        q.question_id for q in package.questions if q.kind is kind and q.subject_id == subject
    )


def _answers_file(tmp_path: Path, payload: dict[str, Any], name: str = "answers.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# ---------------------------------------------------------------- the package


def test_the_package_holds_the_photographs_byte_for_byte(tmp_path):
    folder, package = _export(tmp_path, TWO_SUBJECTS, photos=2)

    for number, photo in enumerate(package.photos, start=1):
        original = (tmp_path / f"original-{number}.jpg").read_bytes()
        copied = (folder / hr.PHOTO_DIR / photo.file_name).read_bytes()
        assert copied == original
        assert photo.sha256 == hashlib.sha256(original).hexdigest()
        assert photo.image_id == f"img-{number}"
    assert hr.load_package(folder) == package
    assert package.package_id == hr.package_id_of(package)


def test_the_package_records_which_engine_and_answer_it_came_from(tmp_path):
    result, _ = _write(tmp_path, TWO_SUBJECTS)
    _, package = _export(tmp_path, TWO_SUBJECTS)

    source = package.source
    assert source.result_sha256 == hashlib.sha256(result.read_bytes()).hexdigest()
    assert (source.code_commit_sha, source.domain_prompt_version) == ("abc1234", "2026-11-01")
    assert source.response_schema_version == 2
    assert source.trace_present


def test_questions_are_derived_from_the_answer(tmp_path):
    _, package = _export(tmp_path, TWO_SUBJECTS)

    asked = [(q.kind, q.subject_id) for q in package.questions]
    assert asked == [
        (hr.QuestionKind.DESIGNATE_TARGET, None),
        (hr.QuestionKind.CONFIRM_IDENTITY, "tree_1"),
        (hr.QuestionKind.CHECK_CONTRADICTION, "tree_1"),
        (hr.QuestionKind.IDENTIFY, "tree_2"),
        (hr.QuestionKind.CHECK_HYPOTHESIS, "tree_2"),
    ]
    confirm = package.questions[1]
    assert confirm.asked_taxon == "quercus"
    assert "fagus" in confirm.text
    assert package.questions[4].asked_taxon == "betula"
    assert package.questions[0].choices == ("tree_1", "tree_2")
    for question in package.questions:
        assert question.allowed_answers == hr.ALLOWED_ANSWERS[question.kind]


def test_a_designated_target_is_not_asked_about(tmp_path):
    stored = _stored(
        [_decision("tree_1", "quercus"), _decision("tree_2", "fagus")], targets=["tree_1"]
    )
    _, package = _export(tmp_path, stored)

    assert hr.QuestionKind.DESIGNATE_TARGET not in {q.kind for q in package.questions}
    assert "(the one asked about)" in package.questions[0].text


def test_the_template_names_every_question_and_answers_none(tmp_path):
    folder, package = _export(tmp_path, TWO_SUBJECTS)

    template = json.loads((folder / hr.TEMPLATE_FILE).read_text(encoding="utf-8"))
    assert template["package_id"] == package.package_id
    assert [a["question_id"] for a in template["answers"]] == [
        q.question_id for q in package.questions
    ]
    assert all(a["answer"] is None for a in template["answers"])
    readme = (folder / hr.README_FILE).read_text(encoding="utf-8")
    assert all(q.question_id in readme for q in package.questions)


def test_photographs_must_match_the_answer(tmp_path):
    stored = _stored([_decision("tree_1", "quercus")], cited=("img-1", "img-3"))
    result, images = _write(tmp_path, stored, photos=2)

    with pytest.raises(hr.HumanReviewError, match="cites img-3"):
        hr.build_package(result, images, allow_location=True)


def test_a_case_record_must_list_as_many_photographs(tmp_path):
    record = {"upload": {"photos": ["photo-1.jpg", "photo-2.jpg"]}, "result": TWO_SUBJECTS}
    result, images = _write(tmp_path, record, photos=1)

    with pytest.raises(hr.HumanReviewError, match="names 2 photographs; 1 were given"):
        hr.build_package(result, images, allow_location=True)


def test_a_failed_case_cannot_be_reviewed(tmp_path):
    result, images = _write(tmp_path, {"error": {"type": "X"}, "trace": {}})

    with pytest.raises(hr.HumanReviewError, match="holds no answer"):
        hr.build_package(result, images, allow_location=True)


def test_unknown_location_metadata_is_refused_unless_allowed(tmp_path):
    # A few bytes are not an image: their metadata cannot be read, which is refused too.
    result, images = _write(tmp_path, TWO_SUBJECTS)

    with pytest.raises(hr.HumanReviewError, match="--allow-location"):
        hr.build_package(result, images)
    package, _ = hr.build_package(result, images, allow_location=True)
    assert package.photos[0].location_metadata is hr.LocationMetadata.UNKNOWN
    assert package.location_allowed


def test_gps_coordinates_are_refused_unless_allowed(tmp_path):
    image_module = pytest.importorskip("PIL.Image")
    result, _ = _write(tmp_path, TWO_SUBJECTS)
    plain, located = tmp_path / "plain.jpg", tmp_path / "located.jpg"
    image_module.new("RGB", (8, 8)).save(plain)
    photo = image_module.new("RGB", (8, 8))
    exif = photo.getexif()
    gps = exif.get_ifd(0x8825)
    gps[1] = "N"
    exif[0x8825] = gps
    photo.save(located, exif=exif)

    package, _ = hr.build_package(result, [plain])
    assert package.photos[0].location_metadata is hr.LocationMetadata.ABSENT
    with pytest.raises(hr.HumanReviewError, match=r"located\.jpg \(present\)"):
        hr.build_package(result, [located])


def test_an_existing_folder_is_not_overwritten(tmp_path):
    folder, _ = _export(tmp_path, TWO_SUBJECTS)
    result, images = _write(tmp_path, TWO_SUBJECTS)
    package, payloads = hr.build_package(result, images, allow_location=True)

    with pytest.raises(hr.HumanReviewError, match="not an empty folder"):
        hr.write_package(package, payloads, folder)


# ---------------------------------------------------------------- the revision


def test_answers_become_a_revision_of_this_case(tmp_path):
    folder, package = _export(tmp_path, TWO_SUBJECTS)
    kinds = hr.QuestionKind
    answers: dict[str, dict[str, Any]] = {
        _question(package, kinds.DESIGNATE_TARGET, None): {
            "answer": "subject",
            "subject_id": "tree_2",
        },
        _question(package, kinds.CONFIRM_IDENTITY, "tree_1"): {
            "answer": "name",
            "taxon": "quercus_robur",
            "resolution": "species",
            "photos": ["img-1"],
        },
        _question(package, kinds.CHECK_CONTRADICTION, "tree_1"): {"answer": "disagree"},
        _question(package, kinds.IDENTIFY, "tree_2"): {
            "answer": "name",
            "taxon": "betula",
            "resolution": "genus",
        },
        _question(package, kinds.CHECK_HYPOTHESIS, "tree_2"): {"answer": "agree"},
    }

    revision, history = hr.import_answers(
        folder, _answers_file(tmp_path, _response(package, answers))
    )

    assert revision.revision == 1
    assert history == (revision,)
    assert revision.target_subject_id == "tree_2"
    first, second = revision.subjects
    assert (first.outcome, first.reviewed_taxon, first.reviewed_resolution) == (
        hr.Outcome.REFINED,
        "quercus_robur",
        "species",
    )
    assert first.contradiction_upheld is False
    assert first.hypothesis_outcome is None
    assert (second.outcome, second.reviewed_taxon) == (hr.Outcome.NAMED, "betula")
    assert second.hypothesis_outcome is hr.Outcome.CONFIRMED
    assert hr.read_ledger(folder) == (revision,)


@pytest.mark.parametrize(
    ("answer", "outcome", "taxon"),
    [
        ({"answer": "agree"}, hr.Outcome.CONFIRMED, "quercus"),
        (
            {"answer": "name", "taxon": "quercus", "resolution": "genus"},
            hr.Outcome.CONFIRMED,
            "quercus",
        ),
        (
            {"answer": "name", "taxon": "fagus", "resolution": "genus"},
            hr.Outcome.CORRECTED,
            "fagus",
        ),
        ({"answer": "disagree"}, hr.Outcome.REJECTED, None),
        ({"answer": "cannot_tell"}, hr.Outcome.UNDECIDED, None),
    ],
)
def test_an_identity_answer_is_read_against_the_name_asked_about(tmp_path, answer, outcome, taxon):
    folder, package = _export(tmp_path, TWO_SUBJECTS)
    qid = _question(package, hr.QuestionKind.CONFIRM_IDENTITY, "tree_1")

    revision, _ = hr.import_answers(
        folder, _answers_file(tmp_path, _response(package, {qid: answer}))
    )

    first, second = revision.subjects
    assert (first.outcome, first.reviewed_taxon) == (outcome, taxon)
    assert second.outcome is hr.Outcome.UNANSWERED
    assert revision.target_subject_id is None


def test_each_review_is_kept_and_the_same_answers_import_once(tmp_path):
    folder, package = _export(tmp_path, TWO_SUBJECTS)
    qid = _question(package, hr.QuestionKind.CONFIRM_IDENTITY, "tree_1")
    first = _answers_file(tmp_path, _response(package, {qid: {"answer": "agree"}}), "a.json")
    second = _answers_file(
        tmp_path,
        _response(
            package, {qid: {"answer": "disagree"}}, reviewer="B.T.", qualification="botanist"
        ),
        "b.json",
    )

    hr.import_answers(folder, first)
    with pytest.raises(hr.HumanReviewError, match="already recorded as revision 1"):
        hr.import_answers(folder, first)
    revision, history = hr.import_answers(folder, second)

    assert [r.revision for r in history] == [1, 2]
    assert [r.subjects[0].outcome for r in history] == [hr.Outcome.CONFIRMED, hr.Outcome.REJECTED]
    assert "differ" in hr.render_revision(revision, history)


def test_an_unfilled_template_does_not_import(tmp_path):
    folder, _ = _export(tmp_path, TWO_SUBJECTS)

    with pytest.raises(hr.HumanReviewError, match="not a usable response"):
        hr.import_answers(folder, folder / hr.TEMPLATE_FILE)
    assert not (folder / hr.LEDGER_FILE).exists()


def _bad_responses(package: hr.ReviewPackage) -> dict[str, tuple[dict[str, Any], str]]:
    target = _question(package, hr.QuestionKind.DESIGNATE_TARGET, None)
    confirm = _question(package, hr.QuestionKind.CONFIRM_IDENTITY, "tree_1")
    good = _response(package, {confirm: {"answer": "agree"}})
    return {
        "another package": (good | {"package_id": "rp-0123456789abcdef"}, "belong to package"),
        "no such question": (_response(package, {"q-99": {"answer": "agree"}}), "no such question"),
        "answer not listed": (_response(package, {target: {"answer": "agree"}}), "is not one of"),
        "unlisted subject": (
            _response(package, {target: {"answer": "subject", "subject_id": "tree_9"}}),
            "not a listed subject",
        ),
        "name without taxon": (_response(package, {confirm: {"answer": "name"}}), "usable"),
        "taxon without name": (
            _response(package, {confirm: {"answer": "agree", "taxon": "fagus"}}),
            "usable",
        ),
        "unknown photograph": (
            _response(package, {confirm: {"answer": "agree", "photos": ["img-7"]}}),
            "no photograph img-7",
        ),
        "a path for a photograph": (
            _response(package, {confirm: {"answer": "agree", "photos": ["../../x"]}}),
            "usable",
        ),
        "terminal escape": (
            _response(package, {confirm: {"answer": "agree", "note": "\x1b[2Jok"}}),
            "usable",
        ),
        "an extra field": (good | {"command": "anything"}, "usable"),
        "nothing answered": (
            _response(package, {confirm: {"answer": None}}),
            "nothing is answered",
        ),
        "a question twice": (
            good | {"answers": [*good["answers"], *good["answers"]]},
            "usable",
        ),
    }


@pytest.mark.parametrize(
    "case",
    [
        "another package",
        "no such question",
        "answer not listed",
        "unlisted subject",
        "name without taxon",
        "taxon without name",
        "unknown photograph",
        "a path for a photograph",
        "terminal escape",
        "an extra field",
        "nothing answered",
        "a question twice",
    ],
)
def test_untrusted_answers_are_refused(tmp_path, case):
    folder, package = _export(tmp_path, TWO_SUBJECTS)
    payload, message = _bad_responses(package)[case]

    with pytest.raises(hr.HumanReviewError, match=message):
        hr.import_answers(folder, _answers_file(tmp_path, payload))
    assert not (folder / hr.LEDGER_FILE).exists()


def test_an_impossible_date_is_refused(tmp_path):
    folder, package = _export(tmp_path, TWO_SUBJECTS)
    qid = _question(package, hr.QuestionKind.CONFIRM_IDENTITY, "tree_1")
    payload = _response(package, {qid: {"answer": "agree"}}, date="2026-02-30")

    with pytest.raises(hr.HumanReviewError, match="not a usable response"):
        hr.import_answers(folder, _answers_file(tmp_path, payload))


def test_an_oversized_response_is_not_read(tmp_path):
    folder, _ = _export(tmp_path, TWO_SUBJECTS)
    path = tmp_path / "huge.json"
    path.write_bytes(b" " * (hr.MAX_RESPONSE_BYTES + 1))

    with pytest.raises(hr.HumanReviewError, match="KiB"):
        hr.import_answers(folder, path)


@pytest.mark.parametrize("changed", ["package", "photo"])
def test_a_package_changed_since_export_is_refused(tmp_path, changed):
    folder, package = _export(tmp_path, TWO_SUBJECTS)
    qid = _question(package, hr.QuestionKind.CONFIRM_IDENTITY, "tree_1")
    answers = _answers_file(tmp_path, _response(package, {qid: {"answer": "agree"}}))
    if changed == "package":
        path = folder / hr.PACKAGE_FILE
        path.write_text(path.read_text(encoding="utf-8").replace("furrowed", "smooth"), "utf-8")
    else:
        (folder / hr.PHOTO_DIR / package.photos[0].file_name).write_bytes(b"another photo")

    with pytest.raises(hr.HumanReviewError, match="changed"):
        hr.import_answers(folder, answers)


def _tree_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_a_correction_changes_this_case_only(tmp_path):
    folder, package = _export(tmp_path, TWO_SUBJECTS)
    qid = _question(package, hr.QuestionKind.CONFIRM_IDENTITY, "tree_1")
    answers = _answers_file(
        tmp_path,
        _response(package, {qid: {"answer": "name", "taxon": "fagus", "resolution": "genus"}}),
    )
    knowledge, before = _tree_hashes(KNOWLEDGE), _tree_hashes(tmp_path)

    hr.import_answers(folder, answers)

    after = _tree_hashes(tmp_path)
    assert set(after) - set(before) == {f"package/{hr.LEDGER_FILE}"}
    assert {k: v for k, v in after.items() if k in before} == before
    assert _tree_hashes(KNOWLEDGE) == knowledge


# ---------------------------------------------------------------- the commands


def test_the_commands_export_and_import(tmp_path):
    result, images = _write(tmp_path, TWO_SUBJECTS)
    folder = tmp_path / "package"
    runner = CliRunner()

    refused = runner.invoke(
        app, ["review-export", str(result), "--out", str(folder), "-i", str(images[0])]
    )
    exported = runner.invoke(
        app,
        [
            "review-export",
            str(result),
            "--out",
            str(folder),
            "-i",
            str(images[0]),
            "--allow-location",
        ],
    )
    package = hr.load_package(folder)
    qid = _question(package, hr.QuestionKind.CONFIRM_IDENTITY, "tree_1")
    answers = _answers_file(tmp_path, _response(package, {qid: {"answer": "agree"}}))
    imported = runner.invoke(app, ["review-import", str(folder), str(answers)])

    assert refused.exit_code == 1
    assert exported.exit_code == 0, exported.output
    assert package.package_id in exported.output
    assert imported.exit_code == 0, imported.output
    assert "tree_1: answer named quercus; confirmed" in imported.output
    assert "no knowledge card, prompt or policy was changed" in imported.output
