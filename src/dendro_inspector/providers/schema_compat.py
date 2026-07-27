"""Translate Pydantic JSON Schema into the dialects provider APIs actually accept.

Pydantic emits Draft 2020-12: `$ref` into a `$defs` table, `anyOf` for optionals,
`const` for single-valued literals, `additionalProperties: false` for closed models.
Vendor structured-output endpoints accept narrower dialects, and the failure is never
graceful — Gemini rejects the request outright, and a local Ollama server answers 400
`failed to parse grammar`.

This module is a translation layer, never a relaxation of the contract. Constraints the
target dialect cannot express are dropped from the *request*, not from validation: the
response is still parsed by the original Pydantic model, which enforces every pattern and
bound that was stripped here. A model that ignores a dropped constraint fails validation
and gets the repair retry — the outcome the boundary already handles.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# Gemini's `responseSchema` is a subset of the OpenAPI 3.0 Schema object.
_GEMINI_KEYS = frozenset(
    {
        "type",
        "format",
        "description",
        "nullable",
        "enum",
        "items",
        "properties",
        "required",
        "anyOf",
        "minItems",
        "maxItems",
        "minimum",
        "maximum",
        "propertyOrdering",
        "pattern",
    }
)

_MAX_DEPTH = 64


def normalize_pattern(pattern: str) -> str:
    r"""Rewrite character classes into the form constrained decoders actually accept.

    Pydantic emits ``[a-z0-9_\-]`` — a hyphen escaped inside a character class, which is
    valid PCRE and valid JSON Schema. Both structured-output backends disagree, in opposite
    and equally inconvenient ways. Measured 2026-07-27:

    * Ollama 0.32.4 answers ``400 failed to parse grammar``: llama.cpp's GBNF converter does
      not know the ``\-`` escape.
    * Gemini accepts the schema and then **ignores the constraint** — asked for a token
      matching ``^[a-z0-9][a-z0-9_\-]*$`` it returns ``F1``. Written ``[a-z0-9_-]`` it
      returns ``f1``.

    The silent case is the dangerous one, and it is why this is a rewrite rather than a
    strip: dropping the pattern and dropping the enforcement look identical from here.

    The hyphen is moved to the end of the class rather than merely unescaped, because
    ``[a\-z]`` unescaped in place becomes the range ``a-z`` — a different language. At the
    end it can only mean a literal. Escapes outside a class are left alone: the ``\.`` in
    ``(\.[a-z]+)`` is a literal dot and must stay one.
    """
    out: list[str] = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char != "[":
            if char == "\\" and index + 1 < len(pattern):
                out.append(pattern[index : index + 2])
                index += 2
                continue
            out.append(char)
            index += 1
            continue
        end = index + 1
        if end < len(pattern) and pattern[end] == "^":
            end += 1
        if end < len(pattern) and pattern[end] == "]":  # a `]` first in a class is a literal
            end += 1
        while end < len(pattern) and pattern[end] != "]":
            end += 2 if pattern[end] == "\\" else 1
        if end >= len(pattern):  # unterminated class — not ours to repair
            out.append(pattern[index:])
            break
        body = pattern[index + 1 : end]
        needs_hyphen = "\\-" in body or body.endswith("-")
        body = body.replace("\\-", "").replace("\\.", ".")
        if needs_hyphen:
            body = body.rstrip("-") + "-"
        out.append(f"[{body}]")
        index = end + 1
    return "".join(out)


class SchemaTranslationError(ValueError):
    """The schema cannot be expressed in the target dialect."""


def inline_refs(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve every ``$ref`` against ``$defs``, returning a self-contained schema.

    Recursive models would expand forever; a depth cap turns that into a loud error rather
    than a hung process. No contract in this package is recursive today, and if one becomes
    recursive this is the right place to find out.
    """
    defs = dict(schema.get("$defs", {}))

    def resolve(node: Any, depth: int) -> Any:
        if depth > _MAX_DEPTH:
            msg = f"schema nesting exceeded {_MAX_DEPTH} levels — is a model recursive?"
            raise SchemaTranslationError(msg)
        if isinstance(node, list):
            return [resolve(item, depth + 1) for item in node]
        if not isinstance(node, dict):
            return node
        ref = node.get("$ref")
        if isinstance(ref, str):
            name = ref.rpartition("/")[2]
            if name not in defs:
                msg = f"unresolvable $ref {ref!r}"
                raise SchemaTranslationError(msg)
            merged = {**defs[name], **{k: v for k, v in node.items() if k != "$ref"}}
            return resolve(merged, depth + 1)
        resolved: dict[str, Any] = {}
        for key, value in node.items():
            if key == "$defs":
                continue
            if key == "properties" and isinstance(value, dict):
                # Field names, not keywords — a field called `$ref` is data, not a pointer.
                resolved[key] = {n: resolve(sub, depth + 1) for n, sub in value.items()}
            else:
                resolved[key] = resolve(value, depth + 1)
        return resolved

    resolved = resolve(dict(schema), 0)
    if not isinstance(resolved, dict):  # pragma: no cover - a schema root is always a mapping
        msg = "schema root did not resolve to an object"
        raise SchemaTranslationError(msg)
    return resolved


