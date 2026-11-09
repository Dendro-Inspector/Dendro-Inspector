"""Request tagging is sent only to the OpenAI platform: Azure OpenAI / Foundry reject it."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from dendro_inspector.providers.openai_adapter import _accepts_metadata


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("api.openai.com", True),
        ("example-resource.openai.azure.com", False),
        ("example.services.ai.azure.com", False),
        (None, False),
    ],
)
def test_metadata_only_for_openai_platform(host: str | None, expected: bool) -> None:
    client = SimpleNamespace(base_url=SimpleNamespace(host=host))
    assert _accepts_metadata(client) is expected


def test_client_without_base_url_gets_no_metadata() -> None:
    assert _accepts_metadata(object()) is False
