"""The spend limit of one inspection.

A run has stages it must complete to have anything to say (plan, extraction, candidates,
the first review round) and stages it may add: a correction retry, which repeats extraction,
candidate generation and review, and the arbiter. The limit applies to the second kind only.
A stage of that kind starts while it fits under the limit and is left out otherwise, and the
run then answers from what it already has, through the same paths a spent retry budget and a
suppressed arbiter already take. Reaching the limit is never a failure and never a provider
error dressed as uncertainty: the trace and the answer both say that a stage was not run.

Whether a stage fits is decided before it starts, so a stage that has started finishes. Its
size in requests is known from the graph. Its size in tokens is not known in advance and is
taken from the same run: a retry is expected to cost what the round before it cost, and the
arbiter what the largest reviewer call did. Repair attempts inside a started stage are
counted afterwards and are why a limit can be exceeded by part of one stage.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Final

from dendro_inspector.graph.definition import NodeName

if TYPE_CHECKING:
    from dendro_inspector.config import GraphConfig
    from dendro_inspector.observability.trace import RunSpend


class Stage(StrEnum):
    """A stage the spend limit may leave out. The value is what the state and trace record."""

    CORRECTION_RETRY = "correction_retry"
    ARBITRATION = "arbitration"


#: How a stop is named where reasons are recorded: an abstention cause, an escalation
#: suppressor.
SPEND_LIMIT_REACHED: Final = "spend_limit_reached"

_REVIEWERS = (
    NodeName.BOTANICAL_REVIEWER.value,
    NodeName.CONFUSION_REVIEWER.value,
    NodeName.CONFIDENCE_REVIEWER.value,
)
#: The model calls one correction retry repeats.
_RETRY_ROUND = (
    NodeName.EVIDENCE_EXTRACTOR.value,
    NodeName.CANDIDATE_GENERATOR.value,
    *_REVIEWERS,
)


def stage_size(stage: Stage, spend: RunSpend) -> tuple[int, int]:
    """Requests and tokens a stage is expected to take, from this run's own last calls."""
    if stage is Stage.CORRECTION_RETRY:
        return len(_RETRY_ROUND), sum(spend.latest_tokens.get(node, 0) for node in _RETRY_ROUND)
    return 1, max((spend.latest_tokens.get(node, 0) for node in _REVIEWERS), default=0)


def affords(config: GraphConfig, spend: RunSpend, stage: Stage) -> bool:
    """Whether ``stage`` still fits under the configured limits. No limit affords anything."""
    requests, tokens = stage_size(stage, spend)
    if config.max_model_calls is not None and spend.requests + requests > config.max_model_calls:
        return False
    return config.max_tokens is None or spend.tokens + tokens <= config.max_tokens
