"""Arm V: a model's genus is named only where the graph's own evidence confirms it on a card.

The decision rules are stated on constructed packets against the repository's cards
(AGENTS.md section 16: non-golden tests); the end-to-end tests run the graph's first stages on
the fake provider with a scripted namer, so no test depends on a model.
"""

from __future__ import annotations

import asyncio
import copy
import json

import pytest

from dendro_inspector.evaluation import model_alone, validated
from dendro_inspector.evaluation.label_scoring import Outcome, ReferenceLabel
from dendro_inspector.graph.definition import NodeName
from dendro_inspector.graph.executor import run_graph
from dendro_inspector.nodes import build_registry
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

SUBJECT = "standing_tree"
PREFIX = {"input_guard", "planner", "evidence_extractor", "evidence_quality"}


def _obs(observation_id: str, feature: str, value: str, subject: str = SUBJECT) -> Observation:
    detachable = feature.split(".")[0] in {"leaf", "fruit", "samara"}
    return Observation(
        observation_id=observation_id,
        feature=feature,
        value=value,
        subject_id=subject,
        source=ObservationSource.IMAGE,
        image_id="img-2",
        visibility=Visibility.CLEAR,
        reliability=Reliability.HIGH,
        attachment=AttachmentStatus.CONFIRMED_ATTACHED if detachable else None,
    )


def _packet(*observations: Observation, subjects: tuple[str, ...] = (SUBJECT,)) -> EvidencePacket:
    return EvidencePacket(
        subjects=tuple(Subject(subject_id=s, kind=SubjectKind.STANDING_TREE) for s in subjects),
        observations=observations,
    )


def _answer(*genera: str | None) -> model_alone.ModelAloneAnswer:
    return model_alone.ModelAloneAnswer.model_validate(
        {
            "subjects": [
                {
                    "subject": "the tree",
                    "genus": genus,
                    "species": None,
                    "family": None,
                    "confidence": 85,
                    "cannot_determine_reason": None if genus else "no leaves visible",
                    "evidence": [],
                }
                for genus in genera
            ]
        }
    )


def _only(decisions):
    assert len(decisions) == 1
    return decisions[0]


def test_a_confirmed_genus_names_the_subject_and_cites_the_observation(knowledge):
    checks, decisions = validated.decide(
        _answer("Acer"),
        _packet(_obs("obs-leaf", "leaf.shape", "palmate_lobed")),
        knowledge,
        evidence_sufficient=True,
    )
    decision = _only(decisions)
    assert decision.selected_taxon == "acer"
    assert decision.resolution == "genus"
    assert decision.status == "card_confirmed"
    assert not decision.abstained
    assert [(r.observation_id, r.feature, r.value, r.image_id) for r in decision.evidence] == [
        ("obs-leaf", "leaf.shape", "palmate_lobed", "img-2")
    ]
    assert decision.visual_hypothesis is None
    assert any(c.verdict is validated.Verdict.CONFIRMED and c.card == "acer" for c in checks)


def test_an_unconfirmed_genus_is_kept_only_as_a_hypothesis_with_the_next_photograph(knowledge):
    _, decisions = validated.decide(
        _answer("Tilia"),
        _packet(_obs("obs-bark", "bark.pattern", "shallow_fissures")),
        knowledge,
        evidence_sufficient=True,
    )
    decision = _only(decisions)
    assert decision.selected_taxon is None
    assert decision.abstention_reason is validated.AbstentionReason.NOT_CONFIRMED
    assert decision.visual_hypothesis == validated.Hypothesis(taxon="tilia")
    assert decision.next_photo
    assert decision.next_photo == knowledge.follow_up_for(validated.genus_cards(knowledge, "tilia"))


def test_a_contradicting_observation_outweighs_a_strong_one(knowledge):
    _, decisions = validated.decide(
        _answer("Acer"),
        _packet(
            _obs("obs-leaf", "leaf.shape", "palmate_lobed"),
            _obs("obs-arr", "leaf.arrangement", "alternate"),
        ),
        knowledge,
        evidence_sufficient=True,
    )
    decision = _only(decisions)
    assert decision.selected_taxon is None
    assert decision.abstention_reason is validated.AbstentionReason.CONTRADICTED


