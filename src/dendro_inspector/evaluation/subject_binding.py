"""Explicit subject binding for arm V (experimental, opt-in): which organism an answer means.

Arm V (:mod:`dendro_inspector.evaluation.validated`) names nothing when an answer with one
genus meets an evidence packet with several organisms (``unbound_subjects``): the answer's
subjects are free text, the evidence's are subject IDs, and photographs alone do not link
them, because the model cites nearly every photograph and organisms share photographs.

This module asks one more question, only about such a unit, and only that one: which listed
root subject is the answer about? The request carries the photographs, the unchanged answer,
the image IDs and the evidence subjects (IDs, kinds, parents, descriptions, observations).
The reply (:class:`BindingReply`) has no genus, species, family or confidence field. Binding
establishes subject identity, not botanical truth: the genus stays the namer's, and whether
the evidence supports it stays the card check's.

A model proposes, code adjudicates (AGENTS.md section 4.6). :func:`verify` accepts a reply
only when every identifier exists in the packet and the reply cites a reference that tells
the selected organism apart from every other one (see :func:`verify` for the rules). Anything
else keeps arm V's abstention. A verified binding names at most that one organism, after
:func:`validated.decide` checks the genus's cards on that organism's own evidence (its root
and attached components, unchanged), with arm V's contradiction rule. Every other organism
keeps its ``unbound_subjects`` abstention.

Records keep the binding request, the reply and the call, so :func:`replay_status` re-decides
a record offline with no provider, and reports a record made under other rules or from other
inputs instead of reusing it. Nothing here changes arm V or any production default; the
driver is ``scripts/eval/bind_subjects.py``.
"""

from __future__ import annotations

import copy
import hashlib
import json
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from dendro_inspector.evaluation import model_alone, validated
from dendro_inspector.evaluation.trace_replay import packet_from_trace
from dendro_inspector.knowledge.loader import KnowledgeBase
from dendro_inspector.providers.base import (
    USAGE_SINK,
    ImageInput,
    ModelProvider,
    UsageSink,
    request_structured,
)
from dendro_inspector.schemas.base import Contract
from dendro_inspector.schemas.evidence import EvidencePacket

ARM = "validated_bound"
NODE = "subject_binding"
#: Bumped whenever a rule in :func:`ineligible_reason`, :func:`verify` or
#: :func:`decide_bound` changes; every record names the version it used.
RULES_VERSION = 1

REQUEST = """## Task: say which listed organism an answer is about

You are shown every photograph of one inspection, in the order of the image IDs listed below.
Another model wrote the answer below about these photographs. A separate pass listed the
organisms the photographs show: each organism is a root subject (an independent tree, log,
detached part or material sample) with the components that visibly belong to it, and the
observations recorded on them.

Exactly one entry of the answer names a genus. Say which ONE listed organism that entry is
about, judged by what the entry describes (its subject and the features it cites, with their
image IDs) and by what you can see in the photographs.

Rules:
- Do not identify any plant or wood, and do not judge whether the answer is right. The genus
  is shown only so you know which entry is meant. Never change or suggest a genus.
- Select a root `subject_id` from the list: never a component's ID and never a new ID.
- Select one organism only. If the entry describes several listed organisms as one subject,
  select the organism its subject names as the main one; if it names none as the main one,
  abstain with `describes_several`.
- A detached part (a fallen leaf, a cone or needles on the ground) is its own organism. Never
  treat it as part of a tree unless the list shows it as that tree's component.
- Do not decide by the order of the photographs or of the list, or by how many photographs
  show an organism. A photograph that shows several organisms proves nothing on its own.
- `supporting_image_ids`: photographs in which you can see the selected organism with what the
  entry describes on it. Include at least one in which it is told apart from the others.
- `supporting_observation_ids`: listed observations of the selected organism (or of its
  components) that correspond to features the entry cites; empty if none corresponds.
- `distinguishing_evidence`: what in those photographs shows that the entry is about this
  organism and not each of the others; when abstaining, why it cannot be told.
- If you cannot tell from that kind of evidence, set `status` to `abstain`, `subject_id` to
  null and `abstention_reason` to `ambiguous` (more than one organism fits),
  `describes_several`, `not_listed` (the entry describes none of them) or `not_visible` (the
  described organism cannot be made out). With `status` `bound`, `abstention_reason` is null.
"""


