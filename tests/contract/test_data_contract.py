"""Every shipped data file must validate against the contract it claims to satisfy.

A malformed knowledge card or evaluation case should fail here, in a fast offline test,
rather than three nodes deep in a run.
"""

from __future__ import annotations

import json

import pytest
import yaml

from evil_duck_dendro.evaluation.runner import load_cases
from evil_duck_dendro.schemas.taxon import ComparisonCard, RegionalPack, TaxonCard

pytestmark = pytest.mark.contract


def _yaml_files(root, subdir):
    return sorted((root / "knowledge" / subdir).glob("*.yaml"))


class TestKnowledgeCards:
    def test_every_taxon_card_validates(self, repo_root):
        paths = _yaml_files(repo_root, "taxa")
        assert paths
        for path in paths:
            card = TaxonCard.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
            assert card.taxon_id == path.stem

    def test_every_comparison_card_validates(self, repo_root):
        paths = _yaml_files(repo_root, "comparisons")
        assert paths
        for path in paths:
            card = ComparisonCard.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
            assert card.comparison_id == path.stem

    def test_every_regional_pack_validates(self, repo_root):
        paths = _yaml_files(repo_root, "regions")
        assert paths
        for path in paths:
            pack = RegionalPack.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
            assert pack.region_id == path.stem

    def test_cards_only_reference_taxa_that_exist(self, repo_root):
        known = {path.stem for path in _yaml_files(repo_root, "taxa")}
        for path in _yaml_files(repo_root, "comparisons"):
            card = ComparisonCard.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
            assert set(card.taxa) <= known
            for difference in card.decisive_differences:
                assert set(difference.separates) <= known

    def test_confusions_are_symmetric(self, repo_root):
        """If Pinus lists Picea as a look-alike, Picea must list Pinus."""
        cards = {
            path.stem: TaxonCard.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
            for path in _yaml_files(repo_root, "taxa")
        }
        for taxon_id, card in cards.items():
            for other in card.common_confusions:
                assert other in cards, f"{taxon_id} names unknown taxon {other}"
                assert taxon_id in cards[other].common_confusions

    def test_placeholder_content_is_declared_honestly(self, repo_root):
        """Nothing here has been dendrologist-reviewed, and every card must say so."""
        for path in _yaml_files(repo_root, "taxa"):
            card = TaxonCard.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
            assert card.placeholder_content is True


class TestEvaluationSuite:
    def test_public_cases_load_and_validate(self, repo_root):
        cases = load_cases(repo_root / "evals" / "public")
        assert len(cases) >= 5

    def test_case_ids_are_unique(self, repo_root):
        cases = load_cases(repo_root / "evals" / "public")
        ids = [case.case_id for case in cases]
        assert len(set(ids)) == len(ids)

    def test_every_case_scenario_file_exists(self, repo_root):
        for case in load_cases(repo_root / "evals" / "public"):
            path = repo_root / "evals" / "fixtures" / f"{case.scenario}.json"
            assert path.is_file(), f"{case.case_id} references missing scenario {case.scenario}"

    def test_every_case_declares_at_least_one_expectation(self, repo_root):
        """A case that asserts nothing is a case that cannot fail, which is worse than none."""
        for case in load_cases(repo_root / "evals" / "public"):
            declared = case.expect.model_dump(exclude_defaults=True)
            assert declared, f"{case.case_id} declares no expectations"


class TestFixtures:
    def test_every_fixture_is_valid_json_with_responses(self, repo_root):
        paths = sorted((repo_root / "evals" / "fixtures").glob("*.json"))
        assert paths
        for path in paths:
            payload = json.loads(path.read_text(encoding="utf-8"))
            assert "responses" in payload, f"{path.name} has no responses block"
            assert payload["responses"], f"{path.name} scripts nothing"

    def test_fixture_keys_are_role_scoped(self, repo_root):
        for path in sorted((repo_root / "evals" / "fixtures").glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            for key in payload["responses"]:
                role, _, node = key.partition(":")
                assert role in {"primary", "arbiter"}, f"{path.name}: bad role in {key!r}"
                assert node, f"{path.name}: missing node in {key!r}"

    def test_fixtures_contain_no_real_email_addresses(self, repo_root):
        """Public repository: fixtures carry synthetic data only."""
        for path in sorted((repo_root / "evals" / "fixtures").glob("*.json")):
            text = path.read_text(encoding="utf-8")
            assert "@" not in text or "@example.com" in text
