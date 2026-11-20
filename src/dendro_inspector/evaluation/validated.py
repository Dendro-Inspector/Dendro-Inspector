"""Arm V: the model alone, checked against its card on the graph's own evidence.

Arm M (:mod:`dendro_inspector.evaluation.model_alone`) asks the model once for a genus. Arm V
asks the same question and, in parallel, runs the graph's first stages exactly as an
inspection does: input guard, planner, evidence extractor, evidence quality, and nothing
after them. No candidate generation, review, arbitration, correction retry or graph fallback
runs. Code then decides, with no model call, whether the observations confirm the model's
genus:

* For every genus the model named and every card of that genus, ``match_card`` (the
  admission boundary the graph itself uses) matches each subject's evidence. The pairing is
  ``confirmed`` when a strong-positive feature of the card was observed with no
  contradicting or disqualifying observation, ``contradicted`` when such an observation
  exists, ``unsupported`` otherwise.
* A subject is named with a genus when exactly one of the model's genera is confirmed on it.
  The answer cites the observations that confirmed it (feature, value, image).
* Otherwise the subject abstains with a reason. Where nothing in the case was named, the
  model's first genus is kept as an *unconfirmed* hypothesis beside the abstention, with the
  follow-up photographs its cards list. A hypothesis is never an answer and is never graded.

``card_confirmed`` means the recorded observations match a feature the card calls strong for
that genus. It is not a botanical identification: a misreading the model makes both when
naming and when describing passes the check (see ``docs/evaluation.md``).

The decision reads the evidence packet as the trace records it, never live state, so a
stored record replays to the same decisions offline (:func:`replay`). Records have the
stored-case layout (``result.response.decisions``, ``result.trace``) and grade with the
graph's own scorer (:func:`grade`).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from enum import StrEnum
from typing import Any, Literal

from dendro_inspector.config import Role
from dendro_inspector.evaluation import model_alone
from dendro_inspector.evaluation.label_scoring import (
    Grade,
    ReferenceLabel,
    SubjectAnswer,
    grade_record,
)
from dendro_inspector.evaluation.trace_replay import packet_from_trace
from dendro_inspector.graph.definition import NodeName
from dendro_inspector.graph.executor import GraphRunResult, NodeContext, partial_trace_of, run_graph
from dendro_inspector.knowledge.loader import KnowledgeBase
from dendro_inspector.knowledge.taxon_cards import match_card
from dendro_inspector.nodes import build_registry
from dendro_inspector.providers.base import UsageSink
from dendro_inspector.schemas.base import Contract
from dendro_inspector.schemas.evidence import EvidencePacket, Observation
from dendro_inspector.schemas.input import CaseInput
from dendro_inspector.schemas.taxon import Resolution

ARM = "validated"
#: Bumped whenever a rule in :func:`decide` changes; every record names the version it used.
RULES_VERSION = 1
#: Arm V runs the graph up to, not including, these nodes: candidate generation, or the
#: photo planner the graph turns to when evidence quality is insufficient.
STOP_BEFORE = frozenset({NodeName.CANDIDATE_GENERATOR, NodeName.PHOTO_PLANNER})
#: The subject a decision belongs to when the evidence names no subject at all.
CASE_SUBJECT = "case"


class Verdict(StrEnum):
    CONFIRMED = "confirmed"
    CONTRADICTED = "contradicted"
    UNSUPPORTED = "unsupported"


class AbstentionReason(StrEnum):
    MODEL_NAMED_NO_GENUS = "model_named_no_genus"
    NO_CARD = "no_card_for_genus"
    NOT_CONFIRMED = "not_confirmed"
    CONTRADICTED = "contradicted"
    AMBIGUOUS = "ambiguous"
    EVIDENCE_INSUFFICIENT = "evidence_insufficient"
    NO_EVIDENCE = "no_evidence"


class EvidenceRef(Contract):
    """One recorded observation, as an answer cites it."""

    observation_id: str
    feature: str
    value: str
    image_id: str | None = None
    visibility: str | None = None
    reliability: str | None = None


class CardCheck(Contract):
    """One card of one genus the model named, matched against one subject's evidence."""

    genus: str
    subject_id: str
    card: str
    verdict: Verdict
    strong: tuple[EvidenceRef, ...] = ()
    contradiction_ids: tuple[str, ...] = ()
    disqualifying_ids: tuple[str, ...] = ()


class Hypothesis(Contract):
    """The model's genus where the cards did not confirm it. Never an answer, never graded.

    Not the graph's :class:`~dendro_inspector.schemas.decisions.VisualHypothesis`, which
    asserts a reading the card calls diagnostic: this one asserts only what the model said.
    """

    taxon: str
    source: Literal["model_alone"] = "model_alone"
    status: Literal["unconfirmed"] = "unconfirmed"


