"""NVIDIA NIM — an OpenAI-compatible host.

Only the four host-specific values live here; the dialect is in
:mod:`dendro_inspector.providers.openai_compatible`.
"""

from __future__ import annotations

from dendro_inspector.providers.openai_compatible import OpenAICompatibleProvider


class NvidiaProvider(OpenAICompatibleProvider):
    adapter_name = "nvidia"
    # The `omni` member of the nemotron-3 family is the multimodal one; its text-only
    # siblings (`nemotron-3-super-120b-a12b`, `nemotron-3-nano-30b-a3b`) cannot serve any
    # role here. Measured on one photograph: 3.9s to a clean schema-conforming answer, where
    # `google/gemma-4-31b-it` and `meta/llama-3.2-90b-vision-instruct` both exceeded 300s on
    # the shared endpoint.
    default_model = "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"
    default_base_url = "https://integrate.api.nvidia.com/v1"
    key_env = "NVIDIA_API_KEY"
    base_url_env = "NVIDIA_BASE_URL"
