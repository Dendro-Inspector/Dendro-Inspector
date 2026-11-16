"""Execution events and the run trace.

The trace is the product, not a debug afterthought: every run records which nodes ran,
what they decided, how many retries were spent, whether the arbiter was called, and which
domain-prompt hash was in force. It never records API keys, image bytes, private user
metadata, or hidden model reasoning.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field

from dendro_inspector.observability.evidence_history import EvidenceRevision
from dendro_inspector.schemas.base import Contract, Identifier, ShortText
from dendro_inspector.schemas.candidates import CardRetrieval
from dendro_inspector.schemas.decisions import (
    AuthorityCheckTrace,
    DecisionDerivation,
    FinalDecision,
    FindingReference,
)
from dendro_inspector.schemas.evidence import KnowledgeCoverage
from dendro_inspector.schemas.taxon import Confidence, Resolution
from dendro_inspector.schemas.vision import VisionShadowResult

GRAPH_VERSION = "0.9.0"

#: Version of the trace's own shape, counted separately from the release. A trace can gain
#: a record between two releases, and a reader has to know which records to expect without
#: inferring it from the package version. Traces written before this field existed carry no
#: version and are version 1: they have no `review_rounds`, so an empty tuple there means
#: "not recorded", never "no review ran".
TRACE_SCHEMA_VERSION = 4
LEGACY_TRACE_SCHEMA_VERSION = 1


class NodeStatus(StrEnum):
    OK = "ok"
    SKIPPED = "skipped"
    RETRIED = "retried"
    FAILED = "failed"


class PromptCompatibilityStatus(StrEnum):
    """Whether prompt-policy compatibility has been established."""

    UNVALIDATED = "unvalidated"
    COMPATIBLE = "compatible"


class ProviderCallRecord(Contract):
    """One structured model call. Prompt and response bodies are deliberately absent."""

    role: str = Field(max_length=40)
    adapter: str = Field(max_length=40)
    model: str | None = Field(default=None, max_length=120)
    node: str = Field(max_length=60)
    response_model: str = Field(max_length=120)
    attempts: int = Field(default=1, ge=1)
    validation_failures: int = Field(default=0, ge=0)
    validation_errors: tuple[str, ...] = Field(
        default=(),
        max_length=40,
        description=(
            "`attempt-N:<field path>:<error type>` for every validation failure this call "
            "repaired or died on. Field paths and error kinds only, never response bodies."
        ),
    )
    duration_ms: float | None = Field(default=None, ge=0)
    #: Provider-reported accounting, summed over every attempt this call made, because
    #: `duration_ms` spans them all. `None` means the provider reported nothing, which is a
    #: different fact from zero and must not be rendered as one.
    input_tokens: int | None = Field(
        default=None, ge=0, description="Total input tokens, including cache reads and writes."
    )
    cached_input_tokens: int | None = Field(
        default=None,
        ge=0,
        description="Prompt tokens served from the provider's cache, where it says so.",
    )
    output_tokens: int | None = Field(default=None, ge=0)
    cache_write_input_tokens: int | None = Field(
        default=None,
        ge=0,
        description="Input tokens written to the provider's cache, already in input_tokens.",
    )
    reasoning_output_tokens: int | None = Field(
        default=None,
        ge=0,
        description=(
            "The part of `output_tokens` a provider attributes to hidden reasoning, where "
            "it reports one separately. It is billed as output and is already included in "
            "`output_tokens`; this field says how much of that total was never shown."
        ),
    )
    reported_cost_usd: float | None = Field(
        default=None,
        ge=0,
        description="Cost as the provider reported it. Never estimated from a price table.",
    )


class ReviewerProjectionRecord(Contract):
    """Audit-safe summary of the bounded input supplied to one reviewer."""

    reviewer: str = Field(max_length=40)
    evidence_ids: tuple[Identifier, ...] = ()
    image_ids: tuple[Identifier, ...] = ()
    candidate_subject_ids: tuple[Identifier, ...] = ()
    taxon_ids: tuple[Identifier, ...] = ()
    include_comparison_cards: bool = True
    include_regional_pack: bool = True


class NodeEvent(Contract):
    """One executed node."""

    node: str = Field(max_length=60)
    status: NodeStatus = NodeStatus.OK
    sequence: int = Field(ge=0)
    detail: ShortText | None = None
    duration_ms: float | None = Field(default=None, ge=0)
    provider_calls: tuple[ProviderCallRecord, ...] = ()
    reviewer_projection: ReviewerProjectionRecord | None = None


class ComponentProjection(Contract):
    """Auditable component-to-identity mapping created by deterministic normalization."""

    identity_subject_id: Identifier
    source_component_id: Identifier
    observation_ids: tuple[Identifier, ...] = Field(min_length=1)


class PromptMetadata(Contract):
    """Identity and policy compatibility of the prompt bundle in force."""

    path: str = Field(max_length=400)
    version: str = Field(default="user-managed", max_length=80)
    sha256: str = Field(min_length=64, max_length=64)
    bytes: int = Field(ge=0)
    is_placeholder: bool = False
    manifest_schema_version: str | None = Field(default=None, max_length=40)
    policy_revision: str | None = Field(default=None, max_length=80)
    node_prompt_revision: str | None = Field(default=None, max_length=80)
    manifest_path: str | None = Field(default=None, max_length=400)
    manifest_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    compatibility_status: PromptCompatibilityStatus = PromptCompatibilityStatus.UNVALIDATED
    #: `full` or `per_node`: whether model calls were sent the whole domain prompt.
    domain_sections: str = Field(default="full", max_length=16)


#: Bounds on the evidence ledger, so a pathological extraction cannot bloat a case record.
MAX_LEDGER_OBSERVATIONS = 400
MAX_LEDGER_SUBJECTS = 32

#: Bounds on the review record. A round holds three reviews, or one arbiter review; the
#: retry budget bounds the rounds. What a bound cuts is counted in the record, never dropped
#: silently.
MAX_REVIEW_ROUNDS = 16
MAX_REVIEW_FINDINGS = 100
MAX_REVIEW_CANDIDATES = 40


class ObservationRecord(Contract):
    """One observation as the decision rules saw it. Tokens only: no notes, no image bytes."""

    observation_id: str = Field(max_length=120)
    subject_id: str = Field(max_length=120)
    feature: str = Field(max_length=120)
    value: str = Field(max_length=120)
    source: str = Field(max_length=40)
    image_id: str | None = Field(default=None, max_length=120)
    visibility: str = Field(max_length=40)
    reliability: str = Field(max_length=40)
    attachment: str | None = Field(default=None, max_length=40)
    wood_surface: str | None = Field(default=None, max_length=40)
    trust: str = Field(
        max_length=40,
        description="Whether this reading may support an identification (EvidenceTrust).",
    )
    tier: int = Field(ge=0, le=10, description="Effective evidence tier after trust.")


class CandidateSupportRecord(Contract):
    """One candidate after validation, with the support it kept."""

    taxon: str = Field(max_length=120)
    resolution: Resolution
    score: str = Field(max_length=40)
    rank: int = Field(ge=1)
    supporting_evidence_ids: tuple[str, ...] = ()
    contradicting_evidence_ids: tuple[str, ...] = ()
    missing_decisive_features: tuple[str, ...] = ()


class DroppedSupportRecord(Contract):
    """One proposed support id that validation refused, and why (PR #18's details)."""

    taxon: str = Field(max_length=120)
    evidence_id: str = Field(max_length=120)
    reason: str = Field(max_length=60)
    feature: str | None = Field(default=None, max_length=120)
    value: str | None = Field(default=None, max_length=120)
    expected_values: tuple[str, ...] = ()


class CandidateValidationRecord(Contract):
    """What deterministic validation admitted and rejected for one subject's candidates."""

    subject_id: str = Field(max_length=120)
    proposed_taxa: tuple[str, ...] = ()
    admitted: tuple[CandidateSupportRecord, ...] = ()
    dropped_support: tuple[DroppedSupportRecord, ...] = ()
    rejected_taxa: tuple[str, ...] = ()
    demoted_scores: tuple[str, ...] = Field(
        default=(), description="`taxon:proposed->effective` support strength changes."
    )
    malformed_evidence_ids: tuple[str, ...] = ()
    evidence_revision: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Which extraction pass these candidates were validated against. A record whose "
            "revision is older than the trace's `evidence_context.revision` cites "
            "observation ids from a packet a correction retry has since replaced."
        ),
    )


