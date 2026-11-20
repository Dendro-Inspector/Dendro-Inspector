"""Explicit subject binding: a reply binds an answer to one organism only if code can verify it.

Constructed packets against the repository's cards (AGENTS.md section 16: non-golden tests);
the provider is always a scripted fake, and no test calls a model.
"""

from __future__ import annotations

import asyncio
import copy
import json
from typing import Any

import pydantic
import pytest

from dendro_inspector.evaluation import model_alone, subject_binding, validated
from dendro_inspector.observability.evidence_history import packet_digest
from dendro_inspector.providers.fake import ScriptedProvider
from dendro_inspector.schemas.evidence import (
    AttachmentStatus,
    EvidencePacket,
    Observation,
    ObservationSource,
    Reliability,
    Subject,
    SubjectKind,
    Visibility,
)

Outcome = subject_binding.Outcome
Reason = validated.AbstentionReason
ReplayStatus = subject_binding.ReplayStatus
IMAGES = ("img-1", "img-2", "img-3", "img-4")
ATTACHED = AttachmentStatus.CONFIRMED_ATTACHED


def _obs(
    observation_id: str,
    feature: str,
    value: str,
    subject: str,
    image: str,
    attachment: AttachmentStatus | None = None,
) -> Observation:
    return Observation(
        observation_id=observation_id,
        feature=feature,
        value=value,
        subject_id=subject,
        source=ObservationSource.IMAGE,
        image_id=image,
        visibility=Visibility.CLEAR,
        reliability=Reliability.HIGH,
        attachment=attachment,
    )


def _subject(subject_id: str, kind: SubjectKind, images: tuple[str, ...], parent=None) -> Subject:
    return Subject(subject_id=subject_id, kind=kind, image_ids=images, parent_subject_id=parent)


def _two_trees(*, b_leaf: bool = False, contradiction: bool = False) -> EvidencePacket:
    """tree_a (img-1, img-2) with a branch bearing palmate leaves; tree_b (img-2, img-3)."""
    subjects = [
        _subject("tree_a", SubjectKind.STANDING_TREE, ("img-1", "img-2")),
        _subject("branch_a", SubjectKind.BRANCH, ("img-1",), parent="tree_a"),
        _subject("tree_b", SubjectKind.STANDING_TREE, ("img-2", "img-3")),
    ]
    observations = [
        _obs("obs-a", "leaf.shape", "palmate_lobed", "branch_a", "img-1", ATTACHED),
        _obs("obs-b", "bark.texture", "smooth_grey", "tree_b", "img-3"),
    ]
    if b_leaf:
        observations.append(
            _obs("obs-b2", "leaf.shape", "palmate_lobed", "tree_b", "img-3", ATTACHED)
        )
    if contradiction:
        subjects.append(_subject("twig_a", SubjectKind.BRANCH, ("img-1",), parent="tree_a"))
        observations.append(
            _obs("obs-c", "leaf.arrangement", "alternate", "twig_a", "img-1", ATTACHED)
        )
    return EvidencePacket(subjects=tuple(subjects), observations=tuple(observations))


def _named(genus: str | None, images: tuple[str, ...]) -> dict[str, Any]:
    return {
        "subject": "the maple by the path",
        "genus": genus,
        "species": None,
        "family": None,
        "confidence": 85,
        "cannot_determine_reason": None,
        "evidence": [{"feature": "palmately lobed leaves", "image_ids": list(images)}],
    }


def _answer(
    genus: str = "Acer", images: tuple[str, ...] = ("img-1",), *more: dict[str, Any]
) -> model_alone.ModelAloneAnswer:
    return model_alone.ModelAloneAnswer.model_validate({"subjects": [_named(genus, images), *more]})


def _reply(
    subject_id: str | None,
    images: tuple[str, ...] = ("img-1",),
    observations: tuple[str, ...] = (),
    *,
    status: str = "bound",
    reason: str | None = None,
) -> subject_binding.BindingReply:
    return subject_binding.BindingReply(
        status=subject_binding.BindingStatus(status),
        subject_id=subject_id,
        supporting_image_ids=images,
        supporting_observation_ids=observations,
        distinguishing_evidence="test",
        abstention_reason=subject_binding.BinderAbstention(reason) if reason else None,
    )


def _decide(packet, reply, knowledge, answer=None) -> subject_binding.BoundDecision:
    return subject_binding.decide_bound(
        answer or _answer(),
        packet,
        knowledge,
        evidence_sufficient=True,
        reply=reply,
        image_ids=IMAGES,
    )


