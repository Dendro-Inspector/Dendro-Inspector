"""Arm M of the v1 held-out protocol: the model alone, without the graph.

One structured call per unit: the domain prompt, every photograph of the unit, and the
request below, which asks for a genus or an explicit "cannot be determined", a family and a
confidence. It goes through the same provider layer, adapters and image preparation as the
graph (``request_structured``), so the arms differ in the graph and nothing else: same model,
same photographs, same ``DENDRO_IMAGE_MAX_EDGE_PX``, same transport.

The request text and the answer schema live here, and every record carries their SHA-256,
so the protocol's sign-off can name exactly what arm M asked
(docs/specs/v1-heldout-protocol.md). Records have the layout of a stored case
(``result.response.decisions``, ``result.trace.events[].provider_calls``), so the results
database and the scorers read them like any other run; ``result.model_alone`` keeps the
answer as the model gave it.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

from pydantic import Field

from dendro_inspector.evaluation.label_scoring import (
    Grade,
    ReferenceLabel,
    SubjectAnswer,
    grade_record,
)
from dendro_inspector.knowledge.loader import KnowledgeBase
from dendro_inspector.providers.base import (
    USAGE_SINK,
    ImageInput,
    ModelProvider,
    UsageSink,
    request_structured,
)
from dendro_inspector.schemas.base import Contract
from dendro_inspector.schemas.taxon import Resolution

NODE = "model_alone"
PROMPT_SEPARATOR = "\n\n---\n\n"

#: What arm M asks, after the domain prompt. Changing a word changes REQUEST_SHA256, which
#: the sign-off table records.
REQUEST = """## Task: identify from the photographs alone

You are shown every photograph of one inspection, each introduced by its image ID. Identify
what they show using the instructions above and what is visible in the photographs. No other
tool, card or reviewer is involved: this answer is final.

Report one subject per distinct tree, log lot or wood sample the photographs show. Usually
there is one. Report several only when the photographs plainly show different plants or lots.

For each subject:
- `genus`: the Latin genus you would name, or null when the genus cannot be determined from
  these photographs. Prefer null to a guess: a wrong genus is worse than none.
- `species`: the Latin species epithet only when the photographs establish it, else null.
- `family`: the Latin family, or null when you cannot establish it either.
- `confidence`: 0-100, how likely the genus you named is right; when `genus` is null, how
  sure you are that it cannot be determined from these photographs.
- `cannot_determine_reason`: when `genus` is null, the missing evidence in one sentence;
  otherwise null.
- `evidence`: the visible features you relied on, each with the image IDs that show it.
"""

#: Said when the uploader stated that every photograph was taken at one tree, which the
#: graph applies in code (``field_context``).
FIELD_CONTEXT = (
    "The uploader states that all of these photographs were taken on site and show the same "
    "tree, or the same lot of logs or firewood, from several angles."
)

_LATIN = r"^[A-Za-z][a-z]+$"


class Evidence(Contract):
    feature: str = Field(min_length=1, max_length=300)
    image_ids: tuple[str, ...] = Field(default=(), max_length=20)


class Subject(Contract):
    subject: str = Field(min_length=1, max_length=200, description="What this entry is about.")
    genus: str | None = Field(
        pattern=_LATIN, description="Latin genus, or null when it cannot be determined."
    )
    species: str | None = Field(default=None, max_length=60)
    family: str | None = Field(pattern=_LATIN)
    confidence: int = Field(ge=0, le=100)
    cannot_determine_reason: str | None = Field(default=None, max_length=400)
    evidence: tuple[Evidence, ...] = Field(default=(), max_length=12)


class ModelAloneAnswer(Contract):
    subjects: tuple[Subject, ...] = Field(min_length=1, max_length=6)


#: The schema the provider is held to; its hash goes into every record.
SCHEMA_SHA256 = hashlib.sha256(
    json.dumps(ModelAloneAnswer.model_json_schema(), sort_keys=True).encode()
).hexdigest()
REQUEST_SHA256 = hashlib.sha256(REQUEST.encode()).hexdigest()


def compose_prompt(domain_prompt: str, *, field_context: bool) -> str:
    """The domain prompt as the outer frame, as the graph sends it, then the request."""
    parts = [domain_prompt, REQUEST]
    if field_context:
        parts.append(FIELD_CONTEXT)
    return PROMPT_SEPARATOR.join(parts)


def _band(confidence: int) -> str:
    """The graph's confidence words, from the 0-100 scale of the domain prompt."""
    return "high" if confidence >= 85 else "medium" if confidence >= 70 else "low"