class BindingStatus(StrEnum):
    BOUND = "bound"
    ABSTAIN = "abstain"


class BinderAbstention(StrEnum):
    """Why the binder could not say which organism the answer is about."""

    AMBIGUOUS = "ambiguous"
    DESCRIBES_SEVERAL = "describes_several"
    NOT_LISTED = "not_listed"
    NOT_VISIBLE = "not_visible"


class BindingReply(Contract):
    """The binding call's whole reply: an organism and references, never a taxon or a score."""

    status: BindingStatus
    subject_id: str | None = Field(
        max_length=64, description="The selected root subject ID; null when abstaining."
    )
    supporting_image_ids: tuple[str, ...] = Field(
        default=(),
        max_length=16,
        description="Photographs in which the selected organism is visible as the answer says.",
    )
    supporting_observation_ids: tuple[str, ...] = Field(
        default=(),
        max_length=32,
        description="Listed observations of the selected organism matching the answer's features.",
    )
    distinguishing_evidence: str = Field(
        min_length=1,
        max_length=600,
        description="What separates the selected organism from every other listed one.",
    )
    abstention_reason: BinderAbstention | None = Field(
        description="Why no organism can be selected; null when status is bound."
    )


#: The schema and request the binder is held to; their hashes go into every record.
SCHEMA_SHA256 = hashlib.sha256(
    json.dumps(BindingReply.model_json_schema(), sort_keys=True).encode()
).hexdigest()
REQUEST_SHA256 = hashlib.sha256(REQUEST.encode()).hexdigest()


class Ineligible(StrEnum):
    """Why a unit gets no binding call: arm V's own decision stands."""

    NO_EVIDENCE = "no_evidence"
    EVIDENCE_INSUFFICIENT = "evidence_insufficient"
    NO_NAMED_SUBJECT = "no_named_subject"
    SEVERAL_NAMED_SUBJECTS = "several_named_subjects"
    SINGLE_ORGANISM = "single_organism"
    NOT_UNBOUND = "not_unbound_subjects"


class Outcome(StrEnum):
    """What became of the binding step for one unit."""

    NOT_ELIGIBLE = "not_eligible"
    BOUND = "bound"
    NO_REPLY = "no_reply"
    CALL_FAILED = "call_failed"
    BINDER_ABSTAINED = "binder_abstained"
    # A reply code rejected, so arm V's abstention stands:
    CONTRADICTORY_REPLY = "contradictory_reply"
    UNKNOWN_SUBJECT = "unknown_subject"
    NOT_A_ROOT = "not_a_root"
    NO_SUPPORTING_IMAGE = "no_supporting_image"
    UNKNOWN_IMAGE = "unknown_image"
    IMAGE_DOES_NOT_SHOW_SUBJECT = "image_does_not_show_subject"
    UNKNOWN_OBSERVATION = "unknown_observation"
    OBSERVATION_OF_ANOTHER_ORGANISM = "observation_of_another_organism"
    DISAGREES_WITH_ANSWER = "disagrees_with_answer"
    NOT_DISTINGUISHED = "not_distinguished"


REJECTED = frozenset(
    {
        Outcome.CONTRADICTORY_REPLY,
        Outcome.UNKNOWN_SUBJECT,
        Outcome.NOT_A_ROOT,
        Outcome.NO_SUPPORTING_IMAGE,
        Outcome.UNKNOWN_IMAGE,
        Outcome.IMAGE_DOES_NOT_SHOW_SUBJECT,
        Outcome.UNKNOWN_OBSERVATION,
        Outcome.OBSERVATION_OF_ANOTHER_ORGANISM,
        Outcome.DISAGREES_WITH_ANSWER,
        Outcome.NOT_DISTINGUISHED,
    }
)