def _said(bound: subject_binding.BoundDecision) -> dict[str, str]:
    return {
        d.subject_id: d.selected_taxon or str(d.abstention_reason and d.abstention_reason.value)
        for d in bound.decisions
    }


UNBOUND = Reason.UNBOUND_SUBJECTS.value


# --- the contract --------------------------------------------------------------------------


def test_the_reply_cannot_carry_a_taxon_a_score_or_several_organisms():
    schema = json.dumps(subject_binding.BindingReply.model_json_schema())
    for field in ("genus", "species", "family", "confidence"):
        assert f'"{field}"' not in schema
    with pytest.raises(pydantic.ValidationError):
        subject_binding.BindingReply.model_validate(
            {
                "status": "bound",
                "subject_id": ["tree_a", "tree_b"],
                "distinguishing_evidence": "x",
                "abstention_reason": None,
            }
        )


def test_the_binder_sees_the_answer_unchanged_and_every_listed_subject():
    packet = _two_trees()
    text = subject_binding.context(_answer(), packet, IMAGES)
    assert json.dumps(_answer().model_dump(mode="json"), ensure_ascii=False, indent=1) in text
    for listed in ('"tree_a"', '"branch_a"', '"tree_b"', '"obs-a"', '"parent_subject_id"'):
        assert listed in text
    assert "img-4" in text


# --- valid, missing and ambiguous bindings ---------------------------------------------------


def test_a_valid_binding_names_only_the_bound_root(knowledge):
    bound = _decide(_two_trees(), _reply("tree_a", ("img-1",), ("obs-a",)), knowledge)
    assert (bound.outcome, bound.subject_id) == (Outcome.BOUND, "tree_a")
    assert _said(bound) == {"tree_a": "acer", "tree_b": UNBOUND}
    named = next(d for d in bound.decisions if d.subject_id == "tree_a")
    assert [r.observation_id for r in named.evidence] == ["obs-a"]


def test_a_missing_or_abstaining_binding_keeps_arm_vs_abstention(knowledge):
    for reply, outcome in (
        (None, Outcome.NO_REPLY),
        (_reply(None, (), status="abstain", reason="ambiguous"), Outcome.BINDER_ABSTAINED),
        (_reply(None, (), status="abstain", reason="describes_several"), Outcome.BINDER_ABSTAINED),
    ):
        bound = _decide(_two_trees(), reply, knowledge)
        assert bound.outcome is outcome
        assert _said(bound) == {"tree_a": UNBOUND, "tree_b": UNBOUND}


def test_a_self_contradictory_reply_is_rejected(knowledge):
    for reply in (
        _reply("tree_a", status="bound", reason="ambiguous"),
        _reply(None, status="bound"),
        _reply("tree_a", status="abstain", reason="ambiguous"),
        _reply(None, (), status="abstain"),
    ):
        bound = _decide(_two_trees(), reply, knowledge)
        assert bound.outcome is Outcome.CONTRADICTORY_REPLY
        assert _said(bound) == {"tree_a": UNBOUND, "tree_b": UNBOUND}


# --- identifiers the packet cannot vouch for -------------------------------------------------


def test_an_invented_or_component_subject_id_binds_nothing(knowledge):
    assert _decide(_two_trees(), _reply("tree_x"), knowledge).outcome is Outcome.UNKNOWN_SUBJECT
    # A component is not an organism: the reply must name its root.
    assert _decide(_two_trees(), _reply("branch_a"), knowledge).outcome is Outcome.NOT_A_ROOT


@pytest.mark.parametrize(
    ("reply", "outcome"),
    [
        (("tree_a", ()), Outcome.NO_SUPPORTING_IMAGE),
        (("tree_a", ("img-9",)), Outcome.UNKNOWN_IMAGE),
        (("tree_a", ("img-3",)), Outcome.IMAGE_DOES_NOT_SHOW_SUBJECT),
        (("tree_a", ("img-1",), ("obs-z",)), Outcome.UNKNOWN_OBSERVATION),
        (("tree_a", ("img-1",), ("obs-b",)), Outcome.OBSERVATION_OF_ANOTHER_ORGANISM),
    ],
)
def test_unsupported_evidence_references_bind_nothing(knowledge, reply, outcome):
    bound = _decide(_two_trees(), _reply(*reply), knowledge)
    assert bound.outcome is outcome
    assert _said(bound) == {"tree_a": UNBOUND, "tree_b": UNBOUND}