def test_a_genus_without_a_card_is_never_named(knowledge):
    _, decisions = validated.decide(
        _answer("Ulmus"),
        _packet(_obs("obs-leaf", "leaf.shape", "palmate_lobed")),
        knowledge,
        evidence_sufficient=True,
    )
    decision = _only(decisions)
    assert decision.selected_taxon is None
    assert decision.abstention_reason is validated.AbstentionReason.NO_CARD
    assert decision.visual_hypothesis == validated.Hypothesis(taxon="ulmus")
    assert decision.next_photo == ()


def test_a_model_that_names_no_genus_gives_no_answer_and_no_hypothesis(knowledge):
    _, decisions = validated.decide(
        _answer(None),
        _packet(_obs("obs-leaf", "leaf.shape", "palmate_lobed")),
        knowledge,
        evidence_sufficient=True,
    )
    decision = _only(decisions)
    assert decision.abstention_reason is validated.AbstentionReason.MODEL_NAMED_NO_GENUS
    assert decision.visual_hypothesis is None


def test_insufficient_evidence_abstains_even_where_a_card_would_confirm(knowledge):
    checks, decisions = validated.decide(
        _answer("Acer"),
        _packet(_obs("obs-leaf", "leaf.shape", "palmate_lobed")),
        knowledge,
        evidence_sufficient=False,
    )
    assert checks == ()
    assert _only(decisions).abstention_reason is validated.AbstentionReason.EVIDENCE_INSUFFICIENT


def test_several_genera_name_no_tree_rather_than_guess_which_tree_is_which(knowledge):
    # The model's subjects are free text: "the maple" and "the lime" carry no evidence
    # subject ID, so even evidence that would confirm each genus on one tree binds nothing.
    checks, decisions = validated.decide(
        _answer("Acer", "Tilia"),
        _packet(
            _obs("obs-1", "leaf.shape", "palmate_lobed", subject="tree_a"),
            _obs("obs-2", "fruit.type", "nutlet_with_bract", subject="tree_b"),
            subjects=("tree_a", "tree_b"),
        ),
        knowledge,
        evidence_sufficient=True,
    )
    assert checks == ()
    assert {d.subject_id for d in decisions} == {"tree_a", "tree_b"}
    for decision in decisions:
        assert decision.selected_taxon is None
        assert decision.abstention_reason is validated.AbstentionReason.UNBOUND
        assert decision.visual_hypothesis is None


def test_one_genus_is_named_only_on_trees_whose_own_evidence_confirms_it(knowledge):
    _, decisions = validated.decide(
        _answer("Acer"),
        _packet(
            _obs("obs-a", "leaf.shape", "palmate_lobed", subject="tree_a"),
            _obs("obs-b", "leaf.shape", "palmate_lobed", subject="tree_b"),
            _obs("obs-c", "bark.pattern", "shallow_fissures", subject="tree_c"),
            subjects=("tree_a", "tree_b", "tree_c"),
        ),
        knowledge,
        evidence_sufficient=True,
    )
    by_subject = {d.subject_id: d for d in decisions}
    assert by_subject["tree_a"].selected_taxon == "acer"
    assert [r.observation_id for r in by_subject["tree_a"].evidence] == ["obs-a"]
    assert [r.observation_id for r in by_subject["tree_b"].evidence] == ["obs-b"]
    assert by_subject["tree_c"].selected_taxon is None
    assert by_subject["tree_c"].abstention_reason is validated.AbstentionReason.NOT_CONFIRMED


def test_a_shared_misreading_is_confirmed_which_is_the_checks_known_limit(knowledge):
    # Recorded on a development oak: one model named Acer and its own extraction read the
    # lobed leaves as palmate_lobed, opposite. The check confirms that; it cannot see that
    # both readings are wrong. Another model's reading of the same leaves (simple_lobed)
    # does not confirm Acer, which is why --answers lets one model's evidence check another.
    misread = _packet(
        _obs("obs-shape", "leaf.shape", "palmate_lobed"),
        _obs("obs-arr", "leaf.arrangement", "opposite"),
    )
    _, decisions = validated.decide(_answer("Acer"), misread, knowledge, evidence_sufficient=True)
    assert _only(decisions).selected_taxon == "acer"

    read_by_another = _packet(_obs("obs-shape", "leaf.shape", "simple_lobed"))
    _, decisions = validated.decide(
        _answer("Acer"), read_by_another, knowledge, evidence_sufficient=True
    )
    assert _only(decisions).selected_taxon is None