def named_subjects(
    answer: model_alone.ModelAloneAnswer | None,
) -> tuple[model_alone.Subject, ...]:
    """The answer's entries that name a genus."""
    return tuple(s for s in answer.subjects if s.genus) if answer is not None else ()


def ineligible_reason(
    answer: model_alone.ModelAloneAnswer | None,
    packet: EvidencePacket | None,
    decisions: Sequence[validated.Decision],
    *,
    evidence_sufficient: bool,
) -> Ineligible | None:
    """None only when binding may run: arm V abstained for several organisms, nothing else.

    Evidence quality is sufficient, exactly one answer entry names a genus, the packet has
    several organisms, and every arm V decision is ``unbound_subjects``.
    """
    if packet is None or not packet.subjects:
        return Ineligible.NO_EVIDENCE
    if not evidence_sufficient:
        return Ineligible.EVIDENCE_INSUFFICIENT
    named = named_subjects(answer)
    if not named:
        return Ineligible.NO_NAMED_SUBJECT
    if len(named) > 1:
        return Ineligible.SEVERAL_NAMED_SUBJECTS
    if len(validated.organisms(packet)) < 2:
        return Ineligible.SINGLE_ORGANISM
    if not decisions or any(
        d.abstention_reason is not validated.AbstentionReason.UNBOUND_SUBJECTS for d in decisions
    ):
        return Ineligible.NOT_UNBOUND
    return None


def organism_images(packet: EvidencePacket, members: Sequence[str]) -> set[str]:
    """The photographs that show an organism: its subjects' and its observations' images."""
    keep = set(members)
    shown = {i for s in packet.subjects if s.subject_id in keep for i in s.image_ids}
    shown |= {o.image_id for o in packet.observations if o.subject_id in keep and o.image_id}
    return shown


def image_ids_of(photo_count: int) -> tuple[str, ...]:
    """The image IDs every arm names a unit's photographs by, in order."""
    return tuple(f"img-{number}" for number in range(1, photo_count + 1))


def context(
    answer: model_alone.ModelAloneAnswer, packet: EvidencePacket, image_ids: Sequence[str]
) -> str:
    """What the binding call reads besides the photographs and :data:`REQUEST`."""
    by_id = {s.subject_id: s for s in packet.subjects}

    def subject(subject_id: str) -> dict[str, Any]:
        s = by_id[subject_id]
        return {
            "subject_id": s.subject_id,
            "kind": s.kind.value,
            "parent_subject_id": s.parent_subject_id,
            "description": s.description,
            "image_ids": list(s.image_ids),
        }

    listed = []
    for root, members in validated.organisms(packet).items():
        keep = set(members)
        listed.append(
            {
                **subject(root),
                "components": [subject(m) for m in members if m != root],
                "observations": [
                    {
                        "observation_id": o.observation_id,
                        "subject_id": o.subject_id,
                        "feature": o.feature,
                        "value": str(o.value),
                        "image_id": o.image_id,
                    }
                    for o in packet.observations
                    if o.subject_id in keep
                ],
            }
        )
    return (
        "## Image IDs\n\n"
        + ", ".join(image_ids)
        + "\n\n## Listed organisms\n\n"
        + json.dumps(listed, ensure_ascii=False, indent=1)
        + "\n\n## The answer\n\n"
        + json.dumps(answer.model_dump(mode="json"), ensure_ascii=False, indent=1)
    )


def input_sha256(text: str, photo_sha256: Sequence[str]) -> str:
    """The binding request's full input: its context and the photographs, in order."""
    payload = {"request_sha256": REQUEST_SHA256, "context": text, "photos": list(photo_sha256)}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


async def ask(
    provider: ModelProvider,
    *,
    text: str,
    images: Sequence[ImageInput],
    max_retries: int = 2,
) -> tuple[BindingReply, UsageSink, float]:
    """The one binding call: its reply, its token accounting and its wall time in seconds."""
    usage = UsageSink()
    started = time.monotonic()
    reply = await request_structured(
        provider=provider,
        role="primary",
        node=NODE,
        prompt=REQUEST + "\n\n" + text,
        images=images,
        response_model=BindingReply,
        metadata={USAGE_SINK: usage},
        max_retries=max_retries,
    )
    return reply, usage, round(time.monotonic() - started, 1)


