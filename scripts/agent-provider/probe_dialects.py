"""Control experiment for the bridge: prove its strict vendor checks can actually fail.

A run where every request was accepted only shows `providers/schema_compat.py` is *sufficient*
if the same endpoints reject the untranslated schema. This sends both — the raw Pydantic JSON
Schema for two real node contracts, and the translated one — to a running `bridge.py`, and
prints what each host did with them.

Run it while the bridge is up, before or after an inspection:

    python scripts/agent-provider/bridge.py                   # in one shell
    python scripts/agent-provider/probe_dialects.py           # in another

Nothing here needs an answer file: every request is expected to fail at the dialect check or
at the credential check, so the bridge never blocks waiting for a model.
"""

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from typing import Any

from dendro_inspector.nodes.evidence_extractor import GeneratedEvidencePacket
from dendro_inspector.providers.schema_compat import (
    to_gemini_schema,
    to_ollama_schema,
    to_openai_schema,
)
from dendro_inspector.schemas.reviews import ReviewResult

INTERESTING_KEYWORDS = frozenset({"$ref", "$defs", "const", "additionalProperties", "allOf"})


def post(base: str, path: str, body: dict[str, Any], headers: dict[str, str]) -> tuple[int, str]:
    request = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read().decode("utf-8")[:160]
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")[:160]
    except urllib.error.URLError as exc:
        return 0, f"could not reach the bridge at {base}: {exc.reason}"


def gemini_body(schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "contents": [{"role": "user", "parts": [{"text": "control probe"}]}],
        "generationConfig": {"responseMimeType": "application/json", "responseSchema": schema},
    }


def ollama_body(schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "model": "control",
        "messages": [{"role": "user", "content": "control probe"}],
        "format": schema,
        "stream": False,
    }


def openai_body(schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "model": "control",
        "messages": [{"role": "user", "content": [{"type": "text", "text": "control probe"}]}],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "Control", "schema": schema, "strict": True},
        },
    }


ROUTES = (
    ("gemini", "/v1beta/models/control:generateContent", gemini_body, {"x-goog-api-key": "probe"}),
    ("ollama", "/api/chat", ollama_body, {}),
    ("openai", "/v1/chat/completions", openai_body, {"Authorization": "Bearer probe"}),
)


def patterns(node: Any) -> list[str]:
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "pattern" and isinstance(value, str):
                found.append(value)
            else:
                found.extend(patterns(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(patterns(item))
    return found


def keywords(node: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            found.add(key)
            if key in {"properties", "$defs"} and isinstance(value, dict):
                for sub in value.values():
                    found |= keywords(sub)
            else:
                found |= keywords(value)
    elif isinstance(node, list):
        for item in node:
            found |= keywords(item)
    return found


def escaped(pattern_list: list[str]) -> int:
    return sum(1 for pattern in pattern_list if "\\-" in pattern)


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe a running bridge with raw schemas.")
    parser.add_argument("--base", default="http://127.0.0.1:8799")
    args = parser.parse_args()

    for model in (GeneratedEvidencePacket, ReviewResult):
        raw = model.model_json_schema()
        raw_patterns = patterns(raw)
        print(f"=== {model.__name__} ===")
        print(f"  raw keywords of interest      {sorted(keywords(raw) & INTERESTING_KEYWORDS)}")
        print(f"  raw patterns, escaped hyphen  {escaped(raw_patterns)} of {len(raw_patterns)}")
        gemini_patterns = patterns(to_gemini_schema(raw))
        ollama_patterns = patterns(to_ollama_schema(raw))
        openai_patterns = patterns(to_openai_schema(raw))
        print(
            "  after translation             "
            f"gemini keywords {sorted(keywords(to_gemini_schema(raw)) & INTERESTING_KEYWORDS)}, "
            f"gemini escaped {escaped(gemini_patterns)}, "
            f"ollama patterns {len(ollama_patterns)}, "
            f"openai escaped {escaped(openai_patterns)} of {len(openai_patterns)}"
        )
        for label, path, body, headers in ROUTES:
            status, detail = post(args.base, path, body(raw), headers)
            print(f"  UNTRANSLATED -> {label:<7} HTTP {status} {detail.strip()[:110]}")

    print("=== credential routing ===")
    for label, path, body, _ in ROUTES:
        if label == "ollama":
            continue  # Ollama has no credential to withhold.
        status, detail = post(args.base, path, body({"type": "object"}), {})
        print(f"  no credential -> {label:<7} HTTP {status} {detail.strip()[:110]}")


if __name__ == "__main__":
    main()
