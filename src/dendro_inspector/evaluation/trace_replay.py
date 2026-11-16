"""Replay a stored run's evidence through today's deterministic decision logic.

A live run records, in its trace, every observation the extractor produced and every
candidate the generator proposed together with what admission did to it. That is enough to
ask a narrow question offline and for free: *given exactly that evidence and those
proposals, what does the current code decide?*

The replay scripts the fake provider with the recorded packet and proposals. Reviewers are
replayed one of two ways:

* ``neutral`` (the default) gives every reviewer a pass with no findings. It measures the
  deterministic layer — admission, card matching, tiers, ceilings, the claim cap — and
  nothing a reviewer model added. That is the layer a knowledge or admission change touches,
  which makes it the right before/after instrument for one, and the wrong instrument for
  judging a model.
* ``recorded`` gives every reviewer the review it returned in the live run, read from the
  trace's ``review_rounds``. It measures what today's adjudication and decision rules make
  of those same reviews, which is the instrument for a change to how review binds a
  verdict. A trace written before reviews were recorded cannot be replayed this way, and
  the replay says so rather than substituting neutral reviewers.

What a trace does not carry is reconstructed conservatively and reported by
:func:`replay_gaps`, never invented:

* Subject kinds are read from ``evidence_context`` where the trace has it. Older traces do
  not record them: they are inferred from the subject id's wording, and
  :func:`replay_fidelity` checks that the rebuilt packet assigns every observation the trust
  and tier the live run recorded. A record whose trust does not reproduce is flagged.
* Evidence the uploader vouched for (``user_attested``) is re-derived rather than copied.
  A trace with evidence history replays each statement from its recorded outcome, on the
  raw packet. Without history the reading is replayed as ``unknown`` on the tree, because
  the subject and status it was filed under were not recorded. Readings credited by the
  earlier field-context rule, which needed no statement, replay uncredited;
  :func:`replay_gaps` and :func:`replay_fidelity` report them.
* Inferences are replayed where the trace has them. From an older trace, a proposal that
  cited an inference id loses that id.
* Older traces omit wood surfaces. They replay as ``unknown``, never as prepared end grain;
  any resulting trust or tier change is reported by :func:`replay_fidelity`.
* A rejected proposal's resolution and score are not recorded. Resolution is read from the
  taxon id's shape and the score is taken as ``weak``; admission recomputes both anyway.
* Without evidence history, only the last internal review round is replayed. A retried run
  uses that round's reviews on every pass, so the earlier retry route is not reconstructed.
* The case's own inputs — declared object type, location, the user's claim and note — are
  not in the trace. A record that used any of them replays without it.
* A reviewer whose model a review plan did not call (``model_called: false``) left no
  response. A replay that reaches it with evidence history reports the route as unavailable
  rather than scripting a pass. The other direction is allowed: a replay run with adaptive
  routing switched on may leave recorded reviews unread, which is the route under test.

Schema-3 traces also carry complete raw and effective packets by extraction revision. The
audit reads the final effective artifact, while replay gives the fake extractor its original
raw packet and the generator its original proposal. This preserves notes, parent/component
attribution and rejected candidates without guessing them from the compact ledger. The
``recorded`` mode starts at the first extraction and consumes each revision's proposals
and reviews in sequence, bound to its graph retry index. Missing responses and divergent
routes fail explicitly. ``neutral`` mode still starts at the final extraction. Neither
mode reproduces the original case inputs or unsuccessful provider repair attempts.
"""

from __future__ import annotations

import json
import tempfile
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from dendro_inspector.config import Adapter, AppConfig, ProviderConfig, Role
from dendro_inspector.knowledge.evidence_hierarchy import project_observation
from dendro_inspector.observability.events import LEGACY_TRACE_SCHEMA_VERSION
from dendro_inspector.observability.evidence_history import EvidenceRevision
from dendro_inspector.providers.base import GRAPH_RETRY_INDEX
from dendro_inspector.providers.fake import UnscriptedCallError
from dendro_inspector.schemas.evidence import (
    AttachmentStatus,
    EvidencePacket,
    SubjectKind,
    WoodSurface,
    requires_wood_surface,
)
from dendro_inspector.schemas.input import (
    AttestationSource,
    CaseInput,
    ImageRef,
    PartAttestation,
)

