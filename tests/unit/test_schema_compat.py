"""Schema translation for provider dialects.

The contract these tests defend: translation may drop what a dialect cannot express, and
may never drop the contract itself. A schema that lost its properties still validates every
response the model returns, because every field has a default — so the failure is silent and
arrives as an empty result rather than an error.
"""

from __future__ import annotations

import re

import pytest
from pydantic import BaseModel, Field

from dendro_inspector.providers.schema_compat import (
    SchemaTranslationError,
    inline_refs,
    normalize_pattern,
    to_gemini_schema,
    to_ollama_schema,
    to_strict_openai_schema,
)
from dendro_inspector.schemas.base import (
    FEATURE_PATH_PATTERN,
    IDENTIFIER_PATTERN,
    VALUE_TOKEN_PATTERN,
    Identifier,
    ValueToken,
)
from dendro_inspector.schemas.evidence import GeneratedEvidencePacket


class _Child(BaseModel):
    token: ValueToken
    count: int = 0


class _Parent(BaseModel):
    child: _Child
    name: Identifier
    note: str | None = None
    tags: list[Identifier] = Field(default_factory=list)


class TestNormalizePattern:
    """The rewrite must preserve the language, not merely please the backend."""

    @pytest.mark.parametrize(
        ("original", "expected"),
        [
            (r"^[a-z0-9][a-z0-9_\-]*$", "^[a-z0-9][a-z0-9_-]*$"),
            (r"^[a-z0-9][a-z0-9_.\-]*$", "^[a-z0-9][a-z0-9_.-]*$"),
            # A dot outside a class is a literal dot and must stay escaped.
            (
                r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$",
                r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$",
            ),
            # Unescaping in place would turn this into the range a-z.
            (r"[a\-z]", "[az-]"),
            ("[a-z]", "[a-z]"),
            (r"[\-abc]", "[abc-]"),
        ],
    )
    def test_it_rewrites_only_what_it_must(self, original, expected):
        assert normalize_pattern(original) == expected

    @pytest.mark.parametrize(
        "pattern",
        [IDENTIFIER_PATTERN, VALUE_TOKEN_PATTERN, FEATURE_PATH_PATTERN, r"[a\-z]", r"[^\-a]"],
    )
    def test_the_rewritten_pattern_matches_the_same_strings(self, pattern):
        rewritten = normalize_pattern(pattern)
        samples = [
            "abc",
            "a-b",
            "a_b",
            "a.b",
            "bark.peeling",
            "A1",
            "f1",
            "a--b",
            "-x",
            "x.",
            "1a",
            "z",
            "az",
        ]
        for sample in samples:
            assert bool(re.match(pattern, sample)) == bool(re.match(rewritten, sample)), sample


class TestInlineRefs:
    def test_it_resolves_refs_and_drops_the_defs_table(self):
        inlined = inline_refs(_Parent.model_json_schema())
        assert "$defs" not in inlined
        assert inlined["properties"]["child"]["properties"]["token"]["type"] == "string"

    def test_an_unresolvable_ref_is_an_error_not_a_silent_drop(self):
        with pytest.raises(SchemaTranslationError, match=r"\$ref"):
            inline_refs({"type": "object", "properties": {"a": {"$ref": "#/$defs/Missing"}}})

    def test_a_field_named_ref_is_data_not_a_pointer(self):
        schema = {"type": "object", "properties": {"$ref": {"type": "string"}}}
        assert inline_refs(schema)["properties"] == {"$ref": {"type": "string"}}


class TestGeminiSchema:
    def test_the_contract_survives_translation(self):
        """The regression that matters: pruning keyed on schema keywords deleted every field.

        `properties` maps field names to schemas. Filtering those names against the allowed
        keyword set leaves `{}` — which Gemini happily satisfies with an empty object, and
        which validates, because the packet's fields all have defaults.
        """
        original = GeneratedEvidencePacket.model_json_schema()
        translated = to_gemini_schema(original)
        assert set(translated["properties"]) == set(original["properties"])
        observation = translated["properties"]["observations"]["items"]
        assert {"feature", "value", "subject_id"} <= set(observation["properties"])

    def test_unsupported_keywords_are_dropped(self):
        translated = to_gemini_schema(_Parent.model_json_schema())
        name = translated["properties"]["name"]
        assert "maxLength" not in name
        assert "additionalProperties" not in translated

    def test_patterns_are_kept_but_normalized(self):
        """Gemini honours a pattern only if its character classes avoid `\\-`."""
        name = to_gemini_schema(_Parent.model_json_schema())["properties"]["name"]
        assert name["pattern"] == "^[a-z0-9][a-z0-9_-]*$"

    def test_optional_becomes_nullable(self):
        note = to_gemini_schema(_Parent.model_json_schema())["properties"]["note"]
        assert note["nullable"] is True
        assert note["type"] == "string"
        assert "anyOf" not in note

    def test_enums_keep_their_members(self):
        subject = to_gemini_schema(GeneratedEvidencePacket.model_json_schema())["properties"][
            "subjects"
        ]["items"]
        assert "log" in subject["properties"]["kind"]["enum"]

    def test_required_never_names_a_pruned_property(self):
        translated = to_gemini_schema(_Parent.model_json_schema())
        assert set(translated.get("required", [])) <= set(translated["properties"])


class TestOllamaSchema:
    def test_patterns_are_dropped_because_gbnf_cannot_compile_them(self):
        """Measured: an escaped hyphen in a character class is a 400 from the server."""
        translated = to_ollama_schema(GeneratedEvidencePacket.model_json_schema())
        rendered = repr(translated)
        assert "pattern" not in rendered
        assert "\\-" not in rendered

    def test_everything_else_survives(self):
        original = GeneratedEvidencePacket.model_json_schema()
        translated = to_ollama_schema(original)
        assert set(translated["properties"]) == set(original["properties"])
        assert set(translated["$defs"]) == set(original["$defs"])

    def test_the_model_still_enforces_what_the_grammar_stopped_enforcing(self):
        """Dropping a pattern from the request must not widen the contract."""
        assert "pattern" not in repr(to_ollama_schema(_Parent.model_json_schema()))
        with pytest.raises(ValueError, match=r"should match pattern"):
            _Parent.model_validate(
                {"child": {"token": "ok"}, "name": "Not A Token", "tags": []},
            )


class TestStrictOpenAISchema:
    def test_every_object_requires_every_property(self):
        translated = to_strict_openai_schema(_Parent.model_json_schema())

        assert translated["required"] == ["child", "name", "note", "tags"]
        assert translated["additionalProperties"] is False
        child = translated["$defs"]["_Child"]
        assert child["required"] == ["token", "count"]
        assert child["additionalProperties"] is False

    def test_schema_defaults_are_transport_only_and_are_removed(self):
        translated = to_strict_openai_schema(_Parent.model_json_schema())

        assert "default" not in repr(translated)
        assert _Parent.model_validate({"child": {"token": "ok"}, "name": "name"}).tags == []

    def test_patterns_are_left_to_the_original_model_validation(self):
        translated = to_strict_openai_schema(_Parent.model_json_schema())

        assert "pattern" not in repr(translated)
        with pytest.raises(ValueError, match=r"should match pattern"):
            _Parent.model_validate(
                {"child": {"token": "ok"}, "name": "Not A Token", "tags": []},
            )