class Decision(Contract):
    """Arm V's answer for one subject, in the stored-case decision shape."""

    subject_id: str
    selected_taxon: str | None = None
    resolution: Literal["genus", "unknown"] = "unknown"
    status: Literal["card_confirmed", "abstained"]
    abstained: bool
    abstention_reason: AbstentionReason | None = None
    evidence: tuple[EvidenceRef, ...] = ()
    visual_hypothesis: Hypothesis | None = None
    next_photo: tuple[str, ...] = ()


def model_genera(answer: model_alone.ModelAloneAnswer | None) -> tuple[str, ...]:
    """Every genus the model named, lower case, in its order, each once."""
    found: list[str] = []
    for subject in answer.subjects if answer is not None else ():
        genus = subject.genus.lower() if subject.genus else None
        if genus and genus not in found:
            found.append(genus)
    return tuple(found)


def genus_cards(knowledge: KnowledgeBase, genus: str) -> tuple[str, ...]:
    """The cards whose identity at genus rank is ``genus`` (genus and species cards)."""
    found = []
    for taxon_id in knowledge.available_taxon_ids():
        identity = knowledge.taxon(taxon_id).identity_at_or_broader(Resolution.GENUS)
        if identity is not None and identity.taxon_id == genus:
            found.append(taxon_id)
    return tuple(found)


def _ref(observation: Observation) -> EvidenceRef:
    return EvidenceRef(
        observation_id=observation.observation_id,
        feature=observation.feature,
        value=str(observation.value),
        image_id=observation.image_id,
        visibility=observation.visibility.value if observation.visibility else None,
        reliability=observation.reliability.value if observation.reliability else None,
    )


def check(knowledge: KnowledgeBase, packet: EvidencePacket, genus: str) -> tuple[CardCheck, ...]:
    """Every card of ``genus`` against every subject of the packet."""
    by_id = {observation.observation_id: observation for observation in packet.observations}
    checks = []
    for card_id in genus_cards(knowledge, genus):
        card = knowledge.taxon(card_id)
        for subject in packet.subjects:
            match = match_card(card, packet, subject.subject_id)
            if match.contradiction_hits or match.disqualifying_hits:
                verdict = Verdict.CONTRADICTED
            elif match.strong_hits:
                verdict = Verdict.CONFIRMED
            else:
                verdict = Verdict.UNSUPPORTED
            checks.append(
                CardCheck(
                    genus=genus,
                    subject_id=subject.subject_id,
                    card=card_id,
                    verdict=verdict,
                    strong=tuple(_ref(by_id[i]) for i in match.strong_hits if i in by_id),
                    contradiction_ids=tuple(match.contradiction_hits),
                    disqualifying_ids=tuple(match.disqualifying_hits),
                )
            )
    return tuple(checks)


def decide(
    answer: model_alone.ModelAloneAnswer | None,
    packet: EvidencePacket | None,
    knowledge: KnowledgeBase,
    *,
    evidence_sufficient: bool,
) -> tuple[tuple[CardCheck, ...], tuple[Decision, ...]]:
    """Arm V's deterministic decision: the card checks made, and one decision per subject."""
    genera = model_genera(answer)
    first = genera[0] if genera else None
    first_cards = genus_cards(knowledge, first) if first else ()

    def abstain(subject_id: str, reason: AbstentionReason, *, hypothesis: bool) -> Decision:
        return Decision(
            subject_id=subject_id,
            status="abstained",
            abstained=True,
            abstention_reason=reason,
            visual_hypothesis=Hypothesis(taxon=first) if first and hypothesis else None,
            next_photo=knowledge.follow_up_for(first_cards) if hypothesis else (),
        )

    if packet is None or not packet.subjects:
        return (), (abstain(CASE_SUBJECT, AbstentionReason.NO_EVIDENCE, hypothesis=True),)
    subject_ids = tuple(subject.subject_id for subject in packet.subjects)
    if not evidence_sufficient:
        reason = AbstentionReason.EVIDENCE_INSUFFICIENT
        return (), tuple(abstain(s, reason, hypothesis=True) for s in subject_ids)
    if not genera:
        reason = AbstentionReason.MODEL_NAMED_NO_GENUS
        return (), tuple(abstain(s, reason, hypothesis=True) for s in subject_ids)

    checks = tuple(c for genus in genera for c in check(knowledge, packet, genus))
    confirmed_on = {
        s: [
            g
            for g in genera
            if any(
                c.genus == g and c.subject_id == s for c in checks if c.verdict is Verdict.CONFIRMED
            )
        ]
        for s in subject_ids
    }
    named = {s for s, found in confirmed_on.items() if len(found) == 1}
    decisions = []
    for subject_id in subject_ids:
        mine = [c for c in checks if c.subject_id == subject_id]
        if subject_id in named:
            genus = confirmed_on[subject_id][0]
            cited: dict[str, EvidenceRef] = {}
            for c in mine:
                if c.genus == genus and c.verdict is Verdict.CONFIRMED:
                    for ref in c.strong:
                        cited.setdefault(ref.observation_id, ref)
            decisions.append(
                Decision(
                    subject_id=subject_id,
                    selected_taxon=genus,
                    resolution="genus",
                    status="card_confirmed",
                    abstained=False,
                    evidence=tuple(cited.values()),
                )
            )
            continue
        if len(confirmed_on[subject_id]) > 1:
            reason = AbstentionReason.AMBIGUOUS
        elif not checks:
            reason = AbstentionReason.NO_CARD
        elif any(c.verdict is Verdict.CONTRADICTED for c in mine):
            reason = AbstentionReason.CONTRADICTED
        else:
            reason = AbstentionReason.NOT_CONFIRMED
        decisions.append(abstain(subject_id, reason, hypothesis=not named))
    return checks, tuple(decisions)


