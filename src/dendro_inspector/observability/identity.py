"""The runtime identity a trace records and a release manifest states.

A stored answer can be reproduced only if the run that made it can be named. The trace
already names the code (`code_commit_sha`) and the prompts (`PromptMetadata`). This names
the other two inputs that change an answer: the knowledge the loader can read, and the
configuration that changes decisions. Both are digests over a canonical form, so the same
cards and settings give the same digest on any machine and in any checkout:

* knowledge: every file the loader reads (taxon, comparison and region cards, the
  vocabulary and value sets) by its path relative to the knowledge root, with line endings
  normalised. ``review-queue/`` is not read by the loader and is not counted;
* configuration: escalation and graph settings, the prompt shaping (tone, domain sections),
  the region pack, and each role's adapter, model and retry setting. Paths, credentials and
  logging settings are left out: they differ between machines without changing an answer.

`dendro release-manifest` prints the same identity for an installation or an image, so a
trace can be matched to the release that produced it.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from dendro_inspector import __version__
from dendro_inspector.config import AppConfig
from dendro_inspector.observability.events import (
    GRAPH_VERSION,
    TRACE_SCHEMA_VERSION,
    PromptMetadata,
    RuntimeIdentity,
)
from dendro_inspector.observability.trace import _discover_code_revision
from dendro_inspector.schemas.decisions import RESULT_SCHEMA_VERSION

#: What `KnowledgeBase` reads, relative to the knowledge root.
KNOWLEDGE_FILES = (
    "taxa/*.yaml",
    "comparisons/*.yaml",
    "regions/*.yaml",
    "vocabulary.yaml",
    "value_sets.yaml",
)


def _text_digest(path: Path) -> str:
    """SHA-256 of a text file with CRLF read as LF, so a Windows checkout matches."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def knowledge_digest(root: Path) -> tuple[str, int]:
    """One digest over every knowledge file the loader reads, and how many there were."""
    files = sorted(
        {path for pattern in KNOWLEDGE_FILES for path in root.glob(pattern) if path.is_file()}
    )
    lines = [f"{path.relative_to(root).as_posix()}\0{_text_digest(path)}\n" for path in files]
    return hashlib.sha256("".join(lines).encode("utf-8")).hexdigest(), len(files)


def decision_config(config: AppConfig) -> dict[str, Any]:
    """The settings that change an answer, in a form that is the same on every machine."""
    return {
        "escalation": config.escalation.model_dump(mode="json"),
        "graph": config.graph.model_dump(mode="json"),
        "prompts": {
            "tone_profile": config.prompts.tone_profile.value,
            "domain_sections": config.prompts.domain_sections.value,
        },
        "knowledge": {
            "region_pack": config.knowledge.region_pack,
            "reference_records": config.knowledge.reference_records is not None,
        },
        "providers": {
            role.value: {
                "adapter": provider.adapter.value,
                "model": provider.model,
                "scenario": provider.scenario,
                "max_structured_retries": provider.max_structured_retries,
            }
            for role, provider in sorted(config.providers.items(), key=lambda item: item[0].value)
        },
    }


def _canonical_digest(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def runtime_identity(config: AppConfig, *, root: Path | None = None) -> RuntimeIdentity:
    """The identity of this process's knowledge and configuration. ``root`` resolves paths."""
    base = root or Path.cwd()
    knowledge_root = config.knowledge.root
    if not knowledge_root.is_absolute():
        knowledge_root = base / knowledge_root
    knowledge_sha256, knowledge_files = knowledge_digest(knowledge_root)
    records = config.knowledge.reference_records
    if records is not None and not records.is_absolute():
        records = base / records
    settings = decision_config(config)
    return RuntimeIdentity(
        package_version=__version__,
        knowledge_sha256=knowledge_sha256,
        knowledge_files=knowledge_files,
        reference_records_sha256=(
            _text_digest(records) if records is not None and records.is_file() else None
        ),
        config_sha256=_canonical_digest(settings),
        config=settings,
    )


#: Version of the manifest's own shape.
MANIFEST_SCHEMA_VERSION = 1


def release_manifest(
    config: AppConfig, prompt: PromptMetadata, *, root: Path | None = None
) -> dict[str, Any]:
    """Everything an answer from this installation depends on, besides its input."""
    base = root or Path.cwd()
    commit, dirty = _discover_code_revision(base)
    return {
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "package_version": __version__,
        "graph_version": GRAPH_VERSION,
        "trace_schema_version": TRACE_SCHEMA_VERSION,
        "response_schema_version": RESULT_SCHEMA_VERSION,
        "code_commit_sha": commit,
        "code_dirty": dirty,
        "domain_prompt": prompt.model_dump(mode="json"),
        "runtime_identity": runtime_identity(config, root=base).model_dump(mode="json"),
    }


def _stored_trace(stored: Any) -> dict[str, Any]:
    """The trace in `dendro inspect --json` output, a batch case record, or a bare trace."""
    if not isinstance(stored, dict):
        return {}
    for holder in (stored.get("result"), stored):
        trace = holder.get("trace") if isinstance(holder, dict) else None
        if isinstance(trace, dict):
            return trace
    return stored if "case_id" in stored and "events" in stored else {}


def _get(mapping: Any, *keys: str) -> Any:
    for key in keys:
        mapping = mapping.get(key) if isinstance(mapping, dict) else None
    return mapping


def _flatten(prefix: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {prefix: value}
    flat: dict[str, Any] = {}
    for key, item in value.items():
        flat.update(_flatten(f"{prefix}.{key}" if prefix else str(key), item))
    return flat


#: What a stored answer is compared on: a name, and where each side keeps the value.
COMPARED = (
    ("code commit", ("code_commit_sha",), ("code_commit_sha",)),
    ("graph version", ("graph_version",), ("graph_version",)),
    ("domain prompt", ("domain_prompt", "sha256"), ("domain_prompt", "sha256")),
    ("prompt manifest", ("domain_prompt", "manifest_sha256"), ("domain_prompt", "manifest_sha256")),
    ("prompt policy", ("domain_prompt", "policy_revision"), ("domain_prompt", "policy_revision")),
    (
        "knowledge",
        ("runtime_identity", "knowledge_sha256"),
        ("runtime_identity", "knowledge_sha256"),
    ),
    ("configuration", ("runtime_identity", "config_sha256"), ("runtime_identity", "config_sha256")),
)


def compare(manifest: dict[str, Any], stored: Any) -> tuple[list[tuple[str, str]], list[str]]:
    """Per identity, whether a stored answer shares it; and each setting that differs.

    A state is ``same``, ``differs``, or ``not recorded`` (a trace older than the record, or
    a case record kept without its trace).
    """
    trace = _stored_trace(stored)
    rows = []
    for name, ours_at, theirs_at in COMPARED:
        ours, theirs = _get(manifest, *ours_at), _get(trace, *theirs_at)
        state = "not recorded" if theirs is None else ("same" if ours == theirs else "differs")
        rows.append((name, state))
    ours_config = _flatten("", _get(manifest, "runtime_identity", "config"))
    theirs_config = _get(trace, "runtime_identity", "config")
    settings = []
    if isinstance(theirs_config, dict):
        theirs_flat = _flatten("", theirs_config)
        for key in sorted(set(ours_config) | set(theirs_flat)):
            if ours_config.get(key) != theirs_flat.get(key):
                settings.append(
                    f"{key}: recorded {theirs_flat.get(key)!r}, here {ours_config.get(key)!r}"
                )
    return rows, settings
