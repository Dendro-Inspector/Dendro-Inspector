"""What a trace says about where it came from and which stage it describes."""

from __future__ import annotations

from dendro_inspector.observability.trace import CODE_COMMIT_ENV, _discover_code_revision
from dendro_inspector.schemas.decisions import AuthorityCheckTrace

SHA = "d65e23c" + "0" * 33


def test_an_image_without_git_reports_its_build_stamped_commit(tmp_path, monkeypatch):
    """Every post-deploy trace read `code_commit_sha: null` although the SHA was known."""
    monkeypatch.setenv(CODE_COMMIT_ENV, SHA.upper())

    assert _discover_code_revision(tmp_path) == (SHA, None)


def test_a_malformed_stamp_is_not_reported(tmp_path, monkeypatch):
    monkeypatch.setenv(CODE_COMMIT_ENV, "not-a-sha")

    assert _discover_code_revision(tmp_path) == (None, None)


def test_authority_outcomes_are_labelled_as_pre_review():
    """`actual_outcome` is computed before review; it is not the final decision."""
    check = AuthorityCheckTrace(subject_id="main_trunk")

    assert check.outcome_stage == "pre_review"
    assert "before review" in (AuthorityCheckTrace.model_fields["actual_outcome"].description or "")
