"""One canonical copy of the domain prompt, and only one.

Two copies is not redundancy, it is a split brain waiting to happen: someone updates one,
forgets the other, and the run trace's hash now attests to a file nobody edited.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

CANONICAL = Path("prompts/domain/system-prompt.md")

#: Directories that never contain a prompt copy and would slow the scan to a crawl.
SKIP_DIRS = frozenset(
    {
        ".git",
        ".bridge",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        "dist",
        "build",
        "traces",
        "runs",
    }
)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _candidate_files(root: Path):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if SKIP_DIRS & set(path.relative_to(root).parts):
            continue
        if path.suffix.lower() in {".md", ".txt", ".prompt"}:
            yield path


def test_the_canonical_domain_prompt_exists(repo_root):
    assert (repo_root / CANONICAL).is_file()


def test_no_other_file_duplicates_the_domain_prompt(repo_root):
    """The gate that stops a second copy drifting out of step with the first."""
    canonical = repo_root / CANONICAL
    expected = _digest(canonical)

    duplicates = [
        path.relative_to(repo_root).as_posix()
        for path in _candidate_files(repo_root)
        if path != canonical and _digest(path) == expected
    ]

    assert duplicates == [], (
        "these files are byte-identical copies of the domain prompt: "
        f"{duplicates}. Keep exactly one canonical copy at {CANONICAL.as_posix()} and "
        "point at alternatives with DENDRO_DOMAIN_PROMPT_PATH."
    )


def test_personality_profiles_are_not_the_domain_prompt(repo_root):
    """Register lives apart from policy — including at the byte level."""
    expected = _digest(repo_root / CANONICAL)
    for path in (repo_root / "prompts" / "personality").glob("*.md"):
        assert _digest(path) != expected