class SubjectRecord(Contract):
    """One identity subject of the final evidence packet."""

    subject_id: str = Field(max_length=120)
    kind: str = Field(max_length=40)
    image_ids: tuple[str, ...] = ()


class ObservationUseRecord(Contract):
    """How candidate validation used one observation in one extraction revision.

    An observation can support one candidate and be rejected for another. Those uses are
    independent, so the record retains both instead of assigning a misleading single state.
    References through an inference are resolved to its originating observations.
    """

    evidence_revision: int = Field(ge=1)
    evidence_ref: str = Field(max_length=160)
    observation_id: str = Field(max_length=120)
    supporting_taxa: tuple[str, ...] = ()
    contradicting_taxa: tuple[str, ...] = ()
    rejected_support: tuple[DroppedSupportRecord, ...] = ()
    candidate_validation_ran: bool = False
    cited: bool = False


class InferenceRecord(Contract):
    """One inference of the final evidence packet. Tokens and ids only."""

    inference_id: str = Field(max_length=120)
    claim: str = Field(max_length=120)
    derived_from: tuple[str, ...] = ()
    strength: str = Field(max_length=40)
    limitations: tuple[str, ...] = ()


class ImageLimitationRecord(Contract):
    """What one photograph could not show, as tokens. The free-text note is not kept."""

    image_id: str = Field(max_length=120)
    lighting: str | None = Field(default=None, max_length=120)
    white_balance: str | None = Field(default=None, max_length=120)
    scale: str = Field(max_length=40)


