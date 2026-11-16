"""A trace names the knowledge and settings it ran with, and a release manifest can be compared.

Without these, a stored answer said which commit and prompt produced it but not which cards
were loaded or which experiments were on, so two answers from one image could not be told
apart, and an answer could not be matched to a release.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from dendro_inspector.cli import app
from dendro_inspector.config import AppConfig, ObservabilityConfig, PromptConfig, ReviewAuthority
from dendro_inspector.observability import identity
from dendro_inspector.observability.events import TRACE_SCHEMA_VERSION, PromptMetadata
from dendro_inspector.runner import build_context

REPO = Path(__file__).parents[2]
PROMPT = PromptMetadata(
    path="prompts/domain/system-prompt.en.md",
    sha256="a" * 64,
    bytes=1,
    manifest_sha256="b" * 64,
    policy_revision="0.9.0",
)


def _knowledge(root: Path) -> Path:
    (root / "taxa").mkdir(parents=True)
    (root / "taxa" / "quercus.yaml").write_bytes(b"identity:\n  taxon_id: quercus\n")
    (root / "vocabulary.yaml").write_bytes(b"pairs: []\n")
    return root


def test_the_knowledge_digest_covers_what_the_loader_reads(tmp_path):
    root = _knowledge(tmp_path / "knowledge")
    digest, count = identity.knowledge_digest(root)

    (root / "review-queue").mkdir()
    (root / "review-queue" / "draft.yaml").write_bytes(b"unreviewed: true\n")
    (root / "notes.txt").write_bytes(b"not knowledge\n")
    assert identity.knowledge_digest(root) == (digest, count) == (digest, 2)

    (root / "taxa" / "quercus.yaml").write_bytes(b"identity:\r\n  taxon_id: quercus\r\n")
    assert identity.knowledge_digest(root) == (digest, 2), "a CRLF checkout is the same knowledge"

    (root / "taxa" / "quercus.yaml").write_bytes(b"identity:\n  taxon_id: fagus\n")
    assert identity.knowledge_digest(root)[0] != digest


def test_a_renamed_card_is_different_knowledge(tmp_path):
    root = _knowledge(tmp_path / "knowledge")
    digest, _ = identity.knowledge_digest(root)

    (root / "taxa" / "quercus.yaml").rename(root / "taxa" / "fagus.yaml")

    assert identity.knowledge_digest(root)[0] != digest


def test_only_a_setting_that_changes_an_answer_changes_the_configuration_identity(tmp_path):
    base = AppConfig()
    knowledge = _knowledge(tmp_path / "knowledge")
    config = base.model_copy(
        update={"knowledge": base.knowledge.model_copy(update={"root": knowledge})}
    )
    first = identity.runtime_identity(config)

    cosmetic = config.model_copy(
        update={
            "observability": ObservabilityConfig(log_level="DEBUG", trace_dir=tmp_path),
            "prompts": PromptConfig(domain_prompt_path=tmp_path / "elsewhere.md"),
        }
    )
    deciding = config.model_copy(
        update={
            "graph": config.graph.model_copy(
                update={"review_authority": ReviewAuthority.EVIDENCE_BOUND}
            )
        }
    )

    assert identity.runtime_identity(cosmetic).config_sha256 == first.config_sha256
    changed = identity.runtime_identity(deciding)
    assert changed.config_sha256 != first.config_sha256
    assert changed.config["graph"]["review_authority"] == "evidence_bound"
    assert changed.knowledge_sha256 == first.knowledge_sha256


def test_the_configuration_record_holds_no_path_or_credential_name():
    recorded = json.dumps(identity.decision_config(AppConfig()))

    assert "api_key_env" not in recorded
    assert "domain_prompt_path" not in recorded
    assert "trace_dir" not in recorded


def test_a_run_records_its_runtime_identity():
    ctx = build_context(root=REPO, case_id="identity")
    trace = ctx.recorder.build()

    assert trace.trace_schema_version == TRACE_SCHEMA_VERSION
    assert trace.runtime_identity == identity.runtime_identity(ctx.config, root=REPO)
    assert trace.runtime_identity is not None
    assert trace.runtime_identity.knowledge_files > 0


def _stored_from(manifest: dict[str, object]) -> dict[str, object]:
    return {
        "response": {},
        "trace": {
            "case_id": "c",
            "code_commit_sha": manifest["code_commit_sha"],
            "graph_version": manifest["graph_version"],
            "domain_prompt": manifest["domain_prompt"],
            "runtime_identity": json.loads(json.dumps(manifest["runtime_identity"])),
        },
    }


def test_a_stored_answer_is_compared_identity_by_identity():
    manifest = identity.release_manifest(AppConfig(), PROMPT, root=REPO)
    stored = _stored_from(manifest)

    rows, settings = identity.compare(manifest, stored)
    # A checkout without git and without a build stamp has no commit to compare.
    assert dict(rows).pop("code commit") in ("same", "not recorded")
    assert {state for name, state in rows if name != "code commit"} == {"same"}
    assert settings == []

    recorded = stored["trace"]["runtime_identity"]  # type: ignore[index]
    recorded["config"]["graph"]["review_authority"] = "evidence_bound"
    recorded["config_sha256"] = "0" * 64
    rows, settings = identity.compare(manifest, {"result": stored})
    assert dict(rows)["configuration"] == "differs"
    assert dict(rows)["knowledge"] == "same"
    assert settings == ["graph.review_authority: recorded 'evidence_bound', here 'recommendation'"]


def test_an_older_trace_has_no_identity_to_compare():
    manifest = identity.release_manifest(AppConfig(), PROMPT, root=REPO)
    older = {"case_id": "c", "events": [], "graph_version": manifest["graph_version"]}

    rows, settings = identity.compare(manifest, older)

    assert dict(rows)["knowledge"] == dict(rows)["configuration"] == "not recorded"
    assert dict(rows)["graph version"] == "same"
    assert settings == []


def test_the_command_prints_a_manifest_and_compares(tmp_path):
    runner = CliRunner()
    printed = runner.invoke(app, ["release-manifest", "--out", str(tmp_path / "m.json")])
    assert printed.exit_code == 0, printed.output
    manifest = json.loads(printed.output)
    assert json.loads((tmp_path / "m.json").read_text(encoding="utf-8")) == manifest
    assert manifest["trace_schema_version"] == TRACE_SCHEMA_VERSION
    assert set(manifest["runtime_identity"]) >= {"knowledge_sha256", "config_sha256", "config"}

    stored = _stored_from(manifest)
    stored["trace"]["runtime_identity"]["knowledge_sha256"] = "1" * 64  # type: ignore[index]
    path = tmp_path / "answer.json"
    path.write_text(json.dumps(stored), encoding="utf-8")
    compared = runner.invoke(app, ["release-manifest", "--compare", str(path)])

    assert compared.exit_code == 1
    assert "knowledge        differs" in compared.output
    assert "configuration    same" in compared.output
