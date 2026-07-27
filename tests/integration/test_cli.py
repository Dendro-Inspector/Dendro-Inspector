"""CLI smoke tests. The CLI is a thin shell; these check the shell, not the science."""

from __future__ import annotations

import hashlib
import json
import shutil

import pytest
from typer.testing import CliRunner

from evil_duck_dendro.cli import app
from evil_duck_dendro.prompts.seal import SEMANTIC_NOTICE

runner = CliRunner()


@pytest.fixture(autouse=True)
def _in_repo_root(repo_root, monkeypatch):
    """Data paths resolve relative to the working directory by design."""
    monkeypatch.chdir(repo_root)


@pytest.fixture
def deployment(repo_root, tmp_path, monkeypatch):
    """A throwaway copy of the prompt tree, run from as if it were the deployment root."""
    shutil.copytree(repo_root / "prompts", tmp_path / "prompts")
    monkeypatch.chdir(tmp_path)
    return tmp_path


class TestGraphCommand:
    def test_it_prints_a_mermaid_flowchart(self):
        result = runner.invoke(app, ["graph"])
        assert result.exit_code == 0
        assert result.stdout.startswith("flowchart TD")

    def test_it_can_write_to_a_file(self, tmp_path):
        out = tmp_path / "graph.mmd"
        result = runner.invoke(app, ["graph", "--out", str(out)])
        assert result.exit_code == 0
        assert out.read_text(encoding="utf-8").startswith("flowchart TD")


class TestInspectCommand:
    def test_fake_mode_produces_a_human_readable_answer(self):
        result = runner.invoke(
            app,
            [
                "inspect",
                "--fake",
                "primary-pass",
                "--image",
                "examples/log.jpg",
                "--object-type",
                "log",
            ],
        )
        assert result.exit_code == 0
        # Ukrainian is the default output language, per the domain prompt.
        assert "Pinus" in result.stdout
        assert "Кряк" in result.stdout

    def test_json_mode_emits_response_and_trace(self):
        result = runner.invoke(
            app,
            ["inspect", "--fake", "primary-pass", "--image", "examples/log.jpg", "--json"],
        )
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["response"]["results"][0]["taxonomic_resolution"] == "genus"
        prompt = payload["trace"]["domain_prompt"]
        assert prompt["sha256"]
        assert prompt["policy_revision"] == "0.2.3"
        assert prompt["manifest_sha256"]
        assert prompt["compatibility_status"] == "compatible"

    def test_missing_input_is_rejected_with_a_usage_error(self):
        assert runner.invoke(app, ["inspect"]).exit_code == 2

    def test_a_nonexistent_image_still_runs_in_fake_mode(self):
        """Fixtures supply the evidence; the missing file is recorded as a limitation."""
        result = runner.invoke(
            app,
            ["inspect", "--fake", "primary-pass", "--image", "no/such/file.jpg"],
        )
        assert result.exit_code == 0

    def test_trace_can_be_written_to_disk(self, tmp_path):
        result = runner.invoke(
            app,
            [
                "inspect",
                "--fake",
                "primary-pass",
                "--image",
                "examples/log.jpg",
                "--case-id",
                "trace-test",
                "--trace-out",
                str(tmp_path),
            ],
        )
        assert result.exit_code == 0
        trace = json.loads((tmp_path / "trace-test.trace.json").read_text(encoding="utf-8"))
        assert trace["case_id"] == "trace-test"
        assert trace["graph_version"]
        assert trace["domain_prompt"]["compatibility_status"] == "compatible"

    def test_incompatible_prompt_policy_exits_cleanly(self, tmp_path, monkeypatch):
        manifest = tmp_path / "incompatible.yaml"
        manifest.write_text(
            'schema_version: "1"\npolicy_revision: "0.2.1"\n',
            encoding="utf-8",
        )
        monkeypatch.setenv("EVIL_DUCK_PROMPT_MANIFEST_PATH", str(manifest))

        result = runner.invoke(
            app,
            ["inspect", "--fake", "primary-pass", "--image", "examples/log.jpg"],
        )

        assert result.exit_code == 3
        assert "policy_revision" in result.stderr