def verify(
    reply: BindingReply,
    packet: EvidencePacket,
    answer: model_alone.ModelAloneAnswer,
    image_ids: Sequence[str],
) -> tuple[Outcome, str | None]:
    """Whether code can check the reply against the packet, and the organism it binds.

    In this order, the first failure wins:

    * the reply is consistent: ``bound`` with a subject and no abstention reason, or
      ``abstain`` with a reason and no subject;
    * the subject exists and is a root (an independent organism, never a component);
    * it cites at least one photograph; each is one of the unit's and shows the organism;
    * each cited observation exists and was recorded on the organism or its components;
    * the organism is visible in a photograph the answer itself cites (else the binder
      disagrees with the answer it was asked about);
    * a reference tells the organism apart from every other one: a cited photograph that
      the answer also cites and that shows no other organism, or a cited observation made in
      a photograph the answer cites whose feature and value no other organism has.

    Photograph order, shared photographs and the binder's own wording authorise nothing.
    """
    if reply.status is BindingStatus.ABSTAIN:
        if reply.subject_id is not None or reply.abstention_reason is None:
            return Outcome.CONTRADICTORY_REPLY, None
        return Outcome.BINDER_ABSTAINED, None
    if reply.subject_id is None or reply.abstention_reason is not None:
        return Outcome.CONTRADICTORY_REPLY, None
    subject_id = reply.subject_id
    groups = validated.organisms(packet)
    if subject_id not in {s.subject_id for s in packet.subjects}:
        return Outcome.UNKNOWN_SUBJECT, None
    if subject_id not in groups:
        return Outcome.NOT_A_ROOT, None
    cited_images = set(reply.supporting_image_ids)
    if not cited_images:
        return Outcome.NO_SUPPORTING_IMAGE, None
    if not cited_images <= set(image_ids):
        return Outcome.UNKNOWN_IMAGE, None
    mine = organism_images(packet, groups[subject_id])
    if not cited_images <= mine:
        return Outcome.IMAGE_DOES_NOT_SHOW_SUBJECT, None
    by_id = {o.observation_id: o for o in packet.observations}
    if any(i not in by_id for i in reply.supporting_observation_ids):
        return Outcome.UNKNOWN_OBSERVATION, None
    members = set(groups[subject_id])
    cited_observations = [by_id[i] for i in reply.supporting_observation_ids]
    if any(o.subject_id not in members for o in cited_observations):
        return Outcome.OBSERVATION_OF_ANOTHER_ORGANISM, None
    answered = {i for s in named_subjects(answer) for e in s.evidence for i in e.image_ids}
    if not mine & answered:
        return Outcome.DISAGREES_WITH_ANSWER, None
    others = set().union(
        *(organism_images(packet, group) for root, group in groups.items() if root != subject_id)
    )
    elsewhere = {
        (o.feature, str(o.value)) for o in packet.observations if o.subject_id not in members
    }
    exclusive_image = any(i in answered and i not in others for i in cited_images)
    distinct_observation = any(
        o.image_id in answered and (o.feature, str(o.value)) not in elsewhere
        for o in cited_observations
    )
    if not (exclusive_image or distinct_observation):
        return Outcome.NOT_DISTINGUISHED, None
    return Outcome.BOUND, subject_id


def organism_decision(
    answer: model_alone.ModelAloneAnswer,
    packet: EvidencePacket,
    knowledge: KnowledgeBase,
    root: str,
) -> validated.Decision:
    """Arm V's decision for one organism alone: its root and attached components, unchanged.

    ``match_card`` reads one subject's own observations, so the organism's checks equal the
    whole packet's for those subjects; a card contradicted on any of them confirms nothing.
    """
    keep = set(validated.organisms(packet)[root])
    alone = packet.model_copy(
        update={
            "subjects": tuple(s for s in packet.subjects if s.subject_id in keep),
            "observations": tuple(o for o in packet.observations if o.subject_id in keep),
            "inferences": (),
        }
    )
    _, decisions = validated.decide(answer, alone, knowledge, evidence_sufficient=True)
    return decisions[0]


