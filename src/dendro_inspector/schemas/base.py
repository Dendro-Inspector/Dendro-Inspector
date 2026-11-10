"""Shared contract base and constrained primitive types.

Every contract in this package is frozen and rejects unknown fields. Two consequences
matter for the graph:

* nodes cannot smuggle state by mutating an object another node holds;
* a model that invents a field fails validation instead of silently widening a contract.

The constrained string types exist to enforce the project's central rule at the type
level: structured fields carry structured tokens, never prose. ``"This is Pinus because
the bark is red"`` cannot be stored as an observation value, because it does not match
``VALUE_TOKEN_PATTERN``.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

# ``bark.flake_geometry``, ``needles.fascicles`` — a namespaced, machine-comparable feature.
FEATURE_PATH_PATTERN = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$"

# ``thin_irregular_edge_lifting``, ``two``, ``5-7`` — a token, never a sentence.
VALUE_TOKEN_PATTERN = r"^[a-z0-9][a-z0-9_.\-]*$"

# ``obs_1``, ``foreground_log_1``, ``pinus`` — a stable identifier.
IDENTIFIER_PATTERN = r"^[a-z0-9][a-z0-9_\-]*$"

#: The format rule is also stated as a description, because a description reaches every
#: provider's schema while ``pattern`` does not: some structured-output dialects drop regex
#: constraints, and a model that never sees the rule writes the sentence it was thinking.
#: Pydantic still enforces the pattern on the response either way.
FEATURE_PATH_RULE = (
    "A dotted lowercase feature path, e.g. `bark.peeling`, `leaf.underside`. Letters, "
    "digits and `_` in each part; no spaces, no sentences."
)
VALUE_TOKEN_RULE = (
    "One lowercase machine token: letters, digits, `_`, `.` or `-`, starting with a letter "
    "or digit, e.g. `location_unknown`, `thin_layers`, `5-7`. Never a phrase or a sentence: "
    "join words with `_`, and put any description in a free-text `notes` field instead."
)

FeaturePath = Annotated[
    str,
    StringConstraints(pattern=FEATURE_PATH_PATTERN, max_length=120),
    Field(description=FEATURE_PATH_RULE),
]
ValueToken = Annotated[
    str,
    StringConstraints(pattern=VALUE_TOKEN_PATTERN, max_length=120),
    Field(description=VALUE_TOKEN_RULE),
]
Identifier = Annotated[str, StringConstraints(pattern=IDENTIFIER_PATTERN, max_length=120)]

#: A model-authored *pointer* at evidence, deliberately not an :data:`Identifier`.
#:
#: An identifier names something this system owns; a reference is a claim that something
#: exists elsewhere, and the claim may simply be wrong. Adjudication already resolves every
#: reference against the packet and discards what it cannot project, so a syntactically
#: malformed reference and an unknown-but-well-formed one are the same event: a pointer to
#: nothing. Enforcing the identifier pattern here made only the first of the two fatal,
#: killing the whole case at the parser before code could adjudicate anything. Length is
#: still bounded, because an unbounded string is a different problem.
EvidenceRef = Annotated[str, StringConstraints(max_length=120)]
ShortText = Annotated[str, StringConstraints(min_length=1, max_length=400)]


class Contract(BaseModel):
    """Immutable, closed-world base model for every data contract."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
        use_enum_values=False,
    )