def test_only_the_confirming_subject_is_named_and_no_hypothesis_is_spread(knowledge):
    _, decisions = validated.decide(
        _answer("Acer"),
        _packet(
            _obs("obs-leaf", "leaf.shape", "palmate_lobed", subject="tree_a"),
            subjects=("tree_a", "tree_b"),
        ),
        knowledge,
        evidence_sufficient=True,
    )
    by_subject = {d.subject_id: d for d in decisions}
    assert by_subject["tree_a"].selected_taxon == "acer"
    assert by_subject["tree_b"].selected_taxon is None
    assert by_subject["tree_b"].visual_hypothesis is None


def test_no_subject_in_the_evidence_abstains_for_the_case(knowledge):
    _, decisions = validated.decide(_answer("Acer"), None, knowledge, evidence_sufficient=True)
    decision = _only(decisions)
    assert decision.subject_id == validated.CASE_SUBJECT
    assert decision.abstention_reason is validated.AbstentionReason.NO_EVIDENCE


def test_grading_reads_named_subjects_and_never_a_hypothesis(knowledge):
    label = ReferenceLabel("tilia")
    named = {
        "result": {"response": {"decisions": [{"subject_id": SUBJECT, "selected_taxon": "tilia"}]}}
    }
    held = {
        "result": {
            "response": {
                "decisions": [
                    {
                        "subject_id": SUBJECT,
                        "selected_taxon": None,
                        "visual_hypothesis": {"taxon": "tilia"},
                    }
                ]
            }
        }
    }
    assert validated.grade(named, label, knowledge).outcome is Outcome.CORRECT
    assert validated.grade(held, label, knowledge).outcome is Outcome.NO_NAME


def test_the_walk_can_stop_before_candidate_generation(simple_case, node_context):
    result = asyncio.run(
        run_graph(simple_case, node_context, build_registry(), stop_before=validated.STOP_BEFORE)
    )
    assert result.stopped_before in validated.STOP_BEFORE
    assert {event.node for event in result.trace.events} <= PREFIX
    assert result.state.evidence is not None
    assert not result.state.decisions


def _run(simple_case, node_context, genus: str):
    async def namer():
        return _answer(genus), None, 0.0

    return asyncio.run(
        validated.run_unit(
            simple_case,
            node_context,
            namer,
            unit_id="unit-1",
            answer_source="test",
            namer_model="scripted",
            namer_adapter="test",
            photos=[{"name": "log.jpg", "sha256": "0" * 64}],
            field_context=False,
            domain_prompt_sha256="d" * 64,
            image_max_edge_px=None,
            code_commit_sha=None,
            started_at="2026-11-20T00:00:00+00:00",
        )
    )


def test_end_to_end_a_confirmed_genus_cites_the_fake_extractors_observation(
    simple_case, node_context, knowledge
):
    stored = _run(simple_case, node_context, "Pinus")
    assert stored["exit_code"] == 0
    decisions = stored["result"]["response"]["decisions"]
    named = [d for d in decisions if d["selected_taxon"] == "pinus"]
    assert named
    assert all(d["status"] == "card_confirmed" for d in named)
    assert {"obs-2"} <= {ref["observation_id"] for d in named for ref in d["evidence"]}
    nodes = {event["node"] for event in stored["result"]["trace"]["events"]}
    assert nodes <= PREFIX | {model_alone.NODE}
    assert NodeName.CANDIDATE_GENERATOR.value not in nodes
    arm = stored["result"][validated.ARM]
    assert arm["rules_version"] == validated.RULES_VERSION
    assert arm["stopped_before"] == NodeName.CANDIDATE_GENERATOR.value
    assert set(arm["wall_seconds"]) == {"namer", "extraction", "total"}
    assert validated.replay_matches(stored, knowledge)


def test_end_to_end_an_unconfirmed_genus_abstains_and_replays(simple_case, node_context, knowledge):
    stored = _run(simple_case, node_context, "Quercus")
    decisions = stored["result"]["response"]["decisions"]
    assert all(d["selected_taxon"] is None for d in decisions)
    assert all(
        d["visual_hypothesis"]
        == {"taxon": "quercus", "source": "model_alone", "status": "unconfirmed"}
        for d in decisions
    )
    assert validated.replay_matches(stored, knowledge)

    tampered = copy.deepcopy(stored)
    tampered["result"]["response"]["decisions"][0]["selected_taxon"] = "quercus"
    assert not validated.replay_matches(tampered, knowledge)


