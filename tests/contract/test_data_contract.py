"""Every shipped data file must validate against the contract it claims to satisfy.

A malformed knowledge card or evaluation case should fail here, in a fast offline test,
rather than three nodes deep in a run.
"""

from __future__ import annotations

import json

import pytest
import yaml

from dendro_inspector.config import Role
from dendro_inspector.evaluation.runner import load_cases
from dendro_inspector.knowledge.taxon_cards import (
    card_value_vocabulary,
    unreachable_selectors,
)
from dendro_inspector.schemas.taxon import (
    ComparisonCard,
    Provenance,
    RegionalPack,
    Resolution,
    SourceType,
    TaxonCard,
    resolution_rank,
)

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

    def test_taxon_cards_declare_coherent_native_and_broader_identities(self, repo_root):
        for path in _yaml_files(repo_root, "taxa"):
            card = TaxonCard.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
            assert card.native_identity.taxon_id == card.taxon_id
            assert card.native_identity.display_name == card.display_name
            assert card.native_identity.resolution is card.native_resolution

            resolutions = [identity.resolution for identity in card.broader_identities]
            assert len(resolutions) == len(set(resolutions))
            assert all(
                resolution_rank(identity.resolution) < resolution_rank(card.native_resolution)
                for identity in card.broader_identities
            )
            assert any(
                identity.resolution is Resolution.FAMILY for identity in card.broader_identities
            )
            if card.native_resolution is Resolution.SPECIES:
                assert any(
                    identity.resolution is Resolution.GENUS for identity in card.broader_identities
                )

    def test_every_taxon_identity_carries_provenance(self, repo_root):
        """A broadened answer names a taxon, so something must be answerable for it.

        The card-level block covers the feature rules. An identity either declares its own
        source or inherits the card's, and inheriting is only honest when the card's source
        actually names it.
        """
        for path in _yaml_files(repo_root, "taxa"):
            card = TaxonCard.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
            for identity in (card.native_identity, *card.broader_identities):
                assert identity.provenance is not None, (
                    f"{card.taxon_id}: identity {identity.taxon_id} carries no provenance"
                )

    def test_no_family_identity_claims_the_domain_prompt(self, repo_root):
        """The domain prompt names genera and species. It names no family at all.

        Section 14 is the stated source for these cards, so a family placement inherited
        from it would be a citation to text that does not exist.
        """
        for path in _yaml_files(repo_root, "taxa"):
            card = TaxonCard.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
            for identity in card.broader_identities:
                if identity.resolution is not Resolution.FAMILY:
                    continue
                provenance = identity.provenance
                assert provenance is not None
                assert provenance.source_type is not SourceType.DOMAIN_PROMPT, (
                    f"{card.taxon_id}: family {identity.taxon_id} claims the domain prompt"
                )

    def test_repeated_broader_identities_share_one_provenance(self, repo_root):
        """The same family declared by five cards must not acquire five stories."""
        seen: dict[tuple[Resolution, str], Provenance] = {}
        for path in _yaml_files(repo_root, "taxa"):
            card = TaxonCard.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
            for identity in card.broader_identities:
                assert identity.provenance is not None
                key = (identity.resolution, identity.taxon_id)
                first = seen.setdefault(key, identity.provenance)
                assert first == identity.provenance, (
                    f"{card.taxon_id}: provenance for {identity.taxon_id} disagrees with an "
                    f"earlier card"
                )

    def test_repeated_genus_and_family_identities_are_consistent(self, repo_root):
        identities: dict[tuple[Resolution, str], str] = {}
        taxon_resolutions: dict[str, Resolution] = {}
        for path in _yaml_files(repo_root, "taxa"):
            card = TaxonCard.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
            for identity in (card.native_identity, *card.broader_identities):
                key = (identity.resolution, identity.taxon_id)
                previous_name = identities.setdefault(key, identity.display_name)
                assert previous_name == identity.display_name

                previous_resolution = taxon_resolutions.setdefault(
                    identity.taxon_id, identity.resolution
                )
                assert previous_resolution is identity.resolution

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

    def test_every_requirement_selector_can_be_satisfied(self, repo_root):
        """A high-confidence requirement no observation could ever satisfy is a dead rule.

        The grammar is `_and_` inside `_or_` over canonical feature paths, and nothing
        rewrites underscores into dots — so `bark_pattern` is not a spelling of
        `bark.pattern`, it is a feature that does not exist. Betula shipped exactly that
        for weeks: every decision quoted `bark.pattern` as support and reported the bark
        requirement as missing in the same breath, because only the disjunction's `leaf`
        limb was reachable. A live limb hides a dead one perfectly, which is why this
        checks every selector rather than every requirement.

        The vocabulary is deliberately the union across all cards, not each card's own
        features: requirements are matched against the evidence packet, and the packet can
        carry any feature the knowledge base declares anywhere.
        """
        cards = [
            TaxonCard.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
            for path in _yaml_files(repo_root, "taxa")
        ]
        features = card_value_vocabulary(cards)
        dead = {
            card.taxon_id: unreachable_selectors(card, features)
            for card in cards
            if unreachable_selectors(card, features)
        }
        assert not dead, f"requirement selectors that no feature can ever match: {dead}"

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
        valid_roles = {role.value for role in Role}
        for path in sorted((repo_root / "evals" / "fixtures").glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            for key in payload["responses"]:
                role, _, node = key.partition(":")
                assert role in valid_roles, f"{path.name}: bad role in {key!r}"
                assert node, f"{path.name}: missing node in {key!r}"

    def test_fixtures_contain_no_real_email_addresses(self, repo_root):
        """Public repository: fixtures carry synthetic data only."""
        for path in sorted((repo_root / "evals" / "fixtures").glob("*.json")):
            text = path.read_text(encoding="utf-8")
            assert "@" not in text or "@example.com" in text