def oracle(
    answer: model_alone.ModelAloneAnswer, packet: EvidencePacket, knowledge: KnowledgeBase
) -> dict[str, validated.Decision]:
    """Per root, the decision a binding to that root would reach. An upper bound only."""
    return {
        root: organism_decision(answer, packet, knowledge, root)
        for root in validated.organisms(packet)
    }


@dataclass(frozen=True, slots=True)
class BoundDecision:
    checks: tuple[validated.CardCheck, ...]
    decisions: tuple[validated.Decision, ...]
    ineligible: Ineligible | None
    outcome: Outcome
    subject_id: str | None


def decide_bound(
    answer: model_alone.ModelAloneAnswer | None,
    packet: EvidencePacket | None,
    knowledge: KnowledgeBase,
    *,
    evidence_sufficient: bool,
    reply: BindingReply | None,
    image_ids: Sequence[str],
) -> BoundDecision:
    """Arm V's decision, with the one organism a verified reply binds checked on its own."""
    checks, decisions = validated.decide(
        answer, packet, knowledge, evidence_sufficient=evidence_sufficient
    )
    reason = ineligible_reason(answer, packet, decisions, evidence_sufficient=evidence_sufficient)
    if reason is not None or answer is None or packet is None:
        return BoundDecision(checks, decisions, reason, Outcome.NOT_ELIGIBLE, None)
    if reply is None:
        return BoundDecision(checks, decisions, None, Outcome.NO_REPLY, None)
    outcome, root = verify(reply, packet, answer, image_ids)
    if outcome is not Outcome.BOUND or root is None:
        return BoundDecision(checks, decisions, None, outcome, None)
    own = organism_decision(answer, packet, knowledge, root)
    decided = tuple(own if d.subject_id == root else d for d in decisions)
    return BoundDecision(checks, decided, None, Outcome.BOUND, root)


def record_sha256(stored: Mapping[str, Any]) -> str:
    """A record's content hash, so a binding knows which arm V record it was made from."""
    return hashlib.sha256(json.dumps(stored, sort_keys=True).encode()).hexdigest()


def inputs_of(
    stored: Mapping[str, Any],
) -> tuple[model_alone.ModelAloneAnswer, EvidencePacket, tuple[str, ...], list[str]]:
    """A decided arm V record's answer, packet, image IDs and photograph hashes."""
    result = stored["result"]
    arm = result[validated.ARM]
    photos = [str(photo.get("sha256")) for photo in arm.get("photos") or ()]
    return (
        model_alone.ModelAloneAnswer.model_validate(arm["answer"]),
        packet_from_trace(result["trace"]),
        image_ids_of(len(photos)),
        photos,
    )


def source_failure(
    unit_id: str, arm_v: Mapping[str, Any] | None, identity: Mapping[str, Any] | None
) -> dict[str, Any]:
    """The record of a unit with no arm V decision to bind, so every planned unit is counted."""
    error = "arm V has no record of this unit" if arm_v is None else "arm V failed this unit"
    if arm_v is not None and arm_v.get("error"):
        error += f": {arm_v['error']}"
    return {
        "unit": unit_id,
        "arm": ARM,
        "exit_code": 1,
        "error": error[:2000],
        "failure": {"stage": "arm_v_missing" if arm_v is None else "arm_v"},
        "run_identity": dict(identity) if identity is not None else None,
    }


