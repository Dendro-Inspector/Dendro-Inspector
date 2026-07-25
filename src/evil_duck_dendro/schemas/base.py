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

from pydantic import BaseModel, ConfigDict, StringConstraints

# ``bark.flake_geometry``, ``needles.fascicles`` — a namespaced, machine-comparable feature.
FEATURE_PATH_PATTERN = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$"

# ``thin_irregular_edge_lifting``, ``two``, ``5-7`` — a token, never a sentence.
VALUE_TOKEN_PATTERN = r"^[a-z0-9][a-z0-9_.\-]*$"

# ``obs_1``, ``foreground_log_1``, ``pinus`` — a stable identifier.
IDENTIFIER_PATTERN = r"^[a-z0-9][a-z0-9_\-]*$"

FeaturePath = Annotated[str, StringConstraints(pattern=FEATURE_PATH_PATTERN, max_length=120)]
ValueToken = Annotated[str, StringConstraints(pattern=VALUE_TOKEN_PATTERN, max_length=120)]
Identifier = Annotated[str, StringConstraints(pattern=IDENTIFIER_PATTERN, max_length=120)]
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
