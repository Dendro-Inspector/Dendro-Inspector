"""Release identity is one contract across package, graph, prompts and evaluation."""

from __future__ import annotations

import json
import tomllib

import yaml

from dendro_inspector import __version__
from dendro_inspector.observability.events import GRAPH_VERSION
from dendro_inspector.prompts.library import DETERMINISTIC_POLICY_REVISION


def test_release_identity_is_aligned(repo_root):
    project = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))
    manifest = yaml.safe_load((repo_root / "prompts" / "versions.yaml").read_text(encoding="utf-8"))
    baseline = json.loads(
        (repo_root / "evals" / "baselines" / "public-v0.9.0.json").read_text(encoding="utf-8")
    )

    assert {
        project["project"]["version"],
        __version__,
        GRAPH_VERSION,
        DETERMINISTIC_POLICY_REVISION,
        manifest["policy_revision"],
        baseline["baseline_version"],
    } == {"0.9.0"}
    assert manifest["schema_version"] == "1"
    assert manifest["node_prompts"]["revision"] == "0.3.0"


def test_distribution_metadata_matches_the_shipped_package(repo_root):
    project = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))
    classifiers = set(project["project"]["classifiers"])

    assert "License :: OSI Approved :: Apache Software License" in classifiers
    assert "Typing :: Typed" in classifiers
    marker = repo_root / "src" / "dendro_inspector" / "py.typed"
    assert marker.read_bytes() == b""
