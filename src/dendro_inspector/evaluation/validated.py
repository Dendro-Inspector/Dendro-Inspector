"""Arm V: the model alone, checked against its card on the graph's own evidence.

Arm M (:mod:`dendro_inspector.evaluation.model_alone`) asks the model once for a genus. Arm V
asks the same question and, in parallel, runs the graph's first stages exactly as an
inspection does: input guard, planner, evidence extractor, evidence quality, and nothing
after them. No candidate generation, review, arbitration, correction retry or graph fallback
runs. Code then decides, with no model call, whether the observations confirm the model's
genus:

* The model's answer binds to the evidence only when it names exactly one genus and the
  evidence shows exactly one organism: one root subject and the components attached to it.
  The model's subjects are free text with no evidence subject ID, so with several genera
  (``unbound_genera``) or several organisms (``unbound_subjects``) nothing says which tree
  the answer was about, and nothing is named.
* For every card of that genus, ``match_card`` (the admission boundary the graph itself
  uses) matches each subject of the organism on its own evidence. The pairing is
  ``confirmed`` when a strong-positive feature of the card was observed with no
  contradicting or disqualifying observation, ``contradicted`` when such an observation
  exists, ``unsupported`` otherwise. A card contradicted on any part of the organism
  confirms nothing.
* The organism is named with the genus when a card is confirmed, citing the observations
  that confirmed it (feature, value, image).
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
import json
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
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
#: The arm name and result key of an arm M record (``model_alone.record``).
_ARM_M = "model_alone"
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
    UNBOUND_GENERA = "unbound_genera"
    UNBOUND_SUBJECTS = "unbound_subjects"
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


def organisms(packet: EvidencePacket) -> dict[str, tuple[str, ...]]:
    """Each root subject with the components that visibly belong to it, root first.

    A root (no ``parent_subject_id``) is an independent identity scope: a tree, a log, a
    detached part or a material sample (:class:`~dendro_inspector.schemas.evidence.Subject`).
    """
    parent = {subject.subject_id: subject.parent_subject_id for subject in packet.subjects}

    def root_of(subject_id: str) -> str:
        seen: set[str] = set()
        while parent.get(subject_id) and subject_id not in seen:
            seen.add(subject_id)
            subject_id = parent[subject_id] or subject_id
        return subject_id

    groups: dict[str, list[str]] = {}
    for subject in packet.subjects:
        root = root_of(subject.subject_id)
        members = groups.setdefault(root, [root])
        if subject.subject_id != root:
            members.append(subject.subject_id)
    return {root: tuple(members) for root, members in groups.items()}


def decide(
    answer: model_alone.ModelAloneAnswer | None,
    packet: EvidencePacket | None,
    knowledge: KnowledgeBase,
    *,
    evidence_sufficient: bool,
) -> tuple[tuple[CardCheck, ...], tuple[Decision, ...]]:
    """Arm V's deterministic decision: the card checks made, and one decision per organism.

    The model's answer is bound to the evidence only when it names one genus and the evidence
    shows one organism (a root subject and the components attached to it). The model's
    subjects are free text with no evidence subject ID, so with several genera or several
    organisms nothing says which tree the answer was about, and nothing is named.
    """
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
    groups = organisms(packet)
    roots = tuple(groups)
    if not evidence_sufficient:
        reason = AbstentionReason.EVIDENCE_INSUFFICIENT
        return (), tuple(abstain(r, reason, hypothesis=True) for r in roots)
    if not genera:
        reason = AbstentionReason.MODEL_NAMED_NO_GENUS
        return (), tuple(abstain(r, reason, hypothesis=True) for r in roots)
    if len(genera) > 1:
        reason = AbstentionReason.UNBOUND_GENERA
        return (), tuple(abstain(r, reason, hypothesis=False) for r in roots)

    genus = genera[0]
    checks = check(knowledge, packet, genus)
    if len(roots) > 1:
        # Recorded so that a later subject binding can be measured; nothing is named.
        reason = AbstentionReason.UNBOUND_SUBJECTS
        return checks, tuple(abstain(r, reason, hypothesis=False) for r in roots)

    root = roots[0]
    members = set(groups[root])
    mine = [c for c in checks if c.subject_id in members]
    contradicted_cards = {c.card for c in mine if c.verdict is Verdict.CONTRADICTED}
    cited: dict[str, EvidenceRef] = {}
    for c in mine:
        if c.verdict is Verdict.CONFIRMED and c.card not in contradicted_cards:
            for ref in c.strong:
                cited.setdefault(ref.observation_id, ref)
    if cited:
        named = Decision(
            subject_id=root,
            selected_taxon=genus,
            resolution="genus",
            status="card_confirmed",
            abstained=False,
            evidence=tuple(cited.values()),
        )
        return checks, (named,)
    if not checks:
        reason = AbstentionReason.NO_CARD
    elif contradicted_cards:
        reason = AbstentionReason.CONTRADICTED
    else:
        reason = AbstentionReason.NOT_CONFIRMED
    return checks, (abstain(root, reason, hypothesis=True),)


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
    identity: Mapping[str, Any] | None = None,
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
        "run_identity": dict(identity) if identity is not None else None,
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
        "code_commit_sha": code_commit_sha,
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


def run_identity(
    *,
    code_commit_sha: str | None,
    config_sha256: str,
    knowledge_sha256: str,
    namer: str,
    extractor: str,
    domain_prompt_sha256: str,
    image_max_edge_px: int | None,
) -> dict[str, Any]:
    """What makes two arm V records comparable: code, configuration, cards, prompts, models.

    ``namer`` is ``live:<adapter:model>`` or ``answers:<folder>``. A run resumes into a folder
    only when every decided record there has the same identity, so one result never mixes
    records from different code, settings or namers.
    """
    return {
        "rules_version": RULES_VERSION,
        "code_commit_sha": code_commit_sha,
        "config_sha256": config_sha256,
        "knowledge_sha256": knowledge_sha256,
        "request_sha256": model_alone.REQUEST_SHA256,
        "schema_sha256": model_alone.SCHEMA_SHA256,
        "domain_prompt_sha256": domain_prompt_sha256,
        "image_max_edge_px": image_max_edge_px,
        "namer": namer,
        "extractor": extractor,
    }


def resume_conflicts(
    stored: Mapping[str, Any],
    identity: Mapping[str, Any],
    *,
    photo_sha256: Sequence[str],
    field_context: bool,
) -> tuple[str, ...]:
    """What differs between a decided record and the run that would reuse it."""
    arm = stored["result"][ARM]
    recorded = arm.get("run_identity") or {}
    differ = [key for key in identity if recorded.get(key) != identity[key]]
    if [photo.get("sha256") for photo in arm.get("photos") or ()] != list(photo_sha256):
        differ.append("photos")
    if bool(arm.get("field_context")) != field_context:
        differ.append("field_context")
    return tuple(differ)


#: Differences that concern one unit's inputs, not the run: that unit is run again (and a
#: missing photograph then fails it alone); any other difference refuses the whole run.
UNIT_LEVEL = frozenset({"photos", "field_context"})


def resume_action(differ: Sequence[str]) -> Literal["reuse", "rerun", "refuse"]:
    """What a run does with an existing decided record, given :func:`resume_conflicts`."""
    if not differ:
        return "reuse"
    return "rerun" if set(differ) <= UNIT_LEVEL else "refuse"


PLAN = "plan.json"


def write_plan(directory: Path, unit_ids: Sequence[str], identity: Mapping[str, Any]) -> None:
    """Record which units this run planned, so readers ignore records left by other plans."""
    directory.mkdir(parents=True, exist_ok=True)
    plan = {"arm": ARM, "run_identity": dict(identity), "units": list(unit_ids)}
    (directory / PLAN).write_text(json.dumps(plan, indent=1) + "\n", encoding="utf-8")


@dataclass(frozen=True, slots=True)
class PlannedRecords:
    """A run folder read against its plan: every planned unit, and what lies outside it."""

    records: Mapping[str, Mapping[str, Any] | None]
    outside_plan: tuple[str, ...]


def planned_records(directory: Path) -> PlannedRecords:
    """Every planned unit's record (None when it has none) and the records not planned.

    A folder without a plan (written before plans existed) counts every record it holds.
    """
    files = {path.stem: path for path in sorted((directory / "cases").glob("*.json"))}
    plan_path = directory / PLAN
    planned = (
        list(json.loads(plan_path.read_text(encoding="utf-8"))["units"])
        if plan_path.exists()
        else list(files)
    )
    records = {
        unit: json.loads(files[unit].read_text(encoding="utf-8")) if unit in files else None
        for unit in planned
    }
    return PlannedRecords(
        records=records, outside_plan=tuple(stem for stem in files if stem not in records)
    )


def setup_failure(
    *, unit_id: str, error: str, started_at: str, code_commit_sha: str | None = None
) -> dict[str, Any]:
    """The record of a planned unit refused before it started, so no unit goes uncounted."""
    return {
        "unit": unit_id,
        "arm": ARM,
        "code_commit_sha": code_commit_sha,
        "started_at": started_at,
        "wall_seconds": 0.0,
        "exit_code": 1,
        "error": error,
        "failure": {"stage": "setup"},
    }


def decided(path: Path) -> bool:
    """Whether ``path`` holds an arm V record with a decision (not a failure)."""
    if not path.exists():
        return False
    stored = json.loads(path.read_text(encoding="utf-8"))
    return stored.get("arm") == ARM and "result" in stored


@dataclass(frozen=True, slots=True)
class ReplaySummary:
    """What ``--replay`` found in a run folder."""

    checked: int
    differing: tuple[str, ...]

    @property
    def exit_code(self) -> int:
        """0 only when records were replayed and all matched; 2 when there was nothing."""
        if not self.checked:
            return 2
        return 1 if self.differing else 0


def replay_run(directory: Path, knowledge: KnowledgeBase) -> ReplaySummary:
    """Replay every planned arm V record with a decision; failed and unplanned ones are not."""
    checked, differing = 0, []
    for unit, stored in planned_records(directory).records.items():
        if stored is None or stored.get("arm") != ARM or "result" not in stored:
            continue
        checked += 1
        if not replay_matches(stored, knowledge):
            differing.append(unit)
    return ReplaySummary(checked=checked, differing=tuple(differing))


class AnswerMismatchError(ValueError):
    """An earlier arm M record does not answer the unit as arm V would ask it."""


@dataclass(frozen=True, slots=True)
class RecordedAnswer:
    answer: model_alone.ModelAloneAnswer
    call: Mapping[str, Any]
    adapter: str
    model: str
    source: str


def answer_from_record(
    earlier: Mapping[str, Any],
    *,
    photo_sha256: Sequence[str],
    field_context: bool,
    domain_prompt_sha256: str,
) -> RecordedAnswer:
    """The answer an earlier arm M run gave, if it was given to exactly this question.

    The photographs must be the same files in the same order (the answer cites them by
    position, ``img-N``), under the same field-context statement, request, answer schema and
    domain prompt. Anything else would check one question's answer against another
    question's evidence, so it is refused rather than recorded.
    """
    if earlier.get("arm") != _ARM_M or "result" not in earlier:
        msg = "the record holds no arm M answer"
        raise AnswerMismatchError(msg)
    arm = earlier["result"][_ARM_M]
    recorded = [photo.get("sha256") for photo in arm.get("photos") or ()]
    if recorded != list(photo_sha256):
        msg = "the answer was given for other photographs, or in another order"
        raise AnswerMismatchError(msg)
    if bool(arm.get("field_context")) != field_context:
        msg = "the answer was given under another field-context statement"
        raise AnswerMismatchError(msg)
    for key, expected in (
        ("request_sha256", model_alone.REQUEST_SHA256),
        ("schema_sha256", model_alone.SCHEMA_SHA256),
        ("domain_prompt_sha256", domain_prompt_sha256),
    ):
        if arm.get(key) != expected:
            msg = f"the answer was given to another {key.removesuffix('_sha256')}"
            raise AnswerMismatchError(msg)
    adapter, _, model = str(earlier["models"]["primary"]).partition(":")
    return RecordedAnswer(
        answer=model_alone.ModelAloneAnswer.model_validate(arm["answer"]),
        call=earlier["result"]["trace"]["events"][0]["provider_calls"][0],
        adapter=adapter,
        model=model,
        source=f"record:{earlier['models']['primary']}:{earlier.get('started_at')}",
    )


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
    identity: Mapping[str, Any] | None = None,
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
        identity=identity,
    )