def test_a_shared_photograph_alone_does_not_distinguish_an_organism(knowledge):
    answer = _answer("Acer", ("img-1", "img-2"))
    # img-2 shows both trees: neither its position nor its sharing proves which one is meant.
    shared = _decide(_two_trees(), _reply("tree_a", ("img-2",)), knowledge, answer)
    assert shared.outcome is Outcome.NOT_DISTINGUISHED
    # Citing a photograph that shows tree_a alone does.
    alone = _decide(_two_trees(), _reply("tree_a", ("img-1", "img-2")), knowledge, answer)
    assert (alone.outcome, alone.subject_id) == (Outcome.BOUND, "tree_a")


def _bark_answer(images: tuple[str, ...]) -> model_alone.ModelAloneAnswer:
    named = _named("Pinus", images)
    named["evidence"] = [{"feature": "thick plated bark", "image_ids": list(images)}]
    return model_alone.ModelAloneAnswer.model_validate({"subjects": [named]})


def test_a_unique_observation_of_another_feature_cannot_bind(knowledge):
    """A wrong binding the first rules accepted: the answer describes bark, and a detached
    leaf in the same photographs has an observation no other organism has. That observation
    does not record the feature the answer cites, so it cannot make the leaf the answer's
    subject; code cannot read that correspondence, so no observation authorises a binding."""
    packet = EvidencePacket(
        subjects=(
            _subject("tree_p", SubjectKind.STANDING_TREE, ("img-1", "img-2", "img-3")),
            _subject("leaf_x", SubjectKind.DETACHED_PART, ("img-2", "img-3")),
        ),
        observations=(
            _obs("obs-p", "bark.texture", "scaly_plates", "tree_p", "img-2"),
            _obs(
                "obs-x",
                "leaf.shape",
                "rounded",
                "leaf_x",
                "img-3",
                AttachmentStatus.CONFIRMED_DETACHED,
            ),
        ),
    )
    leaf = _reply("leaf_x", ("img-3",), ("obs-x",))
    assert _decide(packet, leaf, knowledge, _bark_answer(("img-2", "img-3"))).outcome is (
        Outcome.NOT_DISTINGUISHED
    )
    # Where the answer also cites a photograph of the tree alone, the leaf cannot be its
    # subject at all, and the tree binds on that photograph.
    wider = _bark_answer(("img-1", "img-2", "img-3"))
    assert _decide(packet, leaf, knowledge, wider).outcome is Outcome.ANSWER_DESCRIBES_ANOTHER
    tree = _decide(packet, _reply("tree_p", ("img-1",), ("obs-p",)), knowledge, wider)
    assert (tree.outcome, tree.subject_id) == (Outcome.BOUND, "tree_p")


def test_an_observation_outside_the_answers_photographs_is_no_support(knowledge):
    packet = _two_trees()
    packet = packet.model_copy(
        update={
            "observations": (
                *packet.observations,
                _obs("obs-a2", "bark.texture", "smooth_grey", "tree_a", "img-2"),
            )
        }
    )
    # The answer cites img-1 only; obs-a2 was made in img-2.
    bound = _decide(packet, _reply("tree_a", ("img-1",), ("obs-a2",)), knowledge)
    assert bound.outcome is Outcome.OBSERVATION_OUTSIDE_ANSWER


def test_an_answer_about_several_organisms_binds_none_of_them(knowledge):
    """A tree and its felled log described as one subject: picking a main one is arbitrary."""
    packet = _two_trees(b_leaf=True)
    # img-1 shows tree_a alone and img-3 tree_b alone: the answer describes both.
    answer = _answer("Acer", ("img-1", "img-3"))
    for reply in (_reply("tree_a", ("img-1",)), _reply("tree_b", ("img-3",))):
        bound = _decide(packet, reply, knowledge, answer)
        assert bound.outcome is Outcome.ANSWER_DESCRIBES_ANOTHER
        assert _said(bound) == {"tree_a": UNBOUND, "tree_b": UNBOUND}
    abstained = _reply(None, (), status="abstain", reason="describes_several")
    assert _decide(packet, abstained, knowledge, answer).outcome is Outcome.BINDER_ABSTAINED


def test_a_binding_the_answer_does_not_support_is_rejected(knowledge):
    # The answer cites img-1 only; tree_b is not in it, so the binder disagrees with it.
    bound = _decide(_two_trees(), _reply("tree_b", ("img-3",)), knowledge)
    assert bound.outcome is Outcome.DISAGREES_WITH_ANSWER
    assert _said(bound) == {"tree_a": UNBOUND, "tree_b": UNBOUND}