#: Subject-id words that name a kind. Checked in order; the first hit wins.
_KIND_WORDS: tuple[tuple[str, SubjectKind], ...] = (
    ("log", SubjectKind.LOG),
    ("split", SubjectKind.SPLIT_WOOD),
    ("wood", SubjectKind.WOOD_SURFACE),
    ("bark", SubjectKind.BARK_SURFACE),
    ("branch", SubjectKind.BRANCH),
    ("shoot", SubjectKind.BRANCH),
    ("twig", SubjectKind.BRANCH),
    ("cone", SubjectKind.DETACHED_PART),
    ("fruit", SubjectKind.DETACHED_PART),
    ("nut", SubjectKind.DETACHED_PART),
    ("leaf", SubjectKind.DETACHED_PART),
    ("tree", SubjectKind.STANDING_TREE),
    ("trunk", SubjectKind.STANDING_TREE),
    ("crown", SubjectKind.STANDING_TREE),
)

_OBSERVATION_FIELDS = (
    "observation_id",
    "subject_id",
    "feature",
    "value",
    "source",
    "image_id",
    "visibility",
    "reliability",
    "attachment",
    "wood_surface",
)

_NEUTRAL_REVIEWS = {
    "reviewer:botanical_reviewer": {"reviewer": "botanical", "status": "pass", "findings": []},
    "reviewer:confusion_reviewer": {"reviewer": "confusion", "status": "pass", "findings": []},
    "reviewer:confidence_reviewer": {"reviewer": "confidence", "status": "pass", "findings": []},
    "arbiter:arbiter": {"reviewer": "arbiter", "status": "pass", "findings": []},
}

#: Which fake-provider key answers for each recorded reviewer.
_REVIEW_KEYS = {
    "botanical": "reviewer:botanical_reviewer",
    "confusion": "reviewer:confusion_reviewer",
    "confidence": "reviewer:confidence_reviewer",
    "arbiter": "arbiter:arbiter",
}

#: How a replay answers for the reviewers; see the module docstring.
ReviewReplay = Literal["neutral", "recorded"]

#: Marks something a replay had to do without because the trace predates its recording.
MISSING_FROM_LEGACY_TRACE = "missing_from_legacy_trace"


class ReplayUnavailableError(ValueError):
    """The trace lacks what the requested replay needs, and nothing may stand in for it."""


@dataclass(frozen=True, slots=True)
class StoredRecord:
    """One stored run, reduced to what a replay and an audit need."""

    case_id: str
    source: str
    trace: Mapping[str, Any]
    decisions: tuple[Mapping[str, Any], ...]
    field_context: bool


def load_record(path: Path) -> StoredRecord | None:
    """Read a ``*.full.json`` case record, or ``None`` when it carries no trace."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    result = payload.get("result") if isinstance(payload, dict) else None
    trace = result.get("trace") if isinstance(result, dict) else None
    if not isinstance(result, dict) or not isinstance(trace, dict):
        return None
    if "evidence_observations" not in trace:
        return None
    response = result.get("response") or {}
    upload = payload.get("upload") or {}
    return StoredRecord(
        case_id=str(payload.get("case_id") or trace.get("case_id") or path.stem),
        source=str(path),
        trace=trace,
        decisions=tuple(response.get("decisions") or ()),
        field_context=bool(upload.get("field_context")),
    )


def subject_kind(subject_id: str) -> SubjectKind:
    """The kind a subject id's wording names, or ``unknown``."""
    lowered = subject_id.lower()
    for word, kind in _KIND_WORDS:
        if word in lowered:
            return kind
    return SubjectKind.UNKNOWN


