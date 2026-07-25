"""Deterministic fake provider.

Every unit, contract, integration and evaluation test in this repository runs on this
adapter. No network, no credentials, no cost, no flake. A scenario file scripts one
response per ``role:node`` key; an unscripted key is a loud failure rather than an
improvised answer, because a fake that quietly invents data is worse than no fake at all.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from evil_duck_dendro.providers.base import (
    ImageInput,
    ProviderError,
    ResponseT,
    StructuredOutputError,
)


class ScenarioNotFoundError(ProviderError):
    """The requested scenario file does not exist."""


class UnscriptedCallError(ProviderError):
    """The graph asked for a response the scenario does not script."""


class FakeModelProvider:
    """Replays a scenario script. Deterministic by construction."""

    adapter_name = "fake"

    def __init__(
        self,
        *,
        scenario: str,
        fixtures_root: Path,
        model: str | None = None,
    ) -> None:
        self.model = model or f"fake:{scenario}"
        self.scenario = scenario
        self._path = fixtures_root / f"{scenario}.json"
        if not self._path.is_file():
            msg = (
                f"fake scenario {scenario!r} not found at {self._path}. "
                f"Available: {sorted(p.stem for p in fixtures_root.glob('*.json'))}"
            )
            raise ScenarioNotFoundError(msg)
        payload = json.loads(self._path.read_text(encoding="utf-8"))
        self._responses: dict[str, Any] = payload.get("responses", {})
        # Keys listed here fail validation on their first call and succeed on the retry,
        # which is how the malformed-output repair path is exercised without a live model.
        self._malformed_once: set[str] = set(payload.get("malformed_once", []))
        self._seen: set[str] = set()
        self.calls: list[str] = []

    @property
    def path(self) -> Path:
        return self._path

    async def generate_structured(
        self,
        *,
        role: str,
        prompt: str,
        images: Sequence[ImageInput],
        response_model: type[ResponseT],
        metadata: Mapping[str, Any],
    ) -> ResponseT:
        del prompt, images  # deliberately unused: the fake replays, it does not reason
        node = str(metadata.get("node", "unknown"))
        key = f"{role}:{node}"
        self.calls.append(key)

        if key in self._malformed_once and key not in self._seen:
            self._seen.add(key)
            msg = f"scripted malformed output for {key}"
            raise StructuredOutputError(msg)

        if key not in self._responses:
            msg = (
                f"scenario {self.scenario!r} has no scripted response for {key!r}. "
                f"Scripted keys: {sorted(self._responses)}"
            )
            raise UnscriptedCallError(msg)

        try:
            return response_model.model_validate(self._responses[key])
        except ValidationError as exc:
            msg = (
                f"scenario {self.scenario!r} response for {key!r} does not satisfy "
                f"{response_model.__name__}: {exc}"
            )
            raise StructuredOutputError(msg) from exc


class ScriptedProvider:
    """In-memory fake for unit tests that do not want a fixture file on disk."""

    adapter_name = "scripted"

    def __init__(self, responses: Mapping[str, BaseModel]) -> None:
        self.model = "scripted"
        self._responses = dict(responses)
        self.calls: list[str] = []

    async def generate_structured(
        self,
        *,
        role: str,
        prompt: str,
        images: Sequence[ImageInput],
        response_model: type[ResponseT],
        metadata: Mapping[str, Any],
    ) -> ResponseT:
        del prompt, images
        key = f"{role}:{metadata.get('node', 'unknown')}"
        self.calls.append(key)
        if key not in self._responses:
            msg = f"no scripted response for {key!r}"
            raise UnscriptedCallError(msg)
        value = self._responses[key]
        if not isinstance(value, response_model):
            msg = (
                f"scripted response for {key!r} is {type(value).__name__}, "
                f"expected {response_model.__name__}"
            )
            raise StructuredOutputError(msg)
        return value