def test_a_failed_namer_fails_the_unit_without_a_response(simple_case, node_context):
    async def namer():
        raise RuntimeError("provider unavailable")

    stored = asyncio.run(
        validated.run_unit(
            simple_case,
            node_context,
            namer,
            unit_id="unit-1",
            answer_source="test",
            namer_model="scripted",
            namer_adapter="test",
            photos=[],
            field_context=False,
            domain_prompt_sha256="d" * 64,
            image_max_edge_px=None,
            code_commit_sha=None,
            started_at="2026-11-20T00:00:00+00:00",
        )
    )
    assert stored["exit_code"] == 1
    assert "result" not in stored
    assert "provider unavailable" in stored["error"]


def _arm_m(photo_sha256, *, field_context=False, domain="d" * 64):
    stored = model_alone.record(
        answer=_answer("Pinus"),
        usage=None,
        wall_seconds=1.0,
        adapter="vibe",
        model="large",
        unit_id="unit-1",
        photos=[{"name": f"{n}.jpg", "sha256": s} for n, s in enumerate(photo_sha256)],
        field_context=field_context,
        domain_prompt_sha256=domain,
        image_max_edge_px=1600,
        code_commit_sha=None,
        started_at="2026-11-20T00:00:00+00:00",
    )
    return json.loads(json.dumps(stored))


def test_an_earlier_answer_is_used_only_for_the_same_question():
    photos = ["a" * 64, "b" * 64]
    recorded = validated.answer_from_record(
        _arm_m(photos), photo_sha256=photos, field_context=False, domain_prompt_sha256="d" * 64
    )
    assert validated.model_genera(recorded.answer) == ("pinus",)
    assert (recorded.adapter, recorded.model) == ("vibe", "large")
    assert recorded.source.startswith("record:vibe:large:")


@pytest.mark.parametrize(
    ("earlier", "photos", "field_context", "domain"),
    [
        (["a" * 64, "b" * 64], ["a" * 64, "c" * 64], False, "d" * 64),
        (["a" * 64, "b" * 64], ["b" * 64, "a" * 64], False, "d" * 64),
        (["a" * 64], ["a" * 64, "b" * 64], False, "d" * 64),
        (["a" * 64], ["a" * 64], True, "d" * 64),
        (["a" * 64], ["a" * 64], False, "e" * 64),
    ],
    ids=["other-photo", "other-order", "fewer-photos", "field-context", "domain-prompt"],
)
def test_an_earlier_answer_to_another_question_is_refused(earlier, photos, field_context, domain):
    with pytest.raises(validated.AnswerMismatchError):
        validated.answer_from_record(
            _arm_m(earlier),
            photo_sha256=photos,
            field_context=field_context,
            domain_prompt_sha256=domain,
        )


def test_a_failed_arm_m_record_holds_no_answer_to_use():
    failed = _arm_m(["a" * 64])
    failed["failure"] = failed.pop("result")
    with pytest.raises(validated.AnswerMismatchError):
        validated.answer_from_record(
            failed, photo_sha256=["a" * 64], field_context=False, domain_prompt_sha256="d" * 64
        )


def test_replay_of_a_folder_fails_when_there_is_nothing_to_replay(tmp_path, knowledge):
    (tmp_path / "cases").mkdir()
    summary = validated.replay_run(tmp_path, knowledge)
    assert summary.checked == 0
    assert summary.exit_code == 2


def test_replay_of_a_folder_passes_identical_records_and_names_changed_ones(
    tmp_path, simple_case, node_context, knowledge
):
    stored = _run(simple_case, node_context, "Pinus")
    cases = tmp_path / "cases"
    cases.mkdir()
    (cases / "same.json").write_text(json.dumps(stored), encoding="utf-8")
    assert validated.replay_run(tmp_path, knowledge).exit_code == 0

    changed = copy.deepcopy(stored)
    changed["result"]["response"]["decisions"][0]["selected_taxon"] = "picea"
    (cases / "changed.json").write_text(json.dumps(changed), encoding="utf-8")
    summary = validated.replay_run(tmp_path, knowledge)
    assert summary.checked == 2
    assert summary.differing == ("changed",)
    assert summary.exit_code == 1