def _tokens(events: Sequence[Mapping[str, Any]]) -> int:
    return sum(
        (call.get("input_tokens") or 0) + (call.get("output_tokens") or 0)
        for event in events
        for call in event.get("provider_calls") or ()
    )


def record(
    *,
    unit_id: str,
    case_id: str,
    answer: model_alone.ModelAloneAnswer | None,
    answer_source: str,
    namer_model: str,
    namer_call: Mapping[str, Any] | None,
    extractor_model: str,
    trace: Mapping[str, Any] | None,
    evidence_sufficient: bool,
    insufficient_reasons: Sequence[str],
    stopped_before: str | None,
    photos: Sequence[Mapping[str, Any]],
    field_context: bool,
    domain_prompt_sha256: str,
    image_max_edge_px: int | None,
    code_commit_sha: str | None,
    started_at: str,
    wall_seconds: Mapping[str, float],
    knowledge: KnowledgeBase,
    error: str | None = None,
) -> dict[str, Any]:
    """A stored-case record for one unit; ``error`` makes it a failed case.

    The decision is taken here, from the packet as ``trace`` records it, so the record and
    :func:`replay` read the same evidence.
    """
    trace = dict(trace or {})
    extraction_events = list(trace.get("events") or ())
    events = [
        *extraction_events,
        *([{"node": model_alone.NODE, "provider_calls": [namer_call]}] if namer_call else []),
    ]
    trace["events"] = events
    arm: dict[str, Any] = {
        "rules_version": RULES_VERSION,
        "request_sha256": model_alone.REQUEST_SHA256,
        "schema_sha256": model_alone.SCHEMA_SHA256,
        "domain_prompt_sha256": domain_prompt_sha256,
        "image_max_edge_px": image_max_edge_px,
        "field_context": field_context,
        "photos": list(photos),
        "answer_source": answer_source,
        "answer": answer.model_dump(mode="json") if answer is not None else None,
        "evidence_sufficient": evidence_sufficient,
        "insufficient_reasons": list(insufficient_reasons),
        "stopped_before": stopped_before,
        "wall_seconds": dict(wall_seconds),
        "tokens": {
            "namer": _tokens([{"provider_calls": [namer_call]}] if namer_call else []),
            "extraction": _tokens(extraction_events),
        },
    }
    failed = error is not None or answer is None or not trace
    result: dict[str, Any] = {"trace": trace, ARM: arm}
    if not failed:
        packet = packet_from_trace(trace)
        checks, decisions = decide(
            answer, packet, knowledge, evidence_sufficient=evidence_sufficient
        )
        arm["checks"] = [c.model_dump(mode="json") for c in checks]
        result["response"] = {
            "decisions": [d.model_dump(mode="json") for d in decisions],
            "target_subject_ids": [],
        }
    return {
        "unit": unit_id,
        "case_id": case_id,
        "arm": ARM,
        "models": {"namer": namer_model, "extractor": extractor_model},
        "started_at": started_at,
        "wall_seconds": wall_seconds.get("total", 0.0),
        "exit_code": 1 if failed else 0,
        **({"error": error} if error else {}),
        "failure" if failed else "result": result,
    }