# --- the card check after binding ------------------------------------------------------------


def test_two_organisms_sharing_one_genus_name_only_the_bound_one(knowledge):
    packet = _two_trees(b_leaf=True)
    answer = _answer("Acer", ("img-1", "img-2"))
    bound = _decide(packet, _reply("tree_a", ("img-1",)), knowledge, answer)
    # tree_b's own evidence would confirm Acer, but one answer is bound to one organism.
    assert _said(bound) == {"tree_a": "acer", "tree_b": UNBOUND}


def test_binding_identifies_a_subject_and_does_not_confirm_its_genus(knowledge):
    answer = _answer("Acer", ("img-2", "img-3"))
    bound = _decide(_two_trees(), _reply("tree_b", ("img-3",)), knowledge, answer)
    assert (bound.outcome, bound.subject_id) == (Outcome.BOUND, "tree_b")
    assert _said(bound) == {"tree_a": UNBOUND, "tree_b": Reason.NOT_CONFIRMED.value}


def test_a_contradiction_on_an_attached_component_blocks_the_bound_organism(knowledge):
    bound = _decide(_two_trees(contradiction=True), _reply("tree_a"), knowledge)
    assert bound.outcome is Outcome.BOUND
    assert _said(bound) == {"tree_a": Reason.CONTRADICTED.value, "tree_b": UNBOUND}


def test_a_detached_leaf_beside_a_tree_stays_its_own_organism(knowledge):
    packet = EvidencePacket(
        subjects=(
            _subject("tree_t", SubjectKind.STANDING_TREE, ("img-1", "img-2")),
            _subject("leaf_d", SubjectKind.DETACHED_PART, ("img-2",)),
        ),
        observations=(
            _obs("obs-t", "bark.texture", "smooth_grey", "tree_t", "img-1"),
            _obs(
                "obs-l",
                "leaf.shape",
                "palmate_lobed",
                "leaf_d",
                "img-2",
                AttachmentStatus.CONFIRMED_DETACHED,
            ),
        ),
    )
    answer = _answer("Acer", ("img-1", "img-2"))
    tree = _decide(packet, _reply("tree_t", ("img-1",)), knowledge, answer)
    # The tree is checked on its own evidence: the leaf beside it is not attached to it.
    assert _said(tree) == {"tree_t": Reason.NOT_CONFIRMED.value, "leaf_d": UNBOUND}
    borrowed = _decide(packet, _reply("tree_t", ("img-1",), ("obs-l",)), knowledge, answer)
    assert borrowed.outcome is Outcome.OBSERVATION_OF_ANOTHER_ORGANISM


def test_a_genus_without_a_card_is_bound_and_still_names_nothing(knowledge):
    bound = _decide(_two_trees(), _reply("tree_a"), knowledge, _answer("Salix"))
    assert bound.outcome is Outcome.BOUND
    assert _said(bound) == {"tree_a": Reason.NO_CARD.value, "tree_b": UNBOUND}


# --- eligibility: when no call is made -------------------------------------------------------


def _trace(packet: EvidencePacket) -> dict[str, Any]:
    return {
        "events": [],
        "evidence_history": [
            {
                "revision": 1,
                "effective": packet.model_dump(mode="json"),
                "effective_sha256": packet_digest(packet),
            }
        ],
    }


def _arm_v_record(
    packet: EvidencePacket, knowledge, answer=None, *, sufficient: bool = True
) -> dict[str, Any]:
    return validated.record(
        unit_id="u1",
        case_id="u1",
        answer=answer or _answer(),
        answer_source="test",
        namer_model="test:m",
        namer_call=None,
        extractor_model="test:m",
        trace=_trace(packet),
        evidence_sufficient=sufficient,
        insufficient_reasons=(),
        stopped_before="candidate_generator",
        photos=[{"name": f"p{n}.jpg", "sha256": f"{n}" * 64} for n in range(1, 5)],
        field_context=False,
        domain_prompt_sha256="d" * 64,
        image_max_edge_px=None,
        code_commit_sha=None,
        started_at="t",
        wall_seconds={"total": 1.0},
        knowledge=knowledge,
    )


def _bind(arm_v, knowledge, provider, *, identity=None, images=lambda: ()) -> dict[str, Any]:
    return asyncio.run(
        subject_binding.bind_unit(
            arm_v, knowledge=knowledge, provider=provider, images=images, identity=identity
        )
    )


