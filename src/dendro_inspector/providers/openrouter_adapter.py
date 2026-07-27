"""OpenRouter — an OpenAI-compatible host.

The default is a free vision model: OpenRouter publishes `input_modalities` per model, and
the popular reasoning models (`openai/gpt-oss-120b`, the `nemotron-3` text line) report
`['text']`. They cannot serve any role here, because every node call carries the photograph.
"""

from __future__ import annotations

from dendro_inspector.providers.openai_compatible import OpenAICompatibleProvider


class OpenRouterProvider(OpenAICompatibleProvider):
    adapter_name = "openrouter"
    default_model = "google/gemma-4-31b-it:free"
    default_base_url = "https://openrouter.ai/api/v1"
    key_env = "OPENROUTER_API_KEY"
    base_url_env = "OPENROUTER_BASE_URL"