def replay(stored: Mapping[str, Any], knowledge: KnowledgeBase) -> tuple[Decision, ...]:
    """Re-decide a stored arm V record from its recorded answer and evidence, offline."""
    result = stored["result"]
    arm = result[ARM]
    answer = model_alone.ModelAloneAnswer.model_validate(arm["answer"]) if arm["answer"] else None
    _, decisions = decide(
        answer,
        packet_from_trace(result["trace"]),
        knowledge,
        evidence_sufficient=bool(arm["evidence_sufficient"]),
    )
    return decisions


def replay_matches(stored: Mapping[str, Any], knowledge: KnowledgeBase) -> bool:
    """Whether today's rules reach exactly the decisions the record stores."""
    replayed = [d.model_dump(mode="json") for d in replay(stored, knowledge)]
    return replayed == list(stored["result"]["response"]["decisions"])


def subject_answers(stored: Mapping[str, Any]) -> tuple[SubjectAnswer, ...]:
    """What an arm V record committed to, per subject. Hypotheses commit to nothing."""
    response = (stored.get("result") or {}).get("response") or {}
    return tuple(
        SubjectAnswer(
            decision["subject_id"],
            genera=(decision["selected_taxon"],) if decision.get("selected_taxon") else (),
        )
        for decision in response.get("decisions") or ()
    )


def grade(stored: Mapping[str, Any], label: ReferenceLabel, knowledge: KnowledgeBase) -> Grade:
    """Grade an arm V record against a label with the graph's own scorer."""
    return grade_record(
        subject_answers(stored), model_alone.reference_with_family(knowledge, label)
    )


#: The namer: a coroutine giving the model's answer, its usage (None when read from a
#: record) and its wall time in seconds.
Namer = Callable[[], Awaitable[tuple[model_alone.ModelAloneAnswer, UsageSink | None, float]]]


async def run_unit(
    case: CaseInput,
    ctx: NodeContext,
    namer: Namer,
    *,
    unit_id: str,
    answer_source: str,
    namer_model: str,
    namer_adapter: str,
    photos: Sequence[Mapping[str, Any]],
    field_context: bool,
    domain_prompt_sha256: str,
    image_max_edge_px: int | None,
    code_commit_sha: str | None,
    started_at: str,
    namer_call: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Name and extract concurrently, then decide. A failure on either side fails the unit.

    ``namer_call`` overrides the provider-call entry recorded for the namer (an answer read
    from an earlier record carries that record's call, so its tokens are still counted).
    """
    started = time.monotonic()
    walls: dict[str, float] = {}

    async def timed_namer() -> tuple[model_alone.ModelAloneAnswer, UsageSink | None, float]:
        out = await namer()
        walls["namer"] = round(time.monotonic() - started, 1)
        return out

    async def timed_walk() -> GraphRunResult:
        out = await run_graph(case, ctx, build_registry(), stop_before=STOP_BEFORE)
        walls["extraction"] = round(time.monotonic() - started, 1)
        return out

    answer = None
    trace: Mapping[str, Any] | None = None
    result: GraphRunResult | None = None
    call: Mapping[str, Any] | None = namer_call
    error = None
    try:
        async with asyncio.TaskGroup() as group:
            named = group.create_task(timed_namer())
            walked = group.create_task(timed_walk())
        answer, usage, namer_wall = named.result()
        result = walked.result()
        trace = result.trace.model_dump(mode="json")
        if call is None:
            call = model_alone.provider_call(
                usage=usage, wall_seconds=namer_wall, adapter=namer_adapter, model=namer_model
            )
    except* Exception as group_error:
        first = group_error.exceptions[0]
        error = f"{type(first).__name__}: {first}"[:2000]
        partial = partial_trace_of(first)
        trace = partial.model_dump(mode="json") if partial is not None else None
    walls["total"] = round(time.monotonic() - started, 1)
    quality = result.state.quality if result is not None else None
    primary = ctx.providers.get(Role.PRIMARY)
    return record(
        unit_id=unit_id,
        case_id=case.case_id,
        answer=answer,
        answer_source=answer_source,
        namer_model=f"{namer_adapter}:{namer_model}",
        namer_call=call,
        extractor_model=f"{primary.adapter_name}:{primary.model}",
        trace=trace,
        evidence_sufficient=bool(quality and quality.sufficient),
        insufficient_reasons=tuple(quality.insufficient_reasons) if quality else (),
        stopped_before=(result.stopped_before.value if result and result.stopped_before else None),
        photos=photos,
        field_context=field_context,
        domain_prompt_sha256=domain_prompt_sha256,
        image_max_edge_px=image_max_edge_px,
        code_commit_sha=code_commit_sha,
        started_at=started_at,
        wall_seconds=walls,
        knowledge=ctx.knowledge,
        error=error,
    )
