"""OpenRouter — an OpenAI-compatible host.

The default is a free vision model: OpenRouter publishes `input_modalities` per model, and
the popular reasoning models (`openai/gpt-oss-120b`, the `nemotron-3` text line) report
`['text']`. They cannot serve any role here, because every node call carries the photograph.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Any

from dendro_inspector.providers.base import ProviderUnavailableError
from dendro_inspector.providers.openai_compatible import OpenAICompatibleProvider


class OpenRouterProvider(OpenAICompatibleProvider):
    adapter_name = "openrouter"
    default_model = "google/gemma-4-31b-it:free"
    default_base_url = "https://openrouter.ai/api/v1"
    key_env = "OPENROUTER_API_KEY"
    base_url_env = "OPENROUTER_BASE_URL"

    def _structured_request(
        self,
        *,
        prompt: str,
        schema: Mapping[str, Any],
        model_name: str,
    ) -> tuple[str, Mapping[str, Any]]:
        if self.model != self.default_model:
            return super()._structured_request(
                prompt=prompt,
                schema=schema,
                model_name=model_name,
            )
        schema_text = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
        prompted = (
            f"{prompt}\n\n---\n\n## Required output\n\n"
            "Return one JSON object conforming to the following schema. "
            f"No prose and no code fence.\n\n{schema_text}"
        )
        return prompted, {"type": "json_object"}

    def _provider_preferences(self) -> Mapping[str, Any]:
        data_collection = os.environ.get("OPENROUTER_DATA_COLLECTION", "deny").lower().strip()
        if data_collection not in {"allow", "deny"}:
            msg = "OPENROUTER_DATA_COLLECTION must be 'allow' or 'deny'"
            raise ProviderUnavailableError(msg)
        return {
            "data_collection": data_collection,
            "require_parameters": True,
        }
