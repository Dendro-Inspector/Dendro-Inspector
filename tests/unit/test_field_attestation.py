"""The uploader's explicit word that a photographed part belongs to a photographed tree.

Built on synthetic packets shaped like a common upload: a trunk, plus nuts and a leaf
picked up beneath it that the extractor recorded as a separate detached part.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import typer
from pydantic import ValidationError

from dendro_inspector.cli import _part_attestations
from dendro_inspector.evaluation.trace_replay import (
    StoredRecord,
    _case_input,
    replay_gaps,
)
from dendro_inspector.knowledge.evidence_hierarchy import EvidenceTrust, observation_trust
from dendro_inspector.nodes.field_attestation import (
    apply_part_attestations,
    attested_evidence_ids,
)
from dendro_inspector.observability.trace import TraceRecorder
from dendro_inspector.schemas.evidence import (
    AttachmentStatus,
    AttestationSkipReason,
    AttestationStatus,
    EvidencePacket,
    GeneratedObservation,
    Observation,
    ObservationSource,
    Reliability,
    Subject,
    SubjectKind,
    Visibility,
)
from dendro_inspector.schemas.input import (
    AttestationSource,
    CaseInput,
    ImageRef,
    PartAttestation,
)

REPO = Path(__file__).resolve().parents[2]
TREE = "tree_1"
LOOSE = "nuts_1"
IMAGES = ("img-1", "img-2", "img-3")


def _obs(oid: str, feature: str, value: str, subject: str, **extra: object) -> Observation:
    detachable = feature.split(".")[0] in {"leaf", "leaflet", "nut", "fruit", "cones"}
    payload: dict[str, object] = {
        "observation_id": oid,
        "feature": feature,
        "value": value,
        "subject_id": subject,
        "source": ObservationSource.IMAGE,
        "image_id": "img-1" if subject == TREE else "img-2",
        "visibility": Visibility.CLEAR,
        "reliability": Reliability.HIGH,
        "attachment": AttachmentStatus.CONFIRMED_DETACHED if detachable else None,
    }
    payload.update(extra)
    return Observation.model_validate(payload)


def _packet(*extra: Observation, subjects: tuple[Subject, ...] = ()) -> EvidencePacket:
    return EvidencePacket(
        subjects=(
            Subject(subject_id=TREE, kind=SubjectKind.STANDING_TREE, image_ids=("img-1",)),
            Subject(subject_id=LOOSE, kind=SubjectKind.DETACHED_PART, image_ids=("img-2",)),
            *subjects,
        ),
        observations=(
            _obs("obs-bark", "bark.pattern", "diamond_fissures", TREE),
            _obs("obs-nut", "nut.shell_surface", "deeply_corrugated_ridged", LOOSE),
            _obs("obs-leaf", "leaflet.count", "fifteen_to_twenty_three", LOOSE),
            *extra,
        ),
    )


def _statement(
    parts: tuple[str, ...] = ("img-2",), tree: tuple[str, ...] = ("img-1",), **extra: object
) -> PartAttestation:
    return PartAttestation.model_validate(
        {"attestation_id": "attest-1", "part_image_ids": parts, "tree_image_ids": tree, **extra}
    )


def _case(*statements: PartAttestation, field_context: bool = False) -> CaseInput:
    return CaseInput(
        case_id="case-1",
        images=tuple(ImageRef(image_id=i, path=Path(f"{i}.jpg")) for i in IMAGES),
        user_has_field_context=field_context,
        part_attestations=statements,
    )


class TestWhatCreditsNothing:
    def test_field_context_alone_moves_nothing(self):
        # The flag says the uploader knows the site. It does not say which part is whose.
        packet = _packet()
        attested, outcomes = apply_part_attestations(_case(field_context=True), packet)

        assert attested is packet
        assert outcomes == ()
        assert attested_evidence_ids(attested, TREE) == ()

    def test_several_photographs_together_are_not_a_statement(self):
        packet = _packet()
        assert apply_part_attestations(_case(), packet) == (packet, ())

    def test_a_photograph_the_statement_does_not_name_is_untouched(self):
        packet = _packet()
        attested, outcomes = apply_part_attestations(_case(_statement(parts=("img-3",))), packet)

        assert attested is packet
        assert outcomes[0].status is AttestationStatus.NOTHING_CREDITED
        assert outcomes[0].target_subject_id == TREE
        assert outcomes[0].skipped == ()


class TestAnExplicitStatement:
    def test_credits_the_named_loose_parts_to_the_named_tree(self):
        attested, _ = apply_part_attestations(_case(_statement()), _packet())

        by_id = {o.observation_id: o for o in attested.observations}
        assert by_id["obs-nut"].subject_id == TREE
        assert by_id["obs-nut"].attachment is AttachmentStatus.USER_ATTESTED
        assert by_id["obs-leaf"].attachment is AttachmentStatus.USER_ATTESTED
        assert by_id["obs-bark"].attachment is None
        assert attested_evidence_ids(attested, TREE) == ("obs-nut", "obs-leaf")

    def test_leaves_a_record_of_who_said_what_and_what_it_changed(self):
        statement = _statement(source=AttestationSource.OPERATOR, statement="nuts from under it")
        _, outcomes = apply_part_attestations(_case(statement), _packet())

        (outcome,) = outcomes
        assert outcome.attestation_id == "attest-1"
        assert outcome.source == "operator"
        assert outcome.part_image_ids == ("img-2",)
        assert outcome.tree_image_ids == ("img-1",)
        assert outcome.status is AttestationStatus.APPLIED
        assert outcome.target_subject_id == TREE
        assert [(c.observation_id, c.from_subject_id, c.read_as) for c in outcome.credited] == [
            ("obs-nut", LOOSE, AttachmentStatus.CONFIRMED_DETACHED),
            ("obs-leaf", LOOSE, AttachmentStatus.CONFIRMED_DETACHED),
        ]
        # The author's words stay with the case input.
        assert "nuts from under it" not in outcome.model_dump_json()

    def test_an_emptied_loose_subject_is_dropped_and_its_photos_go_to_the_tree(self):
        attested, _ = apply_part_attestations(_case(_statement()), _packet())

        assert [s.subject_id for s in attested.subjects] == [TREE]
        assert attested.subjects[0].image_ids == ("img-1", "img-2")

    def test_a_part_lying_on_the_tree_itself_is_credited_in_place(self):
        fallen = _obs("obs-on-bark", "leaf.shape", "pinnate", TREE)
        attested, outcomes = apply_part_attestations(
            _case(_statement(parts=("img-1",))), _packet(fallen)
        )

        by_id = {o.observation_id: o for o in attested.observations}
        assert by_id["obs-on-bark"].attachment is AttachmentStatus.USER_ATTESTED
        assert by_id["obs-on-bark"].subject_id == TREE
        assert [c.observation_id for c in outcomes[0].credited] == ["obs-on-bark"]
        # The nuts are in a photograph this statement does not name.
        assert by_id["obs-nut"].subject_id == LOOSE

    def test_attested_evidence_is_capped_support_not_decisive(self):
        attested, _ = apply_part_attestations(_case(_statement()), _packet())
        nut = next(o for o in attested.observations if o.observation_id == "obs-nut")
        assert observation_trust(nut) is EvidenceTrust.CAPPED_POSITIVE

        detached = next(o for o in _packet().observations if o.observation_id == "obs-nut")
        assert observation_trust(detached) is EvidenceTrust.CONTEXT_ONLY


class TestWhatAStatementCannotVouchFor:
    @pytest.mark.parametrize(
        ("extra", "reason"),
        [
            ({"visibility": Visibility.OBSCURED}, AttestationSkipReason.NOT_VISIBLE),
            (
                {"attachment": AttachmentStatus.UNKNOWN},
                AttestationSkipReason.ATTACHMENT_UNKNOWN,
            ),
            ({"reliability": Reliability.LOW}, AttestationSkipReason.LOW_RELIABILITY),
        ],
    )
    def test_an_unplaced_unseen_or_doubtful_reading_stays_where_it_was_filed(self, extra, reason):
        stray = _obs("obs-stray", "leaf.shape", "broad_ovate", LOOSE, **extra)
        attested, outcomes = apply_part_attestations(_case(_statement()), _packet(stray))

        kept = next(o for o in attested.observations if o.observation_id == "obs-stray")
        assert kept.subject_id == LOOSE
        assert kept.attachment is not AttachmentStatus.USER_ATTESTED
        assert observation_trust(kept) is EvidenceTrust.CONTEXT_ONLY
        assert [(s.observation_id, s.subject_id, s.reason) for s in outcomes[0].skipped] == [
            ("obs-stray", LOOSE, reason)
        ]
        # Still in the ledger, and its subject survives because something points at it.
        assert LOOSE in {s.subject_id for s in attested.subjects}

    def test_another_tree_or_a_log_is_never_folded_in(self):
        log = Subject(subject_id="log_1", kind=SubjectKind.LOG, image_ids=("img-2",))
        cones = _obs(
            "obs-cones",
            "cones.shape",
            "ovoid",
            "log_1",
            attachment=AttachmentStatus.CONFIRMED_ATTACHED,
        )
        attested, outcomes = apply_part_attestations(
            _case(_statement()), _packet(cones, subjects=(log,))
        )

        kept = next(o for o in attested.observations if o.observation_id == "obs-cones")
        assert kept.subject_id == "log_1"
        assert kept.attachment is AttachmentStatus.CONFIRMED_ATTACHED
        assert [(s.observation_id, s.reason) for s in outcomes[0].skipped] == [
            ("obs-cones", AttestationSkipReason.OTHER_IDENTITY)
        ]


class TestWhichTree:
    def _two_trees(self) -> EvidencePacket:
        other = Subject(subject_id="tree_2", kind=SubjectKind.STANDING_TREE, image_ids=("img-3",))
        return _packet(subjects=(other,))

    def test_the_statement_names_one_of_two_trees(self):
        attested, outcomes = apply_part_attestations(
            _case(_statement(tree=("img-3",))), self._two_trees()
        )

        assert outcomes[0].target_subject_id == "tree_2"
        assert attested_evidence_ids(attested, "tree_2") == ("obs-nut", "obs-leaf")
        assert attested_evidence_ids(attested, TREE) == ()

    def test_tree_photographs_showing_two_trees_name_neither(self):
        packet = self._two_trees()
        attested, outcomes = apply_part_attestations(
            _case(_statement(tree=("img-1", "img-3"))), packet
        )

        assert attested is packet
        assert outcomes[0].status is AttestationStatus.AMBIGUOUS_TREE
        assert outcomes[0].target_subject_id is None
        assert outcomes[0].credited == ()

    def test_tree_photographs_showing_no_tree_name_none(self):
        packet = _packet()
        attested, outcomes = apply_part_attestations(_case(_statement(tree=("img-2",))), packet)

        assert attested is packet
        assert outcomes[0].status is AttestationStatus.NO_TREE


class TestTheInputContract:
    def test_a_statement_must_name_photographs_of_this_case(self):
        with pytest.raises(ValidationError, match="does not hold"):
            _case(_statement(parts=("img-9",)))

    def test_statement_ids_are_unique_within_a_case(self):
        with pytest.raises(ValidationError, match="duplicate attestation_id"):
            _case(_statement(), _statement(parts=("img-3",)))

    def test_a_photograph_is_named_once_per_side(self):
        with pytest.raises(ValidationError, match="repeats an image id"):
            _statement(parts=("img-2", "img-2"))

    def test_a_model_cannot_claim_user_attested_itself(self):
        with pytest.raises(ValidationError, match="user_attested"):
            GeneratedObservation.model_validate(
                {
                    "observation_id": "obs-1",
                    "feature": "nut.shell_surface",
                    "value": "deeply_corrugated_ridged",
                    "subject_id": TREE,
                    "source": "image",
                    "image_id": "img-1",
                    "attachment": "user_attested",
                }
            )


class TestTheCommandLine:
    def test_photograph_numbers_follow_the_order_of_the_images(self):
        first, second = _part_attestations(["3,4=1", "2=2"], 4)

        assert first.attestation_id == "attest-1"
        assert first.part_image_ids == ("img-3", "img-4")
        assert first.tree_image_ids == ("img-1",)
        assert first.source is AttestationSource.UPLOADER
        assert (second.part_image_ids, second.tree_image_ids) == (("img-2",), ("img-2",))

    @pytest.mark.parametrize("spec", ["3", "=1", "3=", "0=1", "5=1", "a=1", "2,2=1"])
    def test_a_malformed_statement_is_a_usage_error(self, spec):
        with pytest.raises(typer.BadParameter, match="--attest-part"):
            _part_attestations([spec], 4)


def _worker() -> ModuleType:
    path = REPO / "scripts" / "batch" / "run_upload_case.py"
    spec = importlib.util.spec_from_file_location("_batch_upload_attestation", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class TestTheUploadManifest:
    PHOTOS = tuple({"file_name": f"photo-{n}.jpg"} for n in (1, 2, 3))

    def test_file_names_become_photograph_numbers(self):
        manifest = {
            "photos": self.PHOTOS,
            "part_attestations": [
                {"parts": ["photo-3.jpg", "photo-2.jpg"], "tree": ["photo-1.jpg"]}
            ],
        }
        assert _worker().attestation_args(manifest) == ["--attest-part", "3,2=1"]

    def test_field_context_alone_adds_no_statement(self):
        assert _worker().attestation_args({"photos": self.PHOTOS, "field_context": True}) == []

    @pytest.mark.parametrize(
        "entry",
        [{"parts": ["photo-9.jpg"], "tree": ["photo-1.jpg"]}, {"parts": ["photo-2.jpg"]}, "all"],
    )
    def test_a_statement_about_unknown_photographs_is_refused(self, entry):
        worker = _worker()
        with pytest.raises(worker.CaseError, match="part_attestations"):
            worker.attestation_args({"photos": self.PHOTOS, "part_attestations": [entry]})


class TestTheTrace:
    def _trace(self, case: CaseInput, packet: EvidencePacket) -> dict[str, Any]:
        attested, outcomes = apply_part_attestations(case, packet)
        recorder = TraceRecorder("attested")
        recorder.record_evidence(attested, attestations=outcomes)
        return recorder.build().model_dump(mode="json")

    def test_the_outcome_is_kept_with_the_revision_it_shaped(self):
        trace = self._trace(_case(_statement()), _packet())

        (outcome,) = trace["evidence_history"][0]["attestations"]
        assert outcome["status"] == "applied"
        assert outcome["target_subject_id"] == TREE
        assert [c["observation_id"] for c in outcome["credited"]] == ["obs-nut", "obs-leaf"]

    def test_replay_rebuilds_the_statement_from_the_trace_alone(self):
        trace = self._trace(_case(_statement()), _packet())
        case = _case_input(StoredRecord("attested", "inline", trace, (), False))

        (statement,) = case.part_attestations
        assert statement.attestation_id == "attest-1"
        assert statement.part_image_ids == ("img-2",)
        assert statement.tree_image_ids == ("img-1",)
        assert not any(gap.startswith("field_attestation:") for gap in replay_gaps(trace))

    def test_a_credit_from_the_earlier_rule_is_reported_as_a_replay_gap(self):
        # Written before statements existed: credited on the field-context flag alone.
        attested, _ = apply_part_attestations(_case(_statement()), _packet())
        recorder = TraceRecorder("earlier-rule")
        recorder.record_evidence(attested)
        trace = recorder.build().model_dump(mode="json")

        assert any(gap.startswith("field_attestation:") for gap in replay_gaps(trace))
        assert (
            _case_input(StoredRecord("earlier", "inline", trace, (), True)).part_attestations == ()
        )
