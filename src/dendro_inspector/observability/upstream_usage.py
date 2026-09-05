"""Normalize measured token counters reported by local provider workers.

Worker metadata preserves each upstream's native field names for provenance. Consumers use
this module as the one mapping layer from those raw shapes to the trace vocabulary.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def numeric(value: Any) -> int | float | None:
    """Return a real numeric value, excluding booleans and numeric-looking strings."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return value


def _usage_total(payload: Any, aliases: tuple[str, ...]) -> int | None:
    """Read one token counter from a flat usage object or per-model child objects."""
    if not isinstance(payload, Mapping):
        return None
    for alias in aliases:
        direct = numeric(payload.get(alias))
        if direct is not None:
            return int(direct)
    children = [
        _usage_total(child, aliases) for child in payload.values() if isinstance(child, Mapping)
    ]
    present = [value for value in children if value is not None]
    return sum(present) if present else None


def upstream_usage(upstream: Mapping[str, Any]) -> dict[str, int] | None:
    """Project a worker's native usage payload into the canonical trace counters."""
    raw = upstream.get("usage") or upstream.get("model_usage") or upstream.get("tokens")
    if not isinstance(raw, Mapping):
        return None
    values = {
        "input_tokens": _usage_total(raw, ("input_tokens", "inputTokens", "prompt_tokens")),
        "cached_input_tokens": _usage_total(
            raw,
            (
                "cached_input_tokens",
                "cachedInputTokens",
                "cached_tokens",
                "cache_read_input_tokens",
                "cacheReadInputTokens",
            ),
        ),
        "cache_write_input_tokens": _usage_total(
            raw, ("cache_write_input_tokens", "cacheWriteInputTokens")
        ),
        "output_tokens": _usage_total(raw, ("output_tokens", "outputTokens", "completion_tokens")),
        "reasoning_output_tokens": _usage_total(
            raw,
            ("reasoning_output_tokens", "reasoningOutputTokens", "reasoning_tokens"),
        ),
        "total_tokens": _usage_total(raw, ("total_tokens", "totalTokens")),
    }
    present = {key: value for key, value in values.items() if value is not None}
    return present or None