def decisions(answer: ModelAloneAnswer) -> list[dict[str, Any]]:
    """The answer as stored-case decisions, so every reader of runs can read it."""
    found = []
    for number, subject in enumerate(answer.subjects, start=1):
        if subject.genus and subject.species:
            taxon, resolution = f"{subject.genus.lower()}_{subject.species.lower()}", "species"
        elif subject.genus:
            taxon, resolution = subject.genus.lower(), "genus"
        elif subject.family:
            taxon, resolution = subject.family.lower(), "family"
        else:
            taxon, resolution = None, "unknown"
        found.append(
            {
                "subject_id": f"subject-{number}",
                "subject": subject.subject,
                "selected_taxon": taxon,
                "resolution": resolution,
                "confidence": _band(subject.confidence),
                "confidence_score": subject.confidence,
                "status": "abstained" if taxon is None else "model_alone",
                "abstained": taxon is None,
            }
        )
    return found


async def ask(
    provider: ModelProvider,
    *,
    domain_prompt: str,
    images: Sequence[ImageInput],
    field_context: bool,
    max_retries: int = 2,
) -> tuple[ModelAloneAnswer, UsageSink, float]:
    """One arm M call: the answer, its token accounting and its wall time in seconds."""
    usage = UsageSink()
    started = time.monotonic()
    answer = await request_structured(
        provider=provider,
        role="primary",
        node=NODE,
        prompt=compose_prompt(domain_prompt, field_context=field_context),
        images=images,
        response_model=ModelAloneAnswer,
        metadata={USAGE_SINK: usage},
        max_retries=max_retries,
    )
    return answer, usage, round(time.monotonic() - started, 1)


def record(
    *,
    answer: ModelAloneAnswer | None,
    usage: UsageSink | None,
    wall_seconds: float,
    adapter: str,
    model: str | None,
    unit_id: str,
    photos: Sequence[Mapping[str, Any]],
    field_context: bool,
    domain_prompt_sha256: str,
    image_max_edge_px: int | None,
    code_commit_sha: str | None,
    started_at: str,
    error: str | None = None,
) -> dict[str, Any]:
    """A stored-case record for one unit; ``error`` makes it a failed case."""
    call = {
        "role": "primary",
        "node": NODE,
        "model": model,
        "adapter": adapter,
        "duration_ms": int(wall_seconds * 1000),
        **(
            {
                "input_tokens": usage.input_tokens,
                "cached_input_tokens": usage.cached_input_tokens,
                "output_tokens": usage.output_tokens,
                "reasoning_output_tokens": usage.reasoning_output_tokens,
                "reported_cost_usd": usage.reported_cost_usd,
            }
            if usage is not None
            else {}
        ),
    }
    trace = {
        "case_id": unit_id,
        "code_commit_sha": code_commit_sha,
        "started_at": started_at,
        "duration_ms": int(wall_seconds * 1000),
        "events": [{"node": NODE, "provider_calls": [call]}],
    }
    arm = {
        "request_sha256": REQUEST_SHA256,
        "schema_sha256": SCHEMA_SHA256,
        "domain_prompt_sha256": domain_prompt_sha256,
        "image_max_edge_px": image_max_edge_px,
        "field_context": field_context,
        "photos": list(photos),
        "answer": answer.model_dump(mode="json") if answer is not None else None,
    }
    result: dict[str, Any] = {"trace": trace, "model_alone": arm}
    if answer is not None:
        result["response"] = {"decisions": decisions(answer), "target_subject_ids": []}
    return {
        "unit": unit_id,
        "arm": "model_alone",
        "models": {"primary": f"{adapter}:{model}"},
        "started_at": started_at,
        "wall_seconds": wall_seconds,
        "exit_code": 0 if answer is not None else 1,
        **({"error": error} if error else {}),
        "result" if answer is not None else "failure": result,
    }


def subject_answers(stored: Mapping[str, Any]) -> tuple[SubjectAnswer, ...]:
    """What an arm M record committed to, per subject, as the graders read answers."""
    answer = ((stored.get("result") or {}).get("model_alone") or {}).get("answer") or {}
    found = []
    for number, subject in enumerate(answer.get("subjects") or (), start=1):
        genus = (subject.get("genus") or "").lower()
        family = (subject.get("family") or "").lower()
        found.append(
            SubjectAnswer(
                f"subject-{number}",
                genera=(genus,) if genus else (),
                broader=(family,) if family and not genus else (),
            )
        )
    return tuple(found)


def reference_with_family(knowledge: KnowledgeBase, label: ReferenceLabel) -> ReferenceLabel:
    """The label with its family filled in from the genus card, as the coverage audit does."""
    if label.family is not None or label.genus is None:
        return label
    card = knowledge.try_taxon(label.genus)
    family = card.identity_at_or_broader(Resolution.FAMILY) if card is not None else None
    if family is None or family.resolution is not Resolution.FAMILY:
        return label
    return replace(label, family=family.taxon_id)


def grade(stored: Mapping[str, Any], label: ReferenceLabel, knowledge: KnowledgeBase) -> Grade:
    """Grade an arm M record against a label with the graph's own scorer."""
    return grade_record(subject_answers(stored), reference_with_family(knowledge, label))