def to_ollama_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Pydantic JSON Schema -> a schema llama.cpp can compile into a GBNF grammar.

    Ollama turns the schema into a grammar, and its converter does not accept everything
    JSON Schema allows. Measured against Ollama 0.32.4, gemma4:e4b and gemma3:4b: a
    character class containing an escaped hyphen — ``[a-z0-9_\\-]``, which is what Pydantic
    emits for this project's identifier and value-token types — makes the server answer
    400 ``failed to parse grammar``. The same class written ``[a-z0-9_-]`` compiles.

    Rather than encode which escapes today's converter tolerates, drop ``pattern``
    altogether. The grammar still pins structure, types, enums and required fields; the
    regex is re-imposed where it was always enforced, in Pydantic validation of the
    response. Upstream has a family of these (PCRE shorthands, large ``maxLength``), so a
    narrow fix would only survive until the next one.
    """

    def strip(node: Any) -> Any:
        if isinstance(node, list):
            return [strip(item) for item in node]
        if not isinstance(node, dict):
            return node
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key == "pattern":
                continue
            if key in {"properties", "$defs"} and isinstance(value, dict):
                out[key] = {name: strip(sub) for name, sub in value.items()}
            else:
                out[key] = strip(value)
        return out

    stripped = strip(dict(schema))
    if not isinstance(stripped, dict):  # pragma: no cover - the root stays a mapping
        msg = "stripped schema root is not an object"
        raise SchemaTranslationError(msg)
    return stripped


def to_openai_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Pydantic JSON Schema -> an OpenAI-compatible ``json_schema`` response format.

    OpenAI-compatible servers (NVIDIA NIM, vLLM, and others) accept the schema close to as
    written, including ``$defs``. Patterns are still normalized: these servers constrain
    decoding with a grammar engine of their own, and a character class the engine cannot
    read either fails the request or is quietly ignored — the same two failure modes seen on
    Ollama and Gemini.
    """

    def walk(node: Any) -> Any:
        if isinstance(node, list):
            return [walk(item) for item in node]
        if not isinstance(node, dict):
            return node
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key in {"properties", "$defs"} and isinstance(value, dict):
                out[key] = {name: walk(sub) for name, sub in value.items()}
            elif key == "pattern" and isinstance(value, str):
                out[key] = normalize_pattern(value)
            else:
                out[key] = walk(value)
        return out

    walked = walk(dict(schema))
    if not isinstance(walked, dict):  # pragma: no cover - the root stays a mapping
        msg = "schema root is not an object"
        raise SchemaTranslationError(msg)
    return walked


def _collapse_nullable(node: dict[str, Any]) -> dict[str, Any]:
    """``anyOf: [X, {"type": "null"}]`` is Pydantic for optional; Gemini spells it ``nullable``."""
    options = node.get("anyOf")
    if not isinstance(options, list):
        return node
    concrete = [o for o in options if isinstance(o, dict) and o.get("type") != "null"]
    if len(concrete) != len(options) - 1 or len(concrete) != 1:
        return node
    rest = {k: v for k, v in node.items() if k != "anyOf"}
    return {**concrete[0], **rest, "nullable": True}


def to_gemini_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Pydantic JSON Schema -> Gemini ``responseSchema``."""

    def prune(node: Any) -> Any:
        if isinstance(node, list):
            return [prune(item) for item in node]
        if not isinstance(node, dict):
            return node
        node = _collapse_nullable(dict(node))
        if "const" in node and "enum" not in node:
            node["enum"] = [node["const"]]
            node.setdefault("type", "string")
        kept: dict[str, Any] = {}
        for key, value in node.items():
            if key not in _GEMINI_KEYS:
                continue
            if key == "properties" and isinstance(value, dict):
                # A map of field name -> schema. Its keys are the contract's field names,
                # not schema keywords: filtering them against _GEMINI_KEYS deletes the
                # entire contract and leaves a schema the model can satisfy with `{}`.
                kept[key] = {name: prune(sub) for name, sub in value.items()}
            elif key == "pattern" and isinstance(value, str):
                kept[key] = normalize_pattern(value)
            elif key in {"enum", "required"}:
                kept[key] = list(value) if isinstance(value, list) else value
            else:
                kept[key] = prune(value)
        # `required` must not name a property that pruning removed.
        props = kept.get("properties")
        if isinstance(props, dict) and isinstance(kept.get("required"), list):
            kept["required"] = [r for r in kept["required"] if r in props]
        return kept

    pruned = prune(inline_refs(schema))
    if not isinstance(pruned, dict):  # pragma: no cover - prune preserves the root mapping
        msg = "pruned schema root is not an object"
        raise SchemaTranslationError(msg)
    return pruned
