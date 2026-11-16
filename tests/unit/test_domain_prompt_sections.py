"""Per-node domain prompt sections: an experiment, off by default.

The prompt is cut at its own fences and never rewritten. These tests hold that the default
sends every node the whole prompt, that a node's sections are the prompt's exact bytes in
order, and that a prompt whose sections cannot be read fails closed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dendro_inspector.config import DomainSections, PromptConfig, load_config
from dendro_inspector.prompts import sections
from dendro_inspector.prompts.library import PromptLibrary, PromptPolicyError

ROOT = Path(__file__).resolve().parents[2]
PROMPTS = ("prompts/domain/system-prompt.en.md", "prompts/domain/system-prompt.md")
FENCE = "━" * 34


def _library(mode: DomainSections) -> PromptLibrary:
    return PromptLibrary(PromptConfig(domain_sections=mode), root=ROOT)


def _text(path: str) -> str:
    return (ROOT / path).read_bytes().decode("utf-8")


@pytest.mark.parametrize("path", PROMPTS)
class TestTheSectionsAreThePromptsOwn:
    def test_both_locales_read_as_the_same_nineteen_sections(self, path):
        parts = sections.split_sections(_text(path))
        assert [part.number for part in parts] == list(range(sections.LAST_SECTION + 1))

    def test_the_sections_join_back_to_the_exact_prompt(self, path):
        text = _text(path)
        assert "".join(part.text for part in sections.split_sections(text)) == text

    def test_a_node_is_sent_its_sections_byte_for_byte_and_in_order(self, path):
        text = _text(path)
        by_number = {part.number: part.text for part in sections.split_sections(text)}
        for node, chosen in sections.NODE_SECTIONS.items():
            projected = sections.project(text, node)
            assert projected == "".join(by_number[number] for number in sorted(chosen)), node
            assert projected.startswith(sections.shared_prefix(text))


class TestWhatEachNodeIsSent:
    def test_no_model_node_is_sent_the_sections_code_implements(self):
        for node, chosen in sections.NODE_SECTIONS.items():
            assert not chosen & {4, 5, 7, 8, 9, 10, 11, 12}, node

    def test_every_node_keeps_the_evidence_hierarchy_and_the_confidence_scale(self):
        for node, chosen in sections.NODE_SECTIONS.items():
            assert chosen >= sections.SHARED_SECTIONS, node

    def test_the_nodes_that_name_a_tree_keep_the_base_species(self):
        for node in ("evidence_extractor", "candidate_generator", "arbiter"):
            assert 14 in sections.NODE_SECTIONS[node]

    def test_an_unlisted_node_is_sent_the_whole_prompt(self):
        text = _text(PROMPTS[0])
        assert sections.sections_for("response_composer") is None
        assert sections.project(text, "response_composer") == text


class TestAPromptThatCannotBeReadFailsClosed:
    def test_a_missing_section(self):
        text = _text(PROMPTS[0])
        broken = text.replace(f"{FENCE}\n17. SPECIAL EXAMPLES\n{FENCE}\n", "")
        assert broken != text
        with pytest.raises(sections.DomainSectionError, match="expected sections"):
            sections.split_sections(broken)

    def test_an_unnumbered_heading_after_the_first(self):
        text = f"pre\n{FENCE}\nCORE\n{FENCE}\nbody\n{FENCE}\nUNNUMBERED\n{FENCE}\nmore\n"
        with pytest.raises(sections.DomainSectionError, match="unnumbered"):
            sections.split_sections(text)

    def test_the_library_raises_its_policy_error(self, monkeypatch):
        library = _library(DomainSections.PER_NODE)
        library.validate_policy()

        def unreadable(*_: object) -> str:
            raise sections.DomainSectionError("no fences")

        monkeypatch.setattr(sections, "project", unreadable)
        with pytest.raises(PromptPolicyError, match="per-node"):
            library.compose("planner")


class TestTheLibrary:
    def test_the_default_sends_every_node_the_whole_prompt(self):
        library = _library(DomainSections.FULL)
        whole = library.domain.text
        for node in sections.NODE_SECTIONS:
            assert library.compose(node).startswith(whole)

    def test_per_node_sends_each_node_its_projection(self):
        library = _library(DomainSections.PER_NODE)
        whole = library.domain.text
        for node in sections.NODE_SECTIONS:
            composed = library.compose(node)
            assert composed.startswith(sections.project(whole, node))
            assert library.node(node) in composed

    def test_per_node_reports_only_the_shared_sections_as_cacheable(self):
        library = _library(DomainSections.PER_NODE)
        prefix = library.cacheable_prefix_chars()
        assert prefix == len(sections.shared_prefix(library.domain.text))
        heads = {library.compose(node)[:prefix] for node in sections.NODE_SECTIONS}
        assert len(heads) == 1

    def test_the_default_cache_boundary_is_unchanged(self):
        full, per_node = _library(DomainSections.FULL), _library(DomainSections.PER_NODE)
        assert full.cacheable_prefix_chars() > len(full.domain.text)
        assert per_node.cacheable_prefix_chars() < len(per_node.domain.text)

    def test_the_trace_records_which_mode_was_used(self):
        assert _library(DomainSections.FULL).metadata().domain_sections == "full"
        assert _library(DomainSections.PER_NODE).metadata().domain_sections == "per_node"

    def test_the_sealed_hash_is_the_whole_files_either_way(self):
        full, per_node = _library(DomainSections.FULL), _library(DomainSections.PER_NODE)
        assert full.metadata().sha256 == per_node.metadata().sha256


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, DomainSections.FULL),
        ("per_node", DomainSections.PER_NODE),
        ("FULL", DomainSections.FULL),
    ],
)
def test_the_environment_selects_the_mode(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("DENDRO_DOMAIN_PROMPT_SECTIONS", raising=False)
    else:
        monkeypatch.setenv("DENDRO_DOMAIN_PROMPT_SECTIONS", value)
    assert load_config().prompts.domain_sections is expected


def test_an_unknown_mode_fails_at_load(monkeypatch):
    monkeypatch.setenv("DENDRO_DOMAIN_PROMPT_SECTIONS", "summary")
    with pytest.raises(ValueError, match="summary"):
        load_config()