class EvidenceContextRecord(Contract):
    """Everything the final evidence packet carried besides its observations.

    The decision rules read all of it: subject kinds decide evidence trust, inferences are
    cited as support, and the packet flags raise escalation triggers. A replay that has to
    guess any of them is a reconstruction rather than a replay.
    """

    revision: int = Field(
        ge=1,
        description=(
            "How many extraction passes ran. The compact ledger describes the last one; "
            "schema version 3 also retains each pass in evidence_history."
        ),
    )
    subjects: tuple[SubjectRecord, ...] = Field(default=(), max_length=MAX_LEDGER_SUBJECTS)
    inferences: tuple[InferenceRecord, ...] = Field(default=(), max_length=MAX_LEDGER_OBSERVATIONS)
    absent_features: tuple[str, ...] = ()
    context_limitations: tuple[str, ...] = ()
    image_limitations: tuple[ImageLimitationRecord, ...] = ()
    possible_multiple_taxa: bool = False
    instruction_like_content_detected: bool = False


class PlanRecord(Contract):
    """The inspection plan after deterministic facts were merged in. No planner prose."""

    target_features: tuple[str, ...] = ()
    expect_multiple_subjects: bool = False
    bark_only_input: bool = False
    split_firewood_input: bool = False


class ReviewFindingRecord(Contract):
    """One finding as adjudication left it."""

    finding_ref: str | None = Field(
        default=None,
        max_length=120,
        description=(
            "Name of this finding, unique within the run. Derivations cite it, because "
            "`finding_id` is the reviewer's own numbering and reviewers reuse numbers."
        ),
    )
    finding_id: str = Field(max_length=120)
    origin: str = Field(max_length=40)
    category: str = Field(max_length=60)
    severity: str = Field(max_length=40)
    status: str = Field(max_length=40)
    reason_code: str | None = Field(default=None, max_length=60)
    summary: str = Field(max_length=400)
    evidence_ids: tuple[str, ...] = ()
    raised_subject_id: str | None = Field(
        default=None,
        max_length=120,
        description="The subject the finding named as raised, before scope resolution.",
    )
    subject_id: str | None = Field(
        default=None,
        max_length=120,
        description="The subject adjudication resolved it to; decision rules read this one.",
    )
    proposed_taxon: str | None = Field(default=None, max_length=120)
    required_action: str = Field(max_length=40)
    impact: str = Field(max_length=40)


class ReviewerRecord(Contract):
    """One review: what the reviewer returned and what adjudication made of it."""

    reviewer: str = Field(max_length=40)
    status: str = Field(max_length=40)
    subject_id: str | None = Field(default=None, max_length=120)
    recommended_resolution: Resolution | None = None
    recommended_confidence: Confidence | None = None
    recommendation_admitted: bool | None = Field(
        default=None,
        description=(
            "Whether the recommended levels were admitted as bounds. None when the review "
            "recommended no level. False when they bound nothing: every model finding the "
            "review raised was rejected, or no subject could be resolved for it."
        ),
    )
    recommended_candidates: tuple[CandidateSupportRecord, ...] = Field(
        default=(),
        max_length=MAX_REVIEW_CANDIDATES,
        description="The ranking the reviewer proposed, as returned and before validation.",
    )
    findings: tuple[ReviewFindingRecord, ...] = Field(default=(), max_length=MAX_REVIEW_FINDINGS)
    findings_omitted: int = Field(
        default=0, ge=0, description="Findings beyond the record's bound. Zero in practice."
    )