def _single_tree() -> EvidencePacket:
    return EvidencePacket(
        subjects=(_subject("tree_o", SubjectKind.STANDING_TREE, ("img-1",)),),
        observations=(_obs("obs-o", "leaf.shape", "palmate_lobed", "tree_o", "img-1", ATTACHED),),
    )


def test_a_single_organism_costs_no_provider_call(knowledge):
    provider = ScriptedProvider({})
    bound = _bind(_arm_v_record(_single_tree(), knowledge), knowledge, provider)
    assert provider.calls == []
    binding = bound["result"]["binding"]
    assert (binding["outcome"], binding["ineligible_reason"]) == ("not_eligible", "single_organism")
    assert binding["asked"] is False and binding["context"] is None


def test_binding_is_no_fix_for_a_shared_misreading(knowledge):
    """The oak-1 limit: a one-tree packet that misreads lobed leaves as palmate confirms the
    namer's Acer in arm V. Binding never runs there and leaves that decision as it was."""
    arm_v = _arm_v_record(_single_tree(), knowledge)
    provider = ScriptedProvider({})
    bound = _bind(arm_v, knowledge, provider)
    assert provider.calls == []
    assert bound["result"]["response"]["decisions"] == arm_v["result"]["response"]["decisions"]
    assert bound["result"]["response"]["decisions"][0]["selected_taxon"] == "acer"


def test_several_named_answer_subjects_or_weak_evidence_cost_no_call(knowledge):
    other = _named("Acer", ("img-3",)) | {"subject": "the other maple"}
    for arm_v, reason in (
        (_arm_v_record(_two_trees(), knowledge, _answer("Acer", ("img-1",), other)), "several"),
        (_arm_v_record(_two_trees(), knowledge, sufficient=False), "evidence"),
    ):
        provider = ScriptedProvider({})
        binding = _bind(arm_v, knowledge, provider)["result"]["binding"]
        assert provider.calls == []
        assert binding["outcome"] == "not_eligible"
        assert (
            binding["ineligible_reason"]
            == {
                "several": "several_named_subjects",
                "evidence": "evidence_insufficient",
            }[reason]
        )


# --- the live path, with a scripted provider -------------------------------------------------


def test_an_eligible_unit_makes_exactly_one_call_and_records_it(knowledge):
    arm_v = _arm_v_record(_two_trees(), knowledge)
    before = copy.deepcopy(arm_v)
    provider = ScriptedProvider({"primary:subject_binding": _reply("tree_a", ("img-1",))})
    identity = {"binder": "scripted:scripted"}
    bound = _bind(arm_v, knowledge, provider, identity=identity)
    assert provider.calls == ["primary:subject_binding"]
    binding = bound["result"]["binding"]
    assert binding["outcome"] == "bound" and binding["subject_id"] == "tree_a"
    assert binding["supporting_image_ids"] == ["img-1"]
    assert binding["binder"] == "scripted:scripted"
    assert binding["call"]["node"] == subject_binding.NODE
    assert binding["run_identity"] == identity
    assert binding["source_record_sha256"] == subject_binding.record_sha256(before)
    assert len(binding["input_sha256"]) == 64 and "tree_b" in binding["context"]
    assert bound["arm"] == subject_binding.ARM
    assert {"node": subject_binding.NODE, "provider_calls": [binding["call"]]} in bound["result"][
        "trace"
    ]["events"]
    assert arm_v == before  # the arm V record is not changed by binding it


def test_a_failed_binding_call_abstains_safely_and_says_so(knowledge):
    arm_v = _arm_v_record(_two_trees(), knowledge)
    provider = ScriptedProvider({})  # no scripted reply: the call raises
    bound = _bind(arm_v, knowledge, provider)
    binding = bound["result"]["binding"]
    assert binding["outcome"] == "call_failed" and binding["error"]
    assert binding["asked"] is True
    assert bound["result"]["response"]["decisions"] == arm_v["result"]["response"]["decisions"]


def test_changed_photographs_fail_the_call_before_any_request(knowledge):
    def changed() -> tuple[()]:
        msg = "the photographs differ from the arm V record's"
        raise ValueError(msg)

    provider = ScriptedProvider({"primary:subject_binding": _reply("tree_a")})
    bound = _bind(_arm_v_record(_two_trees(), knowledge), knowledge, provider, images=changed)
    assert provider.calls == []
    assert bound["result"]["binding"]["outcome"] == "call_failed"


