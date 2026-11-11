"""Replay a stored run's evidence through today's deterministic decision logic.

A live run records, in its trace, every observation the extractor produced and every
candidate the generator proposed together with what admission did to it. That is enough to
ask a narrow question offline and for free: *given exactly that evidence and those
proposals, what does the current code decide?*

The replay scripts the fake provider with the recorded packet and proposals, and gives every
reviewer a neutral pass with no findings. It therefore measures the deterministic layer —
admission, card matching, tiers, ceilings, the claim cap — and nothing a reviewer model
added. That is the layer a knowledge or admission change touches, which is what makes it the
right before/after instrument for one, and the wrong instrument for judging a model.

What a trace does not carry is reconstructed conservatively and reported, never invented:

* Subject kinds are not recorded. They are inferred from the subject id's wording, and
  :func:`replay_fidelity` checks that the rebuilt packet assigns every observation the trust
  and tier the live run recorded. A record whose trust does not reproduce is flagged.
* Evidence the uploader vouched for (``user_attested``) is replayed as ``unknown`` with the
  record's field-context flag, so attestation is re-derived rather than copied. The subject
  it was originally filed under is not recorded, so it is replayed on the tree.
* Inferences are not recorded, so a proposal that cited an inference id loses that id.
* A rejected proposal's resolution and score are not recorded. Resolution is read from the
  taxon id's shape and the score is taken as ``weak``; admission recomputes both anyway.
"""

from __future__ import annotations

import json
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dendro_inspector.config import Adapter, AppConfig, ProviderConfig, Role
from dendro_inspector.knowledge.evidence_hierarchy import project_observation
from dendro_inspector.schemas.evidence import AttachmentStatus, EvidencePacket, SubjectKind
from dendro_inspector.schemas.input import CaseInput, ImageRef

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
)

_NEUTRAL_REVIEWS = {
    "reviewer:botanical_reviewer": {"reviewer": "botanical", "status": "pass", "findings": []},
    "reviewer:confusion_reviewer": {"reviewer": "confusion", "status": "pass", "findings": []},
    "reviewer:confidence_reviewer": {"reviewer": "confidence", "status": "pass", "findings": []},
    "arbiter:arbiter": {"reviewer": "arbiter", "status": "pass", "findings": []},
}


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
        if row.get("attachment") == AttachmentStatus.USER_ATTESTED.value:
            # Only code sets this, after extraction; replaying the field-context flag
            # re-applies it. The model-side status it replaced was not recorded.
            row["attachment"] = AttachmentStatus.UNKNOWN.value
        rows.append({key: value for key, value in row.items() if value is not None})
    return rows


def _subjects(observations: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
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


def packet_from_trace(trace: Mapping[str, Any]) -> EvidencePacket:
    """The recorded observations as an evidence packet (no inferences: none are stored)."""
    observations = _observations(trace)
    return EvidencePacket.model_validate(
        {"subjects": _subjects(observations), "observations": observations}
    )


def _original_score(taxon: str, admitted_score: str, demoted: Sequence[str]) -> str:
    for entry in demoted:
        name, _, change = str(entry).partition(":")
        if name == taxon and "->" in change:
            return change.split("->", 1)[0]
    return admitted_score


def proposals_from_trace(trace: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The generator's proposals, rebuilt from admitted and dropped support."""
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


def fixture_from_trace(trace: Mapping[str, Any]) -> dict[str, Any]:
    """A fake-provider scenario that replays this trace with neutral reviewers."""
    observations = _observations(trace)
    return {
        "scenario": "trace-replay",
        "description": "Recorded evidence and proposals, replayed with neutral reviewers.",
        "responses": {
            "primary:planner": {
                "target_features": [],
                "expect_multiple_subjects": len({o["subject_id"] for o in observations}) > 1,
                "bark_only_input": False,
                "notes": [],
            },
            "primary:evidence_extractor": {
                "subjects": _subjects(observations),
                "observations": observations,
            },
            "primary:candidate_generator": {"sets": proposals_from_trace(trace)},
            **_NEUTRAL_REVIEWS,
        },
    }


def replay_fidelity(record: StoredRecord) -> tuple[str, ...]:
    """Observations whose rebuilt trust or tier differs from what the live run recorded."""
    packet = packet_from_trace(record.trace)
    stored = {row["observation_id"]: row for row in record.trace["evidence_observations"]}
    mismatches = []
    for observation in packet.observations:
        projection = project_observation(observation)
        row = stored[observation.observation_id]
        if projection.trust.name.lower() != row.get("trust") or int(projection.tier) != row.get(
            "tier"
        ):
            mismatches.append(observation.observation_id)
    return tuple(mismatches)


def _case_input(record: StoredRecord) -> CaseInput:
    images = sorted(
        {
            str(row["image_id"])
            for row in record.trace["evidence_observations"]
            if row.get("image_id")
        }
    ) or ["img-1"]
    return CaseInput(
        case_id="trace-replay",
        images=tuple(ImageRef(image_id=image, path=Path(f"{image}.jpg")) for image in images),
        user_has_field_context=record.field_context,
    )


async def replay(record: StoredRecord, *, config: AppConfig, root: Path) -> Any:
    """Run the graph on the recorded evidence; returns the final graph state."""
    from dendro_inspector.runner import run_case

    with tempfile.TemporaryDirectory() as directory:
        fixtures = Path(directory)
        (fixtures / "trace-replay.json").write_text(
            json.dumps(fixture_from_trace(record.trace)), encoding="utf-8"
        )
        fake = ProviderConfig(adapter=Adapter.FAKE, scenario="trace-replay")
        replay_config = config.model_copy(
            update={
                "providers": {Role.PRIMARY: fake, Role.REVIEWER: fake, Role.ARBITER: fake},
                "fixtures_root": fixtures,
            }
        )
        result = await run_case(_case_input(record), config=replay_config, root=root)
    return result.state