class RecommendationRecord(Contract):
    """One admitted recommendation: a level a review may hold a subject's claim to."""

    reviewer: str | None = Field(default=None, max_length=40)
    subject_id: str = Field(max_length=120)
    resolution: Resolution | None = None
    confidence: Confidence | None = None
    finding_id: str | None = Field(
        default=None,
        max_length=120,
        description=(
            "The accepted model finding the recommendation is bound to. None is a bare "
            "recommendation, which still caps the claim."
        ),
    )
    finding_ref: str | None = Field(default=None, max_length=120)


class RerankRecord(Contract):
    """One validated ranking admitted with the finding that asked for it."""

    reviewer: str = Field(max_length=40)
    subject_id: str = Field(max_length=120)
    finding_id: str = Field(max_length=120)
    finding_ref: str | None = Field(default=None, max_length=120)
    ranking: tuple[str, ...] = ()


class ReviewRoundRecord(Contract):
    """One review pass: every review in it, and what adjudication admitted.

    Recorded per round because a correction retry clears the reviews from the graph state.
    The round that asked for the retry is otherwise gone by the time the run ends.
    """

    stage: Literal["internal", "arbiter"]
    round: int = Field(ge=1)
    evidence_revision: int | None = Field(
        default=None, ge=1, description="The extraction pass this round reviewed."
    )
    reviewers: tuple[ReviewerRecord, ...] = ()
    recommendations: tuple[RecommendationRecord, ...] = ()
    reranks: tuple[RerankRecord, ...] = ()
    retry_required: bool = False
    unresolvable: bool = False
    unverified_abstention: bool = False
    reviewer_disagreement: bool = False
    escalation_recommended: bool = False


AbstentionCause = Literal[
    # A critical finding that asks to withdraw the claim and rests on code or cited evidence.
    "unresolvable_finding",
    # A finding asked for re-extraction and the retry budget was already spent.
    "retry_budget_spent",
    # A model asserted abstention without evidence and the arbiter's own findings agreed.
    "unverified_abstention_confirmed",
    # The same, with no arbitration to confirm or refuse it: withdrawn to fail closed.
    "unverified_abstention_unarbitrated",
    "unspecified",
]


class AbstentionRecord(Contract):
    """Why one subject's claim was withdrawn, and which findings asked for it."""

    subject_id: str = Field(max_length=120)
    resolution: Resolution = Field(description="The bound abstention placed on the subject.")
    cause: AbstentionCause
    findings: tuple[FindingReference, ...] = ()


class DecisionComparison(Contract):
    """Two sets of per-subject verdicts, compared subject by subject.

    A subject with a verdict on one side only is listed, not counted as a change. The
    provisional verdicts exist only for subjects that had candidates, while the final ones
    cover every subject, so a subject that never had a candidate appears on one side by
    construction and says nothing about what the step in between did.
    """

    compared_subject_ids: tuple[str, ...] = ()
    changed_subject_ids: tuple[str, ...] = Field(
        default=(),
        description="Compared subjects whose status, taxon, resolution or confidence moved.",
    )
    only_before_subject_ids: tuple[str, ...] = ()
    only_after_subject_ids: tuple[str, ...] = ()