def _observations(trace: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for stored in trace.get("evidence_observations") or ():
        row = {key: stored.get(key) for key in _OBSERVATION_FIELDS}
        if requires_wood_surface(str(row["feature"])) and row["wood_surface"] is None:
            # Legacy records lack this field. Match Observation's conservative fallback
            # explicitly, since the fake extractor validates GeneratedObservation.
            row["wood_surface"] = WoodSurface.UNKNOWN.value
        if row.get("attachment") == AttachmentStatus.USER_ATTESTED.value:
            # Only code sets this, after extraction, and only from an explicit statement.
            # The model-side status it replaced was not recorded.
            row["attachment"] = AttachmentStatus.UNKNOWN.value
        rows.append({key: value for key, value in row.items() if value is not None})
    return rows


def trace_schema_version(trace: Mapping[str, Any]) -> int:
    """The trace's own shape version; traces written before the field existed are 1."""
    return int(trace.get("trace_schema_version") or LEGACY_TRACE_SCHEMA_VERSION)


def _subjects(
    observations: Sequence[Mapping[str, Any]], trace: Mapping[str, Any] | None = None
) -> list[dict[str, Any]]:
    """The packet's subjects: as recorded where the trace has them, else rebuilt.

    Recorded subjects include the ones nothing was observed on, each of which still gets a
    verdict of its own. Rebuilding from observations cannot see those, and has to guess
    every kind from the wording of its id.
    """
    recorded = ((trace or {}).get("evidence_context") or {}).get("subjects")
    if recorded:
        return [
            {
                "subject_id": subject["subject_id"],
                "kind": subject["kind"],
                "image_ids": list(subject.get("image_ids") or ()),
            }
            for subject in recorded
        ]
    images: dict[str, list[str]] = {}
    for observation in observations:
        bucket = images.setdefault(str(observation["subject_id"]), [])
        image = observation.get("image_id")
        if image and image not in bucket:
            bucket.append(str(image))
    return [
        {"subject_id": subject, "kind": subject_kind(subject).value, "image_ids": image_ids}
        for subject, image_ids in images.items()
    ]


def _packet_payload(trace: Mapping[str, Any]) -> dict[str, Any]:
    """The recorded packet as the extractor would return it, with what the trace kept."""
    observations = _observations(trace)
    payload: dict[str, Any] = {
        "subjects": _subjects(observations, trace),
        "observations": observations,
    }
    context = trace.get("evidence_context")
    if context:
        payload.update(
            inferences=[dict(inference) for inference in context.get("inferences") or ()],
            absent_features=list(context.get("absent_features") or ()),
            context_limitations=list(context.get("context_limitations") or ()),
            image_limitations=[
                {key: value for key, value in limitation.items() if value is not None}
                for limitation in context.get("image_limitations") or ()
            ],
            possible_multiple_taxa=bool(context.get("possible_multiple_taxa")),
            instruction_like_content_detected=bool(
                context.get("instruction_like_content_detected")
            ),
        )
    return payload


def evidence_history_from_trace(trace: Mapping[str, Any]) -> tuple[EvidenceRevision, ...]:
    """Read ordered artifacts and verify content hashes before trusting their contents."""
    history = tuple(
        EvidenceRevision.model_validate(row) for row in trace.get("evidence_history") or ()
    )
    if any(item.revision != index for index, item in enumerate(history, start=1)):
        raise ReplayUnavailableError("evidence_history: revisions must be contiguous and ordered")
    return history


def packet_from_trace(trace: Mapping[str, Any]) -> EvidencePacket:
    """The recorded evidence as a packet. Inferences only where the trace recorded them."""
    history = evidence_history_from_trace(trace)
    if history:
        return history[-1].effective
    return EvidencePacket.model_validate(_packet_payload(trace))


def _original_score(taxon: str, admitted_score: str, demoted: Sequence[str]) -> str:
    for entry in demoted:
        name, _, change = str(entry).partition(":")
        if name == taxon and "->" in change:
            return change.split("->", 1)[0]
    return admitted_score


def proposals_from_trace(trace: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The generator's proposals, rebuilt from admitted and dropped support."""
    history = evidence_history_from_trace(trace)
    if history:
        proposal = history[-1].candidate_proposal
        if proposal is not None:
            return [group.model_dump(mode="json") for group in proposal.sets]
    sets = []
    for validation in trace.get("candidate_validations") or ():
        admitted = {item["taxon"]: item for item in validation.get("admitted") or ()}
        dropped: dict[str, list[str]] = {}
        for detail in validation.get("dropped_support") or ():
            dropped.setdefault(detail["taxon"], []).append(detail["evidence_id"])
        candidates: list[dict[str, Any]] = []
        for taxon in validation.get("proposed_taxa") or ():
            item = admitted.get(taxon)
            support = [*(item or {}).get("supporting_evidence_ids", ()), *dropped.get(taxon, ())]
            candidates.append(
                {
                    "taxon": taxon,
                    "resolution": item["resolution"]
                    if item
                    else ("species" if "_" in taxon else "genus"),
                    "supporting_evidence_ids": list(dict.fromkeys(support)),
                    "contradicting_evidence_ids": list(
                        (item or {}).get("contradicting_evidence_ids", ())
                    ),
                    "missing_decisive_features": [],
                    "score": _original_score(
                        taxon,
                        item["score"] if item else "weak",
                        validation.get("demoted_scores") or (),
                    ),
                    "rank": len(candidates) + 1,
                }
            )
        if candidates:
            sets.append({"subject_id": validation["subject_id"], "candidates": candidates})
    return sets


def _review_response(reviewer: Mapping[str, Any]) -> dict[str, Any]:
    """One recorded review as the reviewer model returned it.

    Only the model's own findings go back in. The code-raised ones were merged into the
    review after the call, and the replayed node raises them again from the same evidence;
    scripting them as model output would file them twice, once under the wrong origin. A
    finding returns with the subject the reviewer named, since adjudication resolves scope
    itself. The recorded status is the one after that merge, which a second merge leaves as
    it is.
    """
    response: dict[str, Any] = {
        "reviewer": reviewer["reviewer"],
        "status": reviewer["status"],
        "findings": [
            {
                key: value
                for key, value in {
                    "finding_id": finding["finding_id"],
                    "category": finding["category"],
                    "severity": finding["severity"],
                    "summary": finding["summary"],
                    "evidence_ids": list(finding.get("evidence_ids") or ()),
                    "subject_id": finding.get("raised_subject_id"),
                    "proposed_taxon": finding.get("proposed_taxon"),
                    "required_action": finding["required_action"],
                    "impact": finding["impact"],
                }.items()
                if value is not None
            }
            for finding in reviewer.get("findings") or ()
            if finding.get("origin") == "model"
        ],
        "recommended_candidates": [
            {
                "taxon": candidate["taxon"],
                "resolution": candidate["resolution"],
                "supporting_evidence_ids": list(candidate.get("supporting_evidence_ids") or ()),
                "contradicting_evidence_ids": list(
                    candidate.get("contradicting_evidence_ids") or ()
                ),
                "missing_decisive_features": list(candidate.get("missing_decisive_features") or ()),
                "score": candidate["score"],
                "rank": candidate["rank"],
            }
            for candidate in reviewer.get("recommended_candidates") or ()
        ],
    }
    for key in ("recommended_resolution", "recommended_confidence", "subject_id"):
        if reviewer.get(key) is not None:
            response[key] = reviewer[key]
    return response


def _replayed_rounds(trace: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    """The rounds a replay scripts: the last internal one, then the arbiter's."""
    rounds = tuple(trace.get("review_rounds") or ())
    internal = [item for item in rounds if item.get("stage") == "internal"]
    arbiter = [item for item in rounds if item.get("stage") == "arbiter"]
    last = max(internal, key=lambda item: int(item.get("round") or 0), default=None)
    return tuple(item for item in (last, arbiter[-1] if arbiter else None) if item is not None)


def reviews_from_trace(trace: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Fake-provider responses for the reviewers, from the reviews the trace recorded.

    A reviewer the run never called keeps its neutral pass: a replay that reaches it under a
    different policy has nothing recorded to give it, and :func:`replay_gaps` says which.
    """
    if trace_schema_version(trace) <= LEGACY_TRACE_SCHEMA_VERSION:
        msg = f"review_rounds: {MISSING_FROM_LEGACY_TRACE}"
        raise ReplayUnavailableError(msg)
    responses: dict[str, dict[str, Any]] = {
        key: dict(response) for key, response in _NEUTRAL_REVIEWS.items()
    }
    for recorded in _replayed_rounds(trace):
        for reviewer in recorded.get("reviewers") or ():
            key = _REVIEW_KEYS.get(str(reviewer.get("reviewer")))
            if key is not None and _model_called(reviewer):
                responses[key] = _review_response(reviewer)
    return responses


def _model_called(reviewer: Mapping[str, Any]) -> bool:
    """Whether a recorded review is a model's answer. Absent in older traces, where it is.

    A review plan can leave a reviewer's model uncalled; its record then holds what its code
    raised and nothing a model said. Scripting that as the model's response would invent a
    pass nobody gave.
    """
    return bool(reviewer.get("model_called", True))


def _response_sequences(
    trace: Mapping[str, Any], history: tuple[EvidenceRevision, ...]
) -> dict[str, list[dict[str, Any]]]:
    """Bind every recorded model response to the graph pass that produced it.

    Empty sequences deliberately reject reviewers or candidate calls the recording never
    reached. They must not acquire neutral stand-ins when a policy change alters routing.
    """
    sequences: dict[str, list[dict[str, Any]]] = {
        key: []
        for key in (
            "primary:evidence_extractor",
            "primary:candidate_generator",
            *_REVIEW_KEYS.values(),
        )
    }
    for index, revision in enumerate(history):
        if revision.retry_index != index:
            raise ReplayUnavailableError("evidence_history: graph retry indices must start at zero")
        if revision.raw is None:
            raise ReplayUnavailableError(
                f"raw_evidence: revision {revision.revision} was not recorded"
            )
        metadata = {GRAPH_RETRY_INDEX: revision.retry_index}
        sequences["primary:evidence_extractor"].append(
            {"response": revision.raw.model_dump(mode="json"), "metadata": metadata}
        )
        if revision.candidate_proposal is not None:
            sequences["primary:candidate_generator"].append(
                {
                    "response": revision.candidate_proposal.model_dump(mode="json"),
                    "metadata": metadata,
                }
            )

    seen: set[tuple[int, str]] = set()
    previous = 0
    for recorded in trace.get("review_rounds") or ():
        revision_id = recorded.get("evidence_revision")
        if not isinstance(revision_id, int) or not 1 <= revision_id <= len(history):
            raise ReplayUnavailableError("review_rounds: missing or unknown evidence revision")
        if revision_id < previous:
            raise ReplayUnavailableError("review_rounds: evidence revisions are out of order")
        previous = revision_id
        revision = history[revision_id - 1]
        if revision.candidate_proposal is None:
            raise ReplayUnavailableError(
                f"candidate_proposal: revision {revision_id} was not recorded"
            )
        for reviewer in recorded.get("reviewers") or ():
            name = str(reviewer.get("reviewer"))
            key = _REVIEW_KEYS.get(name)
            if key is None or (revision_id, key) in seen:
                raise ReplayUnavailableError("review_rounds: unknown or repeated reviewer")
            stage = "arbiter" if name == "arbiter" else "internal"
            if recorded.get("stage") != stage:
                raise ReplayUnavailableError("review_rounds: reviewer is bound to the wrong stage")
            if reviewer.get("findings_omitted"):
                raise ReplayUnavailableError(
                    f"review:{name}: revision {revision_id} has omitted findings"
                )
            seen.add((revision_id, key))
            if not _model_called(reviewer):
                # Nothing was returned, so nothing is scripted: a replay that reaches this
                # reviewer finds no response and reports that it cannot follow this route.
                continue
            sequences[key].append(
                {
                    "response": _review_response(reviewer),
                    "metadata": {GRAPH_RETRY_INDEX: revision.retry_index},
                }
            )
    return sequences


def _credited_without_statement(
    trace: Mapping[str, Any], history: Sequence[EvidenceRevision]
) -> bool:
    """Whether the run credited a reading that no recorded statement accounts for."""
    if any(revision.attestations for revision in history):
        return False
    return any(
        row.get("attachment") == AttachmentStatus.USER_ATTESTED.value
        for row in trace.get("evidence_observations") or ()
    )


def _recorded_attestations(history: Sequence[EvidenceRevision]) -> tuple[PartAttestation, ...]:
    """The uploader's statements, rebuilt from the outcomes the run recorded.

    Every extraction of a run receives the same statements, so the first revision that
    recorded any is the whole set. The author's own words are not in the trace.
    """
    for revision in history:
        if revision.attestations:
            return tuple(
                PartAttestation(
                    attestation_id=outcome.attestation_id,
                    part_image_ids=outcome.part_image_ids,
                    tree_image_ids=outcome.tree_image_ids,
                    source=AttestationSource(outcome.source),
                )
                for outcome in revision.attestations
            )
    return ()


def replay_gaps(trace: Mapping[str, Any], reviews: ReviewReplay = "neutral") -> tuple[str, ...]:
    """What this replay reconstructs or omits, as ``what: why`` entries. Empty when nothing."""
    gaps: list[str] = []
    history = evidence_history_from_trace(trace)
    if history:
        gaps.append("case_inputs: original user context and image files are not recorded")
        if history[-1].raw is None and reviews != "recorded":
            gaps.append("raw_evidence: not recorded; reconstructed from the final ledger")
    if _credited_without_statement(trace, history):
        gaps.append(
            "field_attestation: readings credited by the earlier field-context rule "
            "replay uncredited"
        )
    legacy = trace_schema_version(trace) <= LEGACY_TRACE_SCHEMA_VERSION
    if legacy:
        gaps.extend(
            f"{what}: {MISSING_FROM_LEGACY_TRACE}"
            for what in ("subject_kinds", "inferences", "packet_flags", "plan")
        )
    if reviews != "recorded":
        if len(history) > 1:
            gaps.append("evidence_history: neutral replay uses only the final extraction")
        return tuple(gaps)
    if legacy:
        gaps.append(f"review_rounds: {MISSING_FROM_LEGACY_TRACE}")
        return tuple(gaps)
    if history:
        try:
            _response_sequences(trace, history)
        except ReplayUnavailableError as exc:
            gaps.append(str(exc))
        if any(
            call.get("validation_failures")
            for event in trace.get("events") or ()
            for call in event.get("provider_calls") or ()
        ):
            gaps.append("provider_repairs: unsuccessful responses were not recorded")
        return tuple(gaps)
    rounds = tuple(trace.get("review_rounds") or ())
    replayed = _replayed_rounds(trace)
    internal_rounds = sum(1 for item in rounds if item.get("stage") == "internal")
    if internal_rounds > 1:
        gaps.append(f"review_rounds: only the last of {internal_rounds} internal rounds replays")
    called = {
        str(r.get("reviewer"))
        for item in replayed
        for r in item.get("reviewers") or ()
        if _model_called(r)
    }
    gaps.extend(
        f"review:{reviewer}: not called in the recorded run; neutral if this replay calls it"
        for reviewer in _REVIEW_KEYS
        if rounds and reviewer not in called
    )
    gaps.extend(
        f"review:{reviewer.get('reviewer')}: {reviewer['findings_omitted']} findings omitted"
        for item in replayed
        for reviewer in item.get("reviewers") or ()
        if reviewer.get("findings_omitted")
    )
    return tuple(gaps)


def fixture_from_trace(
    trace: Mapping[str, Any], *, reviews: ReviewReplay = "neutral"
) -> dict[str, Any]:
    """A fake-provider scenario that replays this trace; see the module docstring."""
    packet = _packet_payload(trace)
    history = evidence_history_from_trace(trace)
    if history:
        raw = history[-1].raw
        if raw is not None:
            packet = raw.model_dump(mode="json")
    plan = trace.get("plan") or {}
    fixture: dict[str, Any] = {
        "scenario": "trace-replay",
        "description": f"Recorded evidence and proposals, replayed with {reviews} reviewers.",
        "responses": {
            "primary:planner": {
                "target_features": list(plan.get("target_features") or ()),
                "expect_multiple_subjects": bool(
                    plan.get(
                        "expect_multiple_subjects",
                        len({o["subject_id"] for o in packet["observations"]}) > 1,
                    )
                ),
                "bark_only_input": bool(plan.get("bark_only_input", False)),
                "split_firewood_input": bool(plan.get("split_firewood_input", False)),
                "notes": [],
            },
            "primary:evidence_extractor": packet,
            "primary:candidate_generator": {"sets": proposals_from_trace(trace)},
            **(reviews_from_trace(trace) if reviews == "recorded" else _NEUTRAL_REVIEWS),
        },
    }
    if reviews == "recorded" and history:
        fixture["response_sequences"] = _response_sequences(trace, history)
        # Keep only the one-time planner response. Every other call must consume history.
        fixture["responses"] = {"primary:planner": fixture["responses"]["primary:planner"]}
    return fixture


def replay_fidelity(record: StoredRecord) -> tuple[str, ...]:
    """Observations whose rebuilt trust or tier differs from what the live run recorded."""
    packet = packet_from_trace(record.trace)
    stored = {row["observation_id"]: row for row in record.trace["evidence_observations"]}
    mismatches = []
    for observation in packet.observations:
        projection = project_observation(observation)
        row = stored.get(observation.observation_id)
        if (
            row is None
            or projection.trust.name.lower() != row.get("trust")
            or int(projection.tier) != row.get("tier")
        ):
            mismatches.append(observation.observation_id)
    return tuple(mismatches)


def _case_input(record: StoredRecord) -> CaseInput:
    context = record.trace.get("evidence_context") or {}
    referenced = {
        str(row["image_id"]) for row in record.trace["evidence_observations"] if row.get("image_id")
    }
    # A subject or a limitation may name a photograph nothing was observed on, and the
    # extractor refuses a packet that references an image the case does not hold.
    referenced.update(
        str(image_id)
        for subject in context.get("subjects") or ()
        for image_id in subject.get("image_ids") or ()
    )
    referenced.update(
        str(limitation["image_id"]) for limitation in context.get("image_limitations") or ()
    )
    history = evidence_history_from_trace(record.trace)
    for revision in history:
        raw = revision.raw
        if raw is not None:
            referenced.update(o.image_id for o in raw.observations if o.image_id is not None)
            referenced.update(image for subject in raw.subjects for image in subject.image_ids)
            referenced.update(limitation.image_id for limitation in raw.image_limitations)
    attestations = _recorded_attestations(history)
    for attestation in attestations:
        referenced.update(attestation.part_image_ids)
        referenced.update(attestation.tree_image_ids)
    images = sorted(referenced) or ["img-1"]
    return CaseInput(
        case_id="trace-replay",
        images=tuple(ImageRef(image_id=image, path=Path(f"{image}.jpg")) for image in images),
        user_has_field_context=record.field_context,
        part_attestations=attestations,
    )


async def replay(
    record: StoredRecord,
    *,
    config: AppConfig,
    root: Path,
    reviews: ReviewReplay = "neutral",
) -> Any:
    """Run the graph on the recorded evidence; returns the final graph state.

    Raises :class:`ReplayUnavailableError` for legacy traces without recorded reviews,
    incomplete history, or a graph route that cannot consume the recorded responses.
    """
    from dendro_inspector.runner import run_case

    fixture = fixture_from_trace(record.trace, reviews=reviews)
    with tempfile.TemporaryDirectory() as directory:
        fixtures = Path(directory)
        (fixtures / "trace-replay.json").write_text(json.dumps(fixture), encoding="utf-8")
        fake = ProviderConfig(adapter=Adapter.FAKE, scenario="trace-replay")
        replay_config = config.model_copy(
            update={
                "providers": {Role.PRIMARY: fake, Role.REVIEWER: fake, Role.ARBITER: fake},
                "fixtures_root": fixtures,
            }
        )
        try:
            result = await run_case(_case_input(record), config=replay_config, root=root)
        except UnscriptedCallError as exc:
            if "response_sequences" not in fixture:
                raise
            raise ReplayUnavailableError(f"replay_route: {exc}") from exc
    if "response_sequences" in fixture:
        consumed = Counter(
            f"{call.role}:{call.node}"
            for event in result.trace.events
            for call in event.provider_calls
        )
        # A review plan this replay applied may leave a recorded review unread. That is the
        # route under test, not a divergence; every other unread response still is one.
        skipped = Counter(
            _REVIEW_KEYS[reviewer.value]
            for plan in result.trace.routing_history
            if plan.applied
            for reviewer in plan.skipped_reviewers
        )
        for key, sequence in fixture["response_sequences"].items():
            if len(sequence) - consumed[key] > skipped[key]:
                raise ReplayUnavailableError(
                    f"replay_route: consumed {consumed[key]} of {len(sequence)} responses for {key}"
                )
    return result.state