def record(
    arm_v: Mapping[str, Any],
    *,
    knowledge: KnowledgeBase,
    reply: BindingReply | None = None,
    call: Mapping[str, Any] | None = None,
    error: str | None = None,
    binder: str | None = None,
    identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """An arm V record with the binding step applied. The arm V record is not changed."""
    stored: dict[str, Any] = copy.deepcopy(dict(arm_v))
    result = stored["result"]
    answer, packet, image_ids, photos = inputs_of(stored)
    bound = decide_bound(
        answer,
        packet,
        knowledge,
        evidence_sufficient=bool(result[validated.ARM]["evidence_sufficient"]),
        reply=reply,
        image_ids=image_ids,
    )
    eligible = bound.ineligible is None
    text = context(answer, packet, image_ids) if eligible else None
    if call is not None:
        result["trace"].setdefault("events", []).append({"node": NODE, "provider_calls": [call]})
    accepted = reply if bound.outcome is Outcome.BOUND else None
    result["binding"] = {
        "rules_version": RULES_VERSION,
        "request_sha256": REQUEST_SHA256,
        "schema_sha256": SCHEMA_SHA256,
        "eligible": eligible,
        "ineligible_reason": bound.ineligible.value if bound.ineligible else None,
        "asked": reply is not None or error is not None,
        "binder": binder,
        "context": text,
        "input_sha256": input_sha256(text, photos) if text is not None else None,
        "reply": reply.model_dump(mode="json") if reply is not None else None,
        "outcome": (Outcome.CALL_FAILED if error is not None else bound.outcome).value,
        "subject_id": bound.subject_id,
        "supporting_image_ids": list(accepted.supporting_image_ids) if accepted else [],
        "supporting_observation_ids": (
            list(accepted.supporting_observation_ids) if accepted else []
        ),
        "error": error,
        "call": dict(call) if call is not None else None,
        "source_record_sha256": record_sha256(arm_v),
        "run_identity": dict(identity) if identity is not None else None,
    }
    result["response"]["decisions"] = [d.model_dump(mode="json") for d in bound.decisions]
    stored["arm"] = ARM
    return stored


async def bind_unit(
    arm_v: Mapping[str, Any],
    *,
    knowledge: KnowledgeBase,
    provider: ModelProvider,
    images: Callable[[], Sequence[ImageInput]],
    max_retries: int = 2,
    identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Bind one arm V record: at most one provider call, and only for an eligible unit.

    ``images`` gives the unit's photographs; it is called only when a call will be made, and
    may raise (a changed photograph), which fails the call and keeps arm V's abstention.
    """
    unit_id = str(arm_v.get("unit"))
    if "result" not in arm_v:
        return source_failure(unit_id, arm_v, identity)
    answer, packet, image_ids, _ = inputs_of(arm_v)
    base = decide_bound(
        answer,
        packet,
        knowledge,
        evidence_sufficient=bool(arm_v["result"][validated.ARM]["evidence_sufficient"]),
        reply=None,
        image_ids=image_ids,
    )
    if base.ineligible is not None:
        return record(arm_v, knowledge=knowledge, identity=identity)
    binder = f"{provider.adapter_name}:{provider.model}"
    reply: BindingReply | None = None
    call: dict[str, Any] | None = None
    error: str | None = None
    try:
        reply, usage, wall = await ask(
            provider,
            text=context(answer, packet, image_ids),
            images=images(),
            max_retries=max_retries,
        )
        call = {
            **model_alone.provider_call(
                usage=usage, wall_seconds=wall, adapter=provider.adapter_name, model=provider.model
            ),
            "node": NODE,
        }
    except Exception as exc:  # recorded as call_failed; arm V's abstention stands
        error = f"{type(exc).__name__}: {exc}"[:2000]
    return record(
        arm_v,
        knowledge=knowledge,
        reply=reply,
        call=call,
        error=error,
        binder=binder,
        identity=identity,
    )


def run_identity(
    *,
    arm_v: Mapping[str, Any] | None,
    source: str,
    binder: str,
    code_commit_sha: str | None,
    image_max_edge_px: int | None,
) -> dict[str, Any]:
    """What makes two binding records comparable; a run never resumes into another's folder."""
    return {
        "rules_version": RULES_VERSION,
        "request_sha256": REQUEST_SHA256,
        "schema_sha256": SCHEMA_SHA256,
        "binder": binder,
        "code_commit_sha": code_commit_sha,
        "image_max_edge_px": image_max_edge_px,
        "source": source,
        "arm_v": dict(arm_v) if arm_v is not None else None,
    }


def resume_action(
    existing: Mapping[str, Any] | None,
    *,
    identity: Mapping[str, Any],
    source_sha256: str | None,
) -> tuple[Literal["run", "reuse", "refuse"], str]:
    """What a binding run does with a unit's existing record, and why.

    A record from another run (code, rules, request, schema, binder, source run) refuses the
    whole run. A failed record, a failed call, or an arm V record that changed since is run
    again. Anything else is reused, so a rerun asks only where it has no usable answer.
    """
    if existing is None:
        return "run", "no record yet"
    binding = (existing.get("result") or {}).get("binding")
    recorded = binding.get("run_identity") if binding else existing.get("run_identity")
    if recorded is not None and recorded != dict(identity):
        return "refuse", "a record of another run"
    if binding is None:
        return "run", "the unit failed before"
    if binding.get("outcome") == Outcome.CALL_FAILED:
        return "run", "the binding call failed before"
    if binding.get("source_record_sha256") != source_sha256:
        return "run", "the arm V record changed since"
    return "reuse", ""


def write_plan(directory: Path, unit_ids: Sequence[str], identity: Mapping[str, Any]) -> None:
    """Record the planned units, so replay and tallies ignore records outside the plan."""
    directory.mkdir(parents=True, exist_ok=True)
    plan = {"arm": ARM, "run_identity": dict(identity), "units": list(unit_ids)}
    (directory / validated.PLAN).write_text(json.dumps(plan, indent=1) + "\n", encoding="utf-8")


def tally(records: Mapping[str, Mapping[str, Any] | None]) -> Counter[str]:
    """Planned units by state: ``not_run``, ``failed`` (no decision) or the binding outcome."""
    counts: Counter[str] = Counter()
    for stored in records.values():
        if stored is None:
            counts["not_run"] += 1
            continue
        binding = (stored.get("result") or {}).get("binding")
        counts[binding["outcome"] if binding else "failed"] += 1
    return counts


class ReplayStatus(StrEnum):
    IDENTICAL = "identical"
    DIFFERS = "differs"
    RULES_CHANGED = "rules_changed"
    INPUT_CHANGED = "input_changed"


def replay_status(stored: Mapping[str, Any], knowledge: KnowledgeBase) -> ReplayStatus:
    """Re-decide a bound record from its recorded reply, with no provider.

    A record made under another rules version, request or schema is ``rules_changed``; one
    whose binding input (context or photographs) no longer matches what the record holds is
    ``input_changed``. Neither is re-decided as if it were current.
    """
    result = stored["result"]
    binding = result["binding"]
    if (binding["rules_version"], binding["request_sha256"], binding["schema_sha256"]) != (
        RULES_VERSION,
        REQUEST_SHA256,
        SCHEMA_SHA256,
    ):
        return ReplayStatus.RULES_CHANGED
    answer, packet, image_ids, photos = inputs_of(stored)
    reply = BindingReply.model_validate(binding["reply"]) if binding["reply"] else None
    bound = decide_bound(
        answer,
        packet,
        knowledge,
        evidence_sufficient=bool(result[validated.ARM]["evidence_sufficient"]),
        reply=reply,
        image_ids=image_ids,
    )
    if bound.ineligible is None and binding["input_sha256"] is not None:
        current = input_sha256(context(answer, packet, image_ids), photos)
        if current != binding["input_sha256"]:
            return ReplayStatus.INPUT_CHANGED
    outcome = Outcome.CALL_FAILED if binding["error"] else bound.outcome
    same = (
        [d.model_dump(mode="json") for d in bound.decisions] == result["response"]["decisions"]
        and outcome.value == binding["outcome"]
        and bound.subject_id == binding["subject_id"]
    )
    return ReplayStatus.IDENTICAL if same else ReplayStatus.DIFFERS