class RunTrace(Contract):
    """The inspectable record of one graph run."""

    case_id: str = Field(max_length=120)
    graph_version: str = GRAPH_VERSION
    trace_schema_version: int = Field(default=TRACE_SCHEMA_VERSION, ge=1)
    code_commit_sha: str | None = Field(
        default=None,
        min_length=40,
        max_length=64,
        pattern=r"^[0-9a-f]+$",
        description="Immutable VCS revision, when the runtime can discover one.",
    )
    code_dirty: bool | None = Field(
        default=None,
        description=(
            "Whether tracked or untracked repository files differed from code_commit_sha "
            "when the run began; None means VCS identity was unavailable."
        ),
    )
    domain_prompt: PromptMetadata | None = None
    providers: dict[str, str] = Field(
        default_factory=dict,
        description="role -> 'adapter:model'. Never a credential.",
    )
    events: tuple[NodeEvent, ...] = ()
    component_projections: tuple[ComponentProjection, ...] = ()
    knowledge_coverage: KnowledgeCoverage | None = Field(
        default=None,
        description=(
            "What this run observed that no knowledge card can represent. Recorded on every "
            "run, including clean ones, so a suite can aggregate it — a field that appears "
            "only when something is wrong cannot be a denominator. Without it a failed case "
            "cannot be attributed between a model that did not see and cards that could not "
            "accept what it saw."
        ),
    )
    evidence_observations: tuple[ObservationRecord, ...] = Field(
        default=(),
        max_length=MAX_LEDGER_OBSERVATIONS,
        description=(
            "The final evidence packet's observations: which subject each was attributed "
            "to, how it was read, and whether it was trusted to support an identification."
        ),
    )
    candidate_validations: tuple[CandidateValidationRecord, ...] = Field(
        default=(),
        max_length=MAX_LEDGER_SUBJECTS,
        description=(
            "Per subject, the support validation admitted and the support it refused, with "
            "a machine-readable reason for each refusal."
        ),
    )
    evidence_context: EvidenceContextRecord | None = Field(
        default=None,
        description=(
            "The final packet's subjects, inferences, limitations and flags. None when no "
            "packet was extracted."
        ),
    )
    evidence_history: tuple[EvidenceRevision, ...] = Field(
        default=(),
        description=(
            "Every extraction's raw and effective packets, with content hashes and the "
            "original candidate proposal. Includes notes; handle as inspection artifacts. "
            "Empty in traces written before schema version 3."
        ),
    )
    candidate_validation_history: tuple[CandidateValidationRecord, ...] = ()
    card_retrievals: tuple[CardRetrieval, ...] = ()
    vision_shadow: VisionShadowResult | None = None
    observation_uses: tuple[ObservationUseRecord, ...] = ()
    plan: PlanRecord | None = None
    review_rounds: tuple[ReviewRoundRecord, ...] = Field(
        default=(),
        max_length=MAX_REVIEW_ROUNDS,
        description=(
            "Every review pass in the order it ran: each internal round, including one a "
            "correction retry superseded, then the arbiter's. Each review's findings carry "
            "their adjudicated status and reason, so a downgrade can be traced to the "
            "reviewer and finding behind it."
        ),
    )
    abstentions: tuple[AbstentionRecord, ...] = ()
    retries: int = Field(default=0, ge=0)
    graph_retry_count: int = Field(
        default=0,
        ge=0,
        description="Graph correction-loop retries; distinct from provider validation attempts.",
    )
    correction_changed_outcome: bool | None = None
    correction_changed_status: bool | None = None
    correction_changed_taxon: bool | None = None
    correction_changed_resolution: bool | None = None
    correction_changed_confidence: bool | None = None
    provisional_decisions: tuple[FinalDecision, ...] = Field(
        default=(),
        description="Deterministic verdicts immediately before any arbiter call.",
    )
    correction_comparison: DecisionComparison | None = Field(
        default=None,
        description=(
            "Which subjects the `correction_changed_*` flags compared. A re-extraction may "
            "rename subjects, so a verdict present on one side only is listed here."
        ),
    )
    arbiter_changed_status: bool | None = None
    arbiter_changed_taxon: bool | None = None
    arbiter_changed_resolution: bool | None = None
    arbiter_changed_confidence: bool | None = None
    arbiter_comparison: DecisionComparison | None = Field(
        default=None,
        description=(
            "Which subjects the `arbiter_changed_*` flags compared. Those flags describe "
            "subjects with a verdict both before and after arbitration, and nothing else."
        ),
    )
    authority_checks: tuple[AuthorityCheckTrace, ...] = Field(
        default=(),
        description=(
            "One attachment-authority record per subject. A run with two subjects has two "
            "records; flattening them produced critical evidence ids from one subject "
            "beside a counterfactual taxon from another, describing no world that existed."
        ),
    )
    decision_derivations: tuple[DecisionDerivation, ...] = Field(
        default=(),
        description="One deterministic composition record per final subject verdict.",
    )
    user_claim_negated: bool = Field(
        default=False,
        description=(
            "The user's claim named a taxon only to deny it. Nothing was offered to rule "
            "on, so the verdict is `possible` rather than a ruling against the denied taxon."
        ),
    )
    evidence_authority_sensitive: bool = Field(
        default=False,
        description="Convenience aggregate: any subject's check came back sensitive.",
    )
    escalation_triggered: bool = False
    escalation_reasons: tuple[str, ...] = ()
    escalation_hard_reasons: tuple[str, ...] = Field(
        default=(),
        description="The reasons no cost suppressor may override. A subset of the above.",
    )
    escalation_suppressed_by: tuple[str, ...] = Field(
        default=(),
        description="Why arbitration did not run although reasons were present.",
    )
    arbiter_used: bool = False
    final_resolution: Resolution | None = None
    final_confidence: Confidence | None = None
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    duration_ms: float | None = Field(default=None, ge=0)
    critical_path_ms: float | None = Field(
        default=None,
        ge=0,
        description=(
            "Wall time no amount of concurrency could remove: every serial node plus the "
            "slowest member of each fan-out round. Compare against duration_ms to see what "
            "running the reviewers together actually bought."
        ),
    )

    @property
    def executed_nodes(self) -> tuple[str, ...]:
        return tuple(event.node for event in self.events)
