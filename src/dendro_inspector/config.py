"""Runtime configuration.

Configuration is data. No business logic reads ``os.environ`` directly, and no commercial
model name appears in a node — nodes ask for the logical roles ``primary`` and ``arbiter``
and get whatever the registry is configured to hand them.
"""

from __future__ import annotations

import os
from enum import StrEnum
from pathlib import Path

from pydantic import Field

from dendro_inspector.schemas.base import Contract

DEFAULT_DOMAIN_PROMPT_PATH = Path("prompts/domain/system-prompt.md")
DEFAULT_PROMPT_MANIFEST_PATH = Path("prompts/versions.yaml")
DEFAULT_KNOWLEDGE_ROOT = Path("knowledge")
DEFAULT_NODE_PROMPT_ROOT = Path("prompts/nodes")
DEFAULT_PERSONALITY_ROOT = Path("prompts/personality")


class Role(StrEnum):
    """Logical model roles. Business logic only ever names these."""

    PRIMARY = "primary"
    ARBITER = "arbiter"


class Adapter(StrEnum):
    FAKE = "fake"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    OLLAMA = "ollama"
    GEMINI = "gemini"
    NVIDIA = "nvidia"
    OPENROUTER = "openrouter"


# Which credential each adapter reads by default. The role does not decide this — binding
# `arbiter` to Anthropic once did not make ANTHROPIC_API_KEY the arbiter's key forever, and
# reading the wrong variable surfaces as a missing-credential error for a key that is set.
DEFAULT_KEY_ENV: dict[Adapter, str] = {
    Adapter.OPENAI: "OPENAI_API_KEY",
    Adapter.ANTHROPIC: "ANTHROPIC_API_KEY",
    Adapter.GEMINI: "GEMINI_API_KEY",
    Adapter.NVIDIA: "NVIDIA_API_KEY",
    Adapter.OPENROUTER: "OPENROUTER_API_KEY",
}


class ProviderConfig(Contract):
    """How one logical role is satisfied at runtime."""

    adapter: Adapter = Adapter.FAKE
    model: str | None = Field(default=None, max_length=120)
    api_key_env: str | None = Field(default=None, max_length=120)
    scenario: str | None = Field(
        default=None,
        max_length=120,
        description="Fake adapter only: which evals/fixtures/<scenario>.json script to replay.",
    )
    max_structured_retries: int = Field(default=1, ge=0, le=3)


class EscalationPolicy(Contract):
    """When the independent arbiter is called (spec section 4).

    Every trigger is individually switchable so an operator can tune cost against risk
    without editing code, and so evaluation can measure escalation precision and recall.
    """

    enabled: bool = True

    # Triggers — any one of these firing is sufficient.
    on_species_resolution: bool = True
    on_high_confidence: bool = True
    on_close_leading_candidates: bool = True
    on_reviewer_disagreement: bool = True
    on_unresolved_contradiction: bool = True
    on_bark_colour_dependence: bool = True
    on_multiple_taxa: bool = True
    on_user_challenge: bool = True
    on_bark_only_input: bool = True
    on_instruction_like_content: bool = True
    forced_by_eval_case: bool = False

    # Suppressors — checked first; any one of these blocks escalation entirely.
    suppress_when_insufficient_evidence: bool = True
    suppress_when_abstaining: bool = True
    suppress_when_broad_and_low_risk: bool = True
    suppress_when_clean_and_medium_confidence: bool = True


class GraphConfig(Contract):
    """Execution limits. The retry budget is what makes the graph provably terminating."""

    retry_budget: int = Field(default=1, ge=0, le=3)
    max_steps: int = Field(default=64, ge=8, le=512)
    min_observations_for_candidates: int = Field(default=2, ge=1)
    require_non_colour_evidence: bool = True


class ObservabilityConfig(Contract):
    log_level: str = Field(default="INFO", max_length=10)
    trace_dir: Path | None = None
    emit_json_logs: bool = True


class ToneProfile(StrEnum):
    """Presentation register. Deployment choice, not a scientific one.

    Standard is the default so contributors, demos and corporate deployments do not inherit
    a register nobody chose. Selecting the direct profile changes only the vocabulary
    and the register note sent to the model — never a taxon, level or confidence.
    """

    STANDARD = "standard"
    DIRECT = "direct"


class PromptConfig(Contract):
    domain_prompt_path: Path = DEFAULT_DOMAIN_PROMPT_PATH
    manifest_path: Path = DEFAULT_PROMPT_MANIFEST_PATH
    node_prompt_root: Path = DEFAULT_NODE_PROMPT_ROOT
    personality_root: Path = DEFAULT_PERSONALITY_ROOT
    tone_profile: ToneProfile = ToneProfile.STANDARD


class KnowledgeConfig(Contract):
    root: Path = DEFAULT_KNOWLEDGE_ROOT
    region_pack: str | None = "eastern-europe"


