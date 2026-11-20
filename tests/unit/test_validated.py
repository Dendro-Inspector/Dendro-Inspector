"""Arm V: a model's genus is named only where the graph's own evidence confirms it on a card.

The decision rules are stated on constructed packets against the repository's cards
(AGENTS.md section 16: non-golden tests); the end-to-end tests run the graph's first stages on
the fake provider with a scripted namer, so no test depends on a model.
"""

from __future__ import annotations

import asyncio
import copy

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


def test_two_of_the_models_genera_confirmed_on_one_subject_name_neither(knowledge):
    _, decisions = validated.decide(
        _answer("Acer", "Tilia"),
        _packet(
            _obs("obs-1", "leaf.shape", "palmate_lobed"),
            _obs("obs-2", "fruit.type", "nutlet_with_bract"),
        ),
        knowledge,
        evidence_sufficient=True,
    )
    decision = _only(decisions)
    assert decision.selected_taxon is None
    assert decision.abstention_reason is validated.AbstentionReason.AMBIGUOUS


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
