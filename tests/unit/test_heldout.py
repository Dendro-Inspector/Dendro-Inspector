"""The held-out manifest: independence, leakage and review state are checked, not trusted.

Photographs here are a few synthetic bytes in a temporary folder; only their hashes matter.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from dendro_inspector.cli import app
from dendro_inspector.evaluation import heldout
from dendro_inspector.evaluation.label_scoring import load_labels


def _photo(folder: Path, name: str, content: bytes | None = None) -> str:
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content if content is not None else name.encode())
    return path.relative_to(folder).as_posix()


def _unit(unit_id: str, group: str, photos: list[str], genus: str | None = "quercus", **extra):
    return {
        "unit_id": unit_id,
        "group_id": group,
        "photos": photos,
        "label": {"genus": genus, **extra.pop("label", {})},
        **extra,
    }


def _agree(**extra):
    return {
        "reviewer": "F.R.",
        "qualification": "forester",
        "verdict": "agree",
        "date": "2026-11-20",
        **extra,
    }


def _disagree(genus: str):
    return {
        "reviewer": "B.T.",
        "qualification": "botanist",
        "verdict": "disagree",
        "genus": genus,
        "date": "2026-11-21",
    }


def _manifest(tmp_path: Path, units: list[dict[str, object]], **extra) -> Path:
    path = tmp_path / "heldout.yaml"
    path.write_text(yaml.safe_dump({"name": "test", "units": units, **extra}), encoding="utf-8")
    return path


def _check(path: Path, knowledge) -> heldout.CheckReport:
    return heldout.check(heldout.load_manifest(path), path.parent, knowledge)


class TestIndependence:
    def test_a_clean_set_passes(self, tmp_path, knowledge):
        path = _manifest(
            tmp_path,
            [
                _unit("h1", "tree-1", [_photo(tmp_path, "a.jpg")]),
                _unit("h2", "tree-2", [_photo(tmp_path, "b.jpg")], genus="pinus"),
            ],
        )
        report = _check(path, knowledge)
        assert report.ok, report.errors
        assert (report.units, report.groups) == (2, 2)

    def test_the_same_photograph_in_two_units_is_an_error(self, tmp_path, knowledge):
        a = _photo(tmp_path, "a.jpg", b"same")
        b = _photo(tmp_path, "copy/a-again.jpg", b"same")
        path = _manifest(tmp_path, [_unit("h1", "tree-1", [a]), _unit("h2", "tree-2", [b])])
        report = _check(path, knowledge)
        assert any("same photograph as unit h1" in error for error in report.errors)

    def test_a_photograph_used_in_development_is_an_error(self, tmp_path, knowledge):
        _photo(tmp_path, "dev/old.jpg", b"seen before")
        leaked = _photo(tmp_path, "new/x.jpg", b"seen before")
        path = _manifest(
            tmp_path, [_unit("h1", "tree-1", [leaked])], development_photo_dirs=["dev"]
        )
        report = _check(path, knowledge)
        assert any("used in development" in error for error in report.errors)

    def test_without_development_dirs_leakage_is_reported_unchecked(self, tmp_path, knowledge):
        path = _manifest(tmp_path, [_unit("h1", "tree-1", [_photo(tmp_path, "a.jpg")])])
        assert any("not checked" in w for w in _check(path, knowledge).warnings)

    def test_one_tree_with_two_genera_is_an_error(self, tmp_path, knowledge):
        path = _manifest(
            tmp_path,
            [
                _unit("h1", "tree-1", [_photo(tmp_path, "a.jpg")], genus="quercus"),
                _unit("h2", "tree-1", [_photo(tmp_path, "b.jpg")], genus="fagus"),
            ],
        )
        assert any("tree-1" in error for error in _check(path, knowledge).errors)

    def test_a_mixed_unit_may_differ_from_its_group(self, tmp_path, knowledge):
        path = _manifest(
            tmp_path,
            [
                _unit("h1", "lot-1", [_photo(tmp_path, "a.jpg")], genus="quercus"),
                _unit(
                    "h2",
                    "lot-1",
                    [_photo(tmp_path, "b.jpg")],
                    genus="fagus",
                    categories=["mixed_subjects"],
                ),
            ],
        )
        assert _check(path, knowledge).ok

    def test_a_missing_photograph_is_an_error(self, tmp_path, knowledge):
        path = _manifest(tmp_path, [_unit("h1", "tree-1", ["nowhere.jpg"])])
        assert any("not found" in error for error in _check(path, knowledge).errors)

    def test_a_repeated_unit_id_is_an_error(self, tmp_path, knowledge):
        path = _manifest(
            tmp_path,
            [
                _unit("h1", "tree-1", [_photo(tmp_path, "a.jpg")]),
                _unit("h1", "tree-2", [_photo(tmp_path, "b.jpg")]),
            ],
        )
        assert any("more than once" in error for error in _check(path, knowledge).errors)


class TestCategoriesAndSize:
    def test_a_genus_without_a_card_counts_as_unknown_taxon(self, tmp_path, knowledge):
        path = _manifest(tmp_path, [_unit("h1", "t1", [_photo(tmp_path, "a.jpg")], genus="ginkgo")])
        assert _check(path, knowledge).categories.get("unknown_taxon") == 1

    def test_unknown_taxon_on_a_covered_genus_is_an_error(self, tmp_path, knowledge):
        path = _manifest(
            tmp_path, [_unit("h1", "t1", [_photo(tmp_path, "a.jpg")], categories=["unknown_taxon"])]
        )
        assert any("unknown_taxon" in error for error in _check(path, knowledge).errors)

    def test_missing_categories_and_size_are_warnings(self, tmp_path, knowledge):
        path = _manifest(tmp_path, [_unit("h1", "t1", [_photo(tmp_path, "a.jpg")])])
        report = _check(path, knowledge)
        assert report.ok
        assert "bark_only" in report.missing_categories
        assert any("planning target is 100-200" in w for w in report.warnings)


class TestReviewStatusIsDerived:
    def _unit(self, *reviews) -> heldout.Unit:
        return heldout.Unit.model_validate(_unit("h1", "t1", ["a.jpg"], reviews=list(reviews)))

    def test_no_review_is_preliminary(self):
        assert heldout.review_status(self._unit()) is heldout.ReviewStatus.PRELIMINARY
        assert not heldout.release_ready(self._unit())

    def test_an_agreeing_specialist_makes_it_release_ready(self):
        assert heldout.review_status(self._unit(_agree())) is heldout.ReviewStatus.REVIEWED
        assert heldout.release_ready(self._unit(_agree()))

    def test_a_disagreement_is_disputed_until_two_later_reviews_agree(self):
        disputed = self._unit(_agree(), _disagree("fagus"))
        resolved = self._unit(_disagree("fagus"), _agree(), _agree(reviewer="D.K."))
        assert heldout.review_status(disputed) is heldout.ReviewStatus.DISPUTED
        assert not heldout.release_ready(disputed)
        assert heldout.review_status(resolved) is heldout.ReviewStatus.RESOLVED
        assert heldout.release_ready(resolved)

    def test_a_disagreement_requires_an_explicit_determination(self):
        review = _disagree("fagus")
        assert heldout.Review.model_validate({**review, "genus": None}).genus is None
        del review["genus"]
        with pytest.raises(ValueError, match="names the genus"):
            heldout.Review.model_validate(review)


class TestLabelsExport:
    def test_reviewed_indeterminate_genus_round_trips_without_claiming_a_coverage_gap(
        self, tmp_path, knowledge
    ):
        unit = _unit("h1", "t1", [_photo(tmp_path, "a.jpg")], genus=None, reviews=[_agree()])
        preliminary = _unit("h2", "t2", [_photo(tmp_path, "b.jpg")], genus=None)
        path = _manifest(tmp_path, [unit, preliminary])
        report = _check(path, knowledge)
        assert report.ok and report.genera == {}
        assert report.release_ready_indeterminate_genus_units == 1
        assert "unknown_taxon" not in report.categories
        output = tmp_path / "labels.json"
        result = CliRunner().invoke(app, ["heldout-check", str(path), "--labels-out", str(output)])
        assert result.exit_code == 0, result.output
        assert list(load_labels(output)) == ["h1"]
        assert load_labels(output)["h1"].genus is None
        same_tree = _unit("h3", "t1", [_photo(tmp_path, "c.jpg")], reviews=[_agree()])
        report = _check(_manifest(tmp_path, [unit, same_tree]), knowledge)
        assert report.ok
        assert any("group t1" in w and "no genus" in w for w in report.warnings)
        unit["categories"] = ["unknown_taxon"]
        assert not _check(_manifest(tmp_path, [unit]), knowledge).ok

    def test_release_only_keeps_reviewed_units_and_reads_back(self, tmp_path, knowledge):
        path = _manifest(
            tmp_path,
            [
                _unit(
                    "h1",
                    "t1",
                    [_photo(tmp_path, "a.jpg")],
                    reviews=[_agree()],
                    label={"family": "fagaceae", "label_visible": False},
                ),
                _unit("h2", "t2", [_photo(tmp_path, "b.jpg")], genus="pinus"),
            ],
        )
        manifest = heldout.load_manifest(path)
        release = heldout.labels(manifest, release_only=True)
        assert list(release["labels"]) == ["h1"]
        assert len(heldout.labels(manifest, release_only=False)["labels"]) == 2

        out = tmp_path / "labels.json"
        out.write_text(json.dumps(release), encoding="utf-8")
        loaded = load_labels(out)
        assert loaded["h1"].genus == "quercus"
        assert loaded["h1"].family == "fagaceae"
        assert loaded["h1"].target_subject_ids == "all"
        assert loaded["h1"].label_visible is False


class TestCommand:
    def test_a_failing_set_exits_1_and_writes_no_labels(self, tmp_path):
        path = _manifest(tmp_path, [_unit("h1", "t1", ["nowhere.jpg"])])
        out = tmp_path / "labels.json"
        result = CliRunner().invoke(app, ["heldout-check", str(path), "--labels-out", str(out)])
        assert result.exit_code == 1
        assert "FAILED" in result.output
        assert not out.exists()

    def test_a_clean_set_writes_labels(self, tmp_path):
        path = _manifest(
            tmp_path, [_unit("h1", "t1", [_photo(tmp_path, "a.jpg")], reviews=[_agree()])]
        )
        out = tmp_path / "labels.json"
        result = CliRunner().invoke(app, ["heldout-check", str(path), "--labels-out", str(out)])
        assert result.exit_code == 0, result.output
        assert json.loads(out.read_text(encoding="utf-8"))["labels"]["h1"]["genus"] == "quercus"