class AppConfig(Contract):
    """The whole runtime configuration, assembled once per process."""

    providers: dict[Role, ProviderConfig] = Field(
        default_factory=lambda: {
            Role.PRIMARY: ProviderConfig(),
            Role.ARBITER: ProviderConfig(),
        }
    )
    escalation: EscalationPolicy = EscalationPolicy()
    graph: GraphConfig = GraphConfig()
    prompts: PromptConfig = PromptConfig()
    knowledge: KnowledgeConfig = KnowledgeConfig()
    observability: ObservabilityConfig = ObservabilityConfig()
    fixtures_root: Path = Path("evals/fixtures")

    def provider_for(self, role: Role) -> ProviderConfig:
        try:
            return self.providers[role]
        except KeyError as exc:  # pragma: no cover - configuration error path
            msg = f"no provider configured for role {role.value!r}"
            raise KeyError(msg) from exc


def load_dotenv(path: Path | None = None) -> None:
    """Populate ``os.environ`` from a ``.env`` file, without overriding what is already set.

    `README.md` and `.env.example` have always told the reader to copy the example to
    `.env`; nothing read it back, so the file was decoration. Fifteen lines of parsing beat
    a dependency for that (§14), and the real environment still wins — an exported variable
    is a deliberate act, a file on disk is a default.

    Deliberately not a dotenv implementation: no interpolation, no `export` prefixes, no
    multi-line values. A key, an `=`, and a value.

    Called from the CLI entrypoint rather than from :func:`load_config`, so that importing
    this package never reads a file. A test that builds configuration would otherwise pick
    up whatever provider the developer happens to have in `.env`, and §10's guarantee that
    every gate runs on the fake provider would hold only on machines without one.
    """
    target = path or Path(".env")
    if not target.is_file():
        return
    for line in target.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, raw = stripped.partition("=")
        key = name.strip()
        if not key or key in os.environ:
            continue
        value = raw.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ[key] = value


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def load_config(overrides: AppConfig | None = None) -> AppConfig:
    """Build configuration from the environment.

    ``overrides`` wins outright when supplied — tests and the CLI use it rather than
    mutating process state.
    """
    if overrides is not None:
        return overrides

    scenario = _env("DENDRO_FAKE_SCENARIO", "primary-pass")
    # A hosted frontier model satisfies these contracts first time; a smaller one often needs
    # the validation error handed back to it more than once. That is a property of the model,
    # not of the code, so it belongs in configuration.
    retries = int(_env("DENDRO_STRUCTURED_RETRIES", "1") or "1")
    primary_adapter = Adapter(
        _env("DENDRO_PRIMARY_PROVIDER", Adapter.FAKE.value) or Adapter.FAKE.value
    )
    arbiter_adapter = Adapter(
        _env("DENDRO_ARBITER_PROVIDER", Adapter.FAKE.value) or Adapter.FAKE.value
    )
    primary = ProviderConfig(
        adapter=primary_adapter,
        model=_env("DENDRO_PRIMARY_MODEL"),
        api_key_env=DEFAULT_KEY_ENV.get(primary_adapter),
        scenario=scenario,
        max_structured_retries=retries,
    )
    arbiter = ProviderConfig(
        adapter=arbiter_adapter,
        model=_env("DENDRO_ARBITER_MODEL"),
        api_key_env=DEFAULT_KEY_ENV.get(arbiter_adapter),
        scenario=scenario,
        max_structured_retries=retries,
    )
    trace_dir = _env("DENDRO_TRACE_DIR")

    return AppConfig(
        providers={Role.PRIMARY: primary, Role.ARBITER: arbiter},
        prompts=PromptConfig(
            domain_prompt_path=Path(
                _env("DENDRO_DOMAIN_PROMPT_PATH", str(DEFAULT_DOMAIN_PROMPT_PATH))
                or str(DEFAULT_DOMAIN_PROMPT_PATH)
            ),
            manifest_path=Path(
                _env("DENDRO_PROMPT_MANIFEST_PATH", str(DEFAULT_PROMPT_MANIFEST_PATH))
                or str(DEFAULT_PROMPT_MANIFEST_PATH)
            ),
            tone_profile=ToneProfile(
                _env("DENDRO_TONE_PROFILE", ToneProfile.STANDARD.value)
                or ToneProfile.STANDARD.value
            ),
        ),
        knowledge=KnowledgeConfig(
            root=Path(_env("DENDRO_KNOWLEDGE_ROOT", str(DEFAULT_KNOWLEDGE_ROOT)) or "knowledge"),
            region_pack=_env("DENDRO_REGION_PACK", "eastern-europe"),
        ),
        observability=ObservabilityConfig(
            log_level=_env("DENDRO_LOG_LEVEL", "INFO") or "INFO",
            trace_dir=Path(trace_dir) if trace_dir else None,
        ),
    )