# --- replay and resume -----------------------------------------------------------------------


def _bound_record(knowledge, reply=None) -> dict[str, Any]:
    return subject_binding.record(
        _arm_v_record(_two_trees(), knowledge),
        knowledge=knowledge,
        reply=reply or _reply("tree_a", ("img-1",), ("obs-a",)),
        binder="test:m",
    )


def test_replay_reproduces_decisions_without_a_provider(knowledge, monkeypatch):
    async def no_calls(**_: Any) -> None:
        msg = "replay must not call a provider"
        raise AssertionError(msg)

    monkeypatch.setattr(subject_binding, "request_structured", no_calls)
    records = [
        _bound_record(knowledge),
        _bound_record(knowledge, _reply("tree_x")),
        subject_binding.record(
            _arm_v_record(_two_trees(), knowledge), knowledge=knowledge, error="TimeoutError: x"
        ),
        subject_binding.record(_arm_v_record(_single_tree(), knowledge), knowledge=knowledge),
    ]
    for stored in records:
        assert subject_binding.replay_status(stored, knowledge) is ReplayStatus.IDENTICAL

    tampered = copy.deepcopy(records[0])
    tampered["result"]["binding"]["reply"]["subject_id"] = "tree_b"
    assert subject_binding.replay_status(tampered, knowledge) is ReplayStatus.DIFFERS


def test_replay_reports_other_rules_or_inputs_instead_of_reusing_them(knowledge):
    stored = _bound_record(knowledge)
    older = copy.deepcopy(stored)
    older["result"]["binding"]["rules_version"] = subject_binding.RULES_VERSION - 1
    assert subject_binding.replay_status(older, knowledge) is ReplayStatus.RULES_CHANGED
    other_request = copy.deepcopy(stored)
    other_request["result"]["binding"]["request_sha256"] = "0" * 64
    assert subject_binding.replay_status(other_request, knowledge) is ReplayStatus.RULES_CHANGED
    other_photos = copy.deepcopy(stored)
    other_photos["result"][validated.ARM]["photos"][0]["sha256"] = "f" * 64
    assert subject_binding.replay_status(other_photos, knowledge) is ReplayStatus.INPUT_CHANGED


def test_resume_retries_failures_and_refuses_another_runs_records(knowledge):
    identity = {"binder": "scripted:scripted", "code_commit_sha": "a" * 40}
    arm_v = _arm_v_record(_two_trees(), knowledge)
    source = subject_binding.record_sha256(arm_v)

    def action(existing: dict[str, Any] | None, sha: str | None = source) -> str:
        return subject_binding.resume_action(existing, identity=identity, source_sha256=sha)[0]

    bound = subject_binding.record(
        arm_v, knowledge=knowledge, reply=_reply("tree_a"), identity=identity
    )
    failed_call = subject_binding.record(
        arm_v, knowledge=knowledge, error="TimeoutError: x", identity=identity
    )
    failed_unit = subject_binding.source_failure("u1", None, identity)
    elsewhere = subject_binding.record(
        arm_v, knowledge=knowledge, reply=_reply("tree_a"), identity={**identity, "binder": "x"}
    )
    assert action(None) == "run"
    assert action(bound) == "reuse"
    assert action(failed_call) == "run"
    assert action(failed_unit) == "run"
    assert action(bound, sha="0" * 64) == "run"  # the arm V record changed since
    assert action(elsewhere) == "refuse"
    assert action(subject_binding.source_failure("u1", None, {"binder": "x"})) == "refuse"


def test_every_planned_unit_is_counted_with_failures_apart(knowledge):
    arm_v = _arm_v_record(_two_trees(), knowledge)
    counts = subject_binding.tally(
        {
            "bound": subject_binding.record(arm_v, knowledge=knowledge, reply=_reply("tree_a")),
            "abstained": subject_binding.record(
                arm_v,
                knowledge=knowledge,
                reply=_reply(None, (), status="abstain", reason="ambiguous"),
            ),
            "call": subject_binding.record(arm_v, knowledge=knowledge, error="TimeoutError: x"),
            "unit": subject_binding.source_failure("unit", None, None),
            "single": subject_binding.record(
                _arm_v_record(_single_tree(), knowledge), knowledge=knowledge
            ),
            "not_run": None,
        }
    )
    assert counts == {
        "bound": 1,
        "binder_abstained": 1,
        "call_failed": 1,
        "failed": 1,
        "not_eligible": 1,
        "not_run": 1,
    }