class TestEvalCommand:
    def test_public_suite_passes(self):
        result = runner.invoke(app, ["eval", "--suite", "public"])
        assert result.exit_code == 0, result.stdout
        assert "Failed: 0" in result.stdout

    def test_json_report_is_machine_readable(self, tmp_path):
        out = tmp_path / "report.json"
        result = runner.invoke(app, ["eval", "--suite", "public", "--json", "--out", str(out)])
        assert result.exit_code == 0
        report = json.loads(out.read_text(encoding="utf-8"))
        assert report["failed"] == 0
        assert report["metrics"]["cases"] >= 5

    def test_unknown_suite_fails_cleanly(self):
        result = runner.invoke(app, ["eval", "--suite", "does-not-exist"])
        assert result.exit_code != 0

    def test_it_configures_structured_logging_like_inspect_does(self, monkeypatch):
        """A suite run emits the same warnings a real run does; they must not print bare."""
        calls: list[tuple[str, bool]] = []
        monkeypatch.setattr(
            "evil_duck_dendro.cli.configure_logging",
            lambda level, *, json_output: calls.append((level, json_output)),
        )

        result = runner.invoke(app, ["eval", "--suite", "public"])

        assert result.exit_code == 0, result.stdout
        assert calls == [("INFO", True)]

    def test_logging_configuration_comes_from_the_environment_not_a_constant(self, monkeypatch):
        calls: list[tuple[str, bool]] = []
        monkeypatch.setattr(
            "evil_duck_dendro.cli.configure_logging",
            lambda level, *, json_output: calls.append((level, json_output)),
        )
        monkeypatch.setenv("EVIL_DUCK_LOG_LEVEL", "DEBUG")

        runner.invoke(app, ["eval", "--suite", "public"])

        assert calls == [("DEBUG", True)]


class TestPromptSealCommand:
    def test_the_shipped_bundle_needs_no_re_sealing(self):
        result = runner.invoke(app, ["prompt-seal"])
        assert result.exit_code == 0
        assert "Nothing to re-seal" in result.stdout

    def test_dry_run_prints_old_to_new_and_writes_nothing(self, deployment):
        manifest = deployment / "prompts" / "versions.yaml"
        before = manifest.read_bytes()
        prompt = deployment / "prompts" / "domain" / "system-prompt.md"
        prompt.write_bytes(prompt.read_bytes() + b"\nowner edit\n")
        new_hash = hashlib.sha256(prompt.read_bytes()).hexdigest()

        result = runner.invoke(app, ["prompt-seal"])

        assert result.exit_code == 0
        assert (
            f"b4c38c00ac0a274322488cf93ed504ac4d19617e6dde0a55f4600126c06cf7d7 -> {new_hash}"
            in result.stdout
        )
        assert "Dry run" in result.stdout
        assert manifest.read_bytes() == before

    def test_dry_run_says_it_attests_bytes_not_semantics(self, deployment):
        prompt = deployment / "prompts" / "domain" / "system-prompt.md"
        prompt.write_bytes(prompt.read_bytes() + b"\nowner edit\n")

        result = runner.invoke(app, ["prompt-seal"])

        assert SEMANTIC_NOTICE in result.stdout

    def test_write_re_seals_and_revalidates_the_bundle(self, deployment):
        prompt = deployment / "prompts" / "domain" / "system-prompt.md"
        prompt.write_bytes(prompt.read_bytes() + b"\nowner edit\n")
        new_hash = hashlib.sha256(prompt.read_bytes()).hexdigest()

        result = runner.invoke(app, ["prompt-seal", "--write"])

        assert result.exit_code == 0
        assert "revalidated" in result.stdout
        assert new_hash in (deployment / "prompts" / "versions.yaml").read_text(encoding="utf-8")
        assert runner.invoke(app, ["prompt-info"]).exit_code == 0

    def test_it_refuses_a_manifest_bound_to_another_policy_revision(self, deployment):
        manifest = deployment / "prompts" / "versions.yaml"
        manifest.write_text(
            manifest.read_text(encoding="utf-8").replace(
                'policy_revision: "0.2.3"', 'policy_revision: "0.2.1"'
            ),
            encoding="utf-8",
        )

        result = runner.invoke(app, ["prompt-seal", "--write"])

        assert result.exit_code == 3
        assert "policy_revision" in result.stderr
        assert 'policy_revision: "0.2.1"' in manifest.read_text(encoding="utf-8")


class TestPromptInfoCommand:
    def test_it_reports_the_loaded_prompt_identity(self):
        result = runner.invoke(app, ["prompt-info"])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert len(payload["sha256"]) == 64
        assert payload["is_placeholder"] is False
        assert payload["version"] == "user-managed"
        assert payload["manifest_schema_version"] == "1"
        assert payload["policy_revision"] == "0.2.3"
        assert payload["node_prompt_revision"] == "0.2.0"
        assert len(payload["manifest_sha256"]) == 64
        assert payload["compatibility_status"] == "compatible"

    def test_incompatible_manifest_fails_cleanly(self, tmp_path, monkeypatch):
        manifest = tmp_path / "incompatible.yaml"
        manifest.write_text(
            'schema_version: "1"\npolicy_revision: "0.2.1"\n',
            encoding="utf-8",
        )
        monkeypatch.setenv("EVIL_DUCK_PROMPT_MANIFEST_PATH", str(manifest))

        result = runner.invoke(app, ["prompt-info"])

        assert result.exit_code == 3
        assert "incompatible" in result.stderr.lower()
        assert "policy_revision" in result.stderr
