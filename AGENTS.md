# Agent Guidelines for Dendro Inspector

- **Status:** Draft
- **Owner:** Dendro Inspector maintainers
- **Date:** 2026-07-25
- **Last-verified:** 2026-07-25

**This is a public open-source repository.** Everything committed here is permanent,
world-readable, and mirrored by third parties within minutes. Sections 13-15 carry the
rules that exist *only* because of that; they override anything softer above them.

Distilled from the Azure Subscription Vending - EA repository (`AGENTS.md` §§0-14,
`FABLE.md`, `skills/hard-problem-method`, `skills/verify-before-report`,
`docs/DOCUMENTATION-CREATION-PROCESS.md`, `.githooks/pre-commit.ps1`, commit history).
Project-specific Azure/vending content was dropped; the enforcement shape was kept.

---

## The Main Rule

**Every rule and every contract has exactly one canonical home, and each one is backed
by a gate that can fail. A claim without evidence is a hypothesis — label it as one.**

Everything below is that rule applied to a surface: instructions, code, docs, commits,
status reports. Two sources of truth is the bug this file exists to prevent.

---

## 0. Instruction Boundary

`AGENTS.md` is the single source of truth for agent behavior and project instructions.
Tool entrypoints (`CLAUDE.md`, `.github/copilot-instructions.md`, `AGENTS.md` mirrors)
are bootstrap pointers and **must not** duplicate operational rules. If a rule changes,
change it here.

Corollary: do not add a rule to this file that you cannot point at a gate, a reviewer,
or an explicit owner decision for. Unenforced prose decays into folklore.

---

## 1. Operating Principles (How to Work Here)

1. **Think before coding.** State assumptions explicitly. When a task is ambiguous,
   present the interpretations before editing. Verify against the actual tree
   (`git ls-files`, file reads) — session memory and status docs go stale.
2. **Simplicity first.** The minimum change that solves the stated problem. No
   speculative features, no premature abstraction.
3. **Surgical changes.** Touch only what the task requires. Preserve existing style,
   comments, and load-bearing fallbacks. Never reformat a whole file — a line-ending
   flip buries a 4-line change in a 700-line diff.
4. **Goal-driven execution.** Define verifiable success criteria before editing, run the
   matching gates, and report a status from §3 — never "done" without evidence.
5. **Stale-knowledge rule.** Re-verify any fact sourced from memory, a review doc, or an
   earlier message by live read or grep before acting on it or repeating it.
6. **Do not loop.** Two or three failed attempts on one approach means stop, re-read the
   evidence, re-hypothesize. For incidents: write down ≥3 candidate causes, then pick the
   cheapest test that *discriminates* between the top two — not the one that confirms
   your favorite. Never fix on an unconfirmed hypothesis.

---

## 2. Authorization Boundaries

Operations with real-world side effects require explicit owner approval **in the current
session**; approval in a previous session does not carry over:

- Deploying, releasing, or triggering CI/CD pipelines.
- Cloud CLI / control-plane / data-plane mutations.
- Anything sending data to an external service.
- `git push`, force-push, and branch deletion.

Public-repo additions — these are irreversible in practice, because the internet keeps a
copy:

- Pushing to a public branch, opening or commenting on a public issue or PR, publishing a
  release or tag, publishing a package to any registry (npm, PyPI, crates.io, …).
- Any action that speaks in the project's voice to people outside the maintainer group.
- Adding, changing, or removing a dependency (§14).

Never run `git clean`, `git reset --hard`, or broad restore commands. Never delete
untracked files unless explicitly asked. If verification requires an operation you are
not authorized for, report `BLOCKED` with the exact command for the owner to run.

---

## 3. Evidence-Gated Reporting (Mandatory Vocabulary)

Optimistic reporting is the top failure mode of agent sessions. Use these statuses:

| Status | Meaning | Requires |
|---|---|---|
| `VERIFIED` | claim plus evidence artifact | command output, run ID, query result, or diff |
| `IMPLEMENTED-UNVERIFIED` | edit made, validation not run | name the missing validation and why it did not run |
| `UNKNOWN` | no evidence either way | name what would produce evidence |
| `BLOCKED` | verification needs access you lack | the exact command for the owner |

Forbidden without evidence: *done, works, should work, successfully, wired, fixed,
everything passes*.

Report format:

```text
Claim:
Status: VERIFIED | IMPLEMENTED-UNVERIFIED | UNKNOWN | BLOCKED
Evidence: <command + result | file:line | run id>
Not verified:
Next verification step:
```

Anti-patterns: running a syntax gate and claiming semantic safety; citing a review doc,
a memory entry, or your own earlier message as evidence for current code state;
retro-fitting evidence to a conclusion already written; "tested the happy path" reported
as `VERIFIED` for error handling.

---

## 4. Hard Requirements

Each hard requirement follows one shape — copy it when adding a new one:

```markdown
### N.M <Rule name>
<File or component> is the **source of truth** for <contract>.
<What must change together, in the same commit.>

**Mandatory validation:**
```<shell>
<gate command>
```
Do not merge if validation fails.
```

Seed requirements (fill the gate commands once the stack exists):

### 4.1 One definition per artifact
There is one source-controlled definition per component. No parallel dev/prod copies to
diff. Environment differences live **only** in config (parameters / app settings / env
files) and are referenced by name — never as a branch inside the artifact itself.

### 4.2 Naming consistency
Pick a canonical name and casing per interface and use it everywhere: code, config,
scripts, docs. If a transformation between systems is required, implement it in one
mapping layer and document it. Do not introduce aliases without updating docs and
validators. When the convention is unclear, ask before renaming.

### 4.3 Contract alignment
When a request/response, schema, or public interface changes, the schema files, the
consumers, and the docs change in the same commit. A gate proves it.

### 4.4 Gate registry
Every path referenced from this file and from `skills/` must resolve to a tracked file.
A validator enforces it — backticked paths are invisible to link checkers, and rotted
references are how instruction files lose authority.

### 4.5 The gates

Run all five before opening a pull request. CI runs the same set on every push and pull
request, including from forks.

```bash
ruff format --check .              # formatting
ruff check .                       # lint
mypy                               # strict on src, relaxed on tests
pytest                             # unit, contract, integration, evaluation
evil-duck eval --suite public      # the public conformance suite
```

Do not merge if any of these fail. The evaluation suite is fully deterministic — a red case
is a real signal, never flakiness.

How many cases the suite holds is not recorded here. It changed three times in two days
while this line still said "the five evaluation cases", which is how a file that claims to be
the single source of truth starts being read as decoration. Counts live where they can be
counted: `evals/public/` and `docs/evaluation.md`.

### 4.6 The determinism boundary

**A model proposes; code adjudicates.** Every decision that could inflate a claim — whether
evidence suffices, whether a finding is admissible, whether to escalate, the final
resolution and confidence, and what the user is told — is deterministic code, testable
without a provider, identical across runs.

Moving any of those into a model call is a rejected change unless the case for it is made
explicitly and agreed first. See `docs/architecture.md`, "Determinism boundary".

---

## 5. Change Discipline

- Validate changed JSON/YAML/config syntax before committing; surface-specific gates in
  §4 remain mandatory and are not replaced by a generic parser check.
- Verify a path exists before citing or editing it.
- Prefer editing an existing file over creating a parallel source of truth. Never create
  `NEW`, `FINAL`, `LATEST`, or `V2` copies to avoid editing the authoritative file.
- After a bulk rename or restructure, do a second pass for stale paths and broken
  cross-references, and run the link validator.
- Before removing a guard, fallback, or defensive branch, check the producer contract
  that made it necessary. Defensive code is usually a scar, not decoration.
- No drive-by refactors mixed into a fix.

---

## 6. Commit Conventions

- **No AI co-author trailers of any kind.** Never add `Co-Authored-By: Claude`,
  `Co-authored-by: Copilot <*+Copilot@users.noreply.github.com>`, or equivalents.
  Commits are authored by the developer.
- Subject line: `type(scope): imperative summary` — `feat`, `fix`, `docs`, `chore`,
  `refactor`, `test`. Scope is the touched surface. Bare `type: subject` is acceptable
  when no scope applies.
- One logical change per commit; each commit independently reviewable and revertable.
- Do not commit screenshots, binaries, generated output, or secrets.
- Commit messages record *what changed and why*, not session narration.

Public-repo additions:

- **No internal references.** No employer or client names, internal hostnames, ticket
  IDs, wiki links, Slack/Teams threads, internal email addresses, or real user data — in
  commit messages, code comments, test fixtures, or docs. If an outside reader cannot
  follow the reference, it does not belong in the commit.
- **Write for strangers.** The commit message is read by people with no access to the
  conversation that produced it. "Fix the thing we discussed" is not a commit message.
- A commit rewriting history on a public branch requires owner approval; assume someone
  has already pulled it.
- If the project adopts DCO sign-off, `Signed-off-by:` is required on every commit and
  must name a real contributor. This does not weaken the no-AI-trailer rule above: sign-off
  asserts the human's right to submit, an AI trailer asserts authorship. **TODO:** decide
  DCO vs CLA vs neither (§15).

---

## 7. Documentation Standard

**Public-facing root files are exempt from the header standard below** and keep their
ecosystem-conventional form: `README.md`, `LICENSE`, `CONTRIBUTING.md`,
`CODE_OF_CONDUCT.md`, `SECURITY.md`, `CHANGELOG.md`, `AGENTS.md`, `CLAUDE.md`, and
`skills/*/SKILL.md`. Retrofitting a `Last-verified:` field onto a README breaks the
convention every drive-by contributor and every package registry expects. The two
audiences are different: root files serve users and contributors, `docs/**` serves
maintainers and agents.

Two lifecycle classes under `docs/**`:

- **Living documents** — everything except `docs/reviews/**` and `docs/archive/**`.
  Expected to reflect the current system.
- **Point-in-time documents** — `docs/reviews/**` and `docs/archive/**`. Dated
  assessments, evidence records, superseded state. An archive file is never a source.

Required header, directly under the H1, before the first `##`:

```markdown
- **Status:** <Current | Draft | Deprecated | Superseded by [doc](path)>
- **Owner:** <team or role>
- **Date:** <YYYY-MM-DD>
- **Last-verified:** <YYYY-MM-DD>
```

Point-in-time documents use freeform `Status` and **no** `Last-verified` — they describe
a historical assessment, not an ongoing claim of currency.

Rules:

- `Date` = created or last substantially revised. Link repairs and formatting do not
  bump it. `Last-verified` = the date content was actually checked against live
  behavior. **Never bump `Last-verified` without performing that check.**
- Prefer a team or role as `Owner` on living docs; a person's name goes stale the moment
  they change teams. Person names are fine on dated reviews.
- Location encodes authority (`reference/` canonical, `reviews/` dated-not-current,
  `archive/` superseded). Do not add an `authority` field, and do not add YAML front
  matter — nothing parses it and it becomes a second source of truth.
- Before creating a doc: search for the one that already owns the subject; update it if
  it can stay canonical. Create new only for a distinct audience, lifecycle, or owner.
  A document with no reader action is storage, not documentation.
- Link to the implementation instead of copying sections that will drift. Use relative
  links with exact path casing.
- Root level holds governance files and active registers only. Graduate a completed
  register to `docs/reviews/<NAME>-YYYY-MM-DD.md`.

---

## 8. Skill Routing

Local skills live in `skills/` — vendor-neutral, shared by every agent entrypoint. Tool
directories (`.claude/`, `.codex/`, `.github/`) hold **config and hook routing only**;
never a mirrored copy of a skill. One primary skill per session; one primary objective
per session.

| Skill | Use for |
|---|---|
| *(none yet — add as recurring workflows emerge)* | |

Do not vendor-fork this catalog. A mirrored skill drifts within a week.

Until a skill exists, the routing documents are `docs/architecture.md` (contracts and
layering), `docs/agent-graph.md` (nodes and routing), `docs/review-pipeline.md` (finding
admissibility), `docs/model-roles.md` (escalation), `docs/evaluation.md` (adding cases).

---

## 9. Agent Governance

When reasoning about agents, roles, capabilities, limits, or project rules:

- Use only definitions present in this repository, with `AGENTS.md` as the canonical
  catalog. Do not import assumptions from generic agent frameworks.
- Do not infer or invent missing behavior. If required behavior is absent, say: *"This
  behavior is not defined in AGENTS.md. Please provide or update the definition."*
- Cite the applicable `AGENTS.md` section in agent-related answers.
- Propose missing structures only when asked, and label them as proposals — never as
  active project rules.
- Sections are cross-referenced by number. **Never renumber them.**

---

## 10. Environment and Tools

- Detect the current shell before writing shell-specific syntax, quoting, or
  continuation characters. Primary shell here: **PowerShell 7+ on Windows**.
- Preserve existing line endings on edit. A CRLF→LF rewrite turns a 4-line change into
  an every-line diff.
- Python 3.12+ with a project venv at `.venv/`. Install with `pip install -e ".[dev]"`.
- No credential is needed for any gate. If a change makes a test require an API key, the
  change is wrong — every test runs against the fake provider.
- Vendor SDKs (`openai`, `anthropic`) are optional extras and are lazily imported. Do not
  assume either is installed.

---

## 11. Hook and Gate Enforcement

- One policy implementation, thin runtime adapters. Executable policy lives in
  `scripts/agent-hooks/`; `.claude/settings.json`, `.codex/hooks.json`, and
  `.github/hooks/*.json` carry **routing only** — no duplicated policy logic.
- Local pre-commit validates **staged blobs** (`git show :<path>`), not the working
  tree. Baseline checks worth having on day one: parse changed JSON, reject tabs in
  YAML, syntax-check scripts, scan for secrets (password/API key/token/private key/creds
  in URL), scan fixtures for real PII.
- **Secret and PII detection hard-fails in a public repo.** The source project ran these
  warn-only because a leak there was internal; here a leaked credential is public the
  instant it is pushed, and deleting the commit does not unpublish it. Warn-only is the
  wrong default when the blast radius is the internet.
- Tool hooks are defense-in-depth, not a security boundary: runtime trust, timeouts, and
  local config can bypass them. **In a public repo they are barely even that** — an
  external contributor's fork never installs your Git hook, and the first time you see
  their code is in a PR. Prose in `AGENTS.md` reaches maintainers and agents; it does not
  reach drive-by contributors at all.
- **Required status checks plus branch protection are the only real enforcement layer.**
  Any rule that must hold for outside contributions has to be a CI check on
  `pull_request`, or it does not hold. This is the main rule (§"The Main Rule") in its
  strongest form: unenforced governance in an open repo is decoration.
- CI on fork PRs runs without repository secrets and with a read-only token. Design gates
  so the correctness checks (build, lint, test, schema, secret scan) need no secrets, and
  isolate anything privileged into a separate maintainer-triggered workflow. Never wire
  `pull_request_target` with a checkout of untrusted PR code — that hands write-scope
  tokens to a stranger's branch.
- Prefer changed-files mode over full-repo mode for new gates: it can ship today without
  forcing a repository-wide backfill.

---

## 12. Project Facts

**Evil Duck Dendro Inspector** — an eval-driven, evidence-based, multimodal agent that
identifies trees, logs, bark, leaves, fruit, cones and wood from photographs.

The one loop it must do reliably:

```text
image + context -> evidence -> candidates -> review -> (arbitration) -> capped decision -> honest answer
```

Dendrology is the reference domain. The engineering subject is evidence handling, review,
calibrated uncertainty, evaluation and CI. **It is never forced to return a species.**

### Stack

| Layer | Choice |
|---|---|
| Language | Python 3.12+ |
| Contracts | Pydantic v2, frozen, `extra="forbid"` |
| CLI | Typer |
| Data | YAML (knowledge, eval cases), JSON (fixtures, traces) |
| Orchestration | Custom ~100-line executor. **No agent framework.** |
| Tests | pytest (no pytest-asyncio — tests call `asyncio.run`) |
| Lint / format | Ruff | 
| Types | mypy strict |
| CI | GitHub Actions |

### Key files

| File | Purpose | When to modify |
|---|---|---|
| `src/evil_duck_dendro/schemas/base.py` | Frozen contract base, constrained string types | Rarely — a change here touches everything |
| `src/evil_duck_dendro/schemas/evidence.py` | Observation vs inference; referential integrity | Evidence contract changes |
| `src/evil_duck_dendro/graph/definition.py` | The single graph declaration | Adding or rewiring a node |
| `src/evil_duck_dendro/graph/routing.py` | Pure routing; the termination argument | Changing a branch condition |
| `src/evil_duck_dendro/knowledge/evidence_hierarchy.py` | Tiers, ceilings, attachment rule, evidence trust projection — §2/§6 of the domain prompt | Only when the domain prompt changes |
| `src/evil_duck_dendro/knowledge/candidate_validation.py` | The candidate admission boundary, shared by generation and every reviewer ranking | Never widen it to admit a candidate a case needs |
| `src/evil_duck_dendro/nodes/final_decision.py` | The claim cap, identity selection and downgrade composition | Never loosen the cap to pass a test |
| `src/evil_duck_dendro/nodes/review_synthesizer.py` | Finding admissibility and rerank binding, shared with the arbiter | Adding a finding category |
| `src/evil_duck_dendro/nodes/escalation_gate.py` | Trigger / suppressor precedence | Tuning arbitration cost |
| `src/evil_duck_dendro/prompts/library.py` | Prompt loading, hashing and fail-closed policy validation | Effectively never — contract-tested |
| `prompts/versions.yaml` | The prompt/policy compatibility manifest | Only via `evil-duck prompt-seal --write` |
| `knowledge/taxa/*.yaml` | Taxon cards; `supported_resolution` caps claims | Adding a taxon (no code change) |
| `evals/public/*.yaml`, `evals/fixtures/*.json` | Deterministic evaluation | Adding a case |
| `prompts/domain/system-prompt.md` | **User artifact and primary knowledge source.** Never translate, shorten or reformat | Only the owner replaces it |

### Commands

```bash
pip install -e ".[dev]"            # setup
pytest                             # tests
evil-duck eval --suite public      # evaluation
evil-duck graph                    # render the executable graph
evil-duck inspect --fake primary-pass --image examples/log.jpg
evil-duck prompt-info              # which domain prompt is loaded, and its hash
evil-duck prompt-seal              # re-pin prompt hashes after the owner edits a prompt
```

`prompt-seal` is a dry run by default and needs `--write` to touch `prompts/versions.yaml`.
It re-attests bytes; it cannot attest that a changed prompt still means what the
deterministic policy implements. That judgement is §12's conformance review.

### The domain prompt is the behavioural specification

`prompts/domain/system-prompt.md` is the **authoritative behavioural specification** for
dendrology policy, and the system is derived from it.

When implementation behaviour differs from the active prompt version, treat the mismatch as
a **specification-conformance failure requiring review**. Do not automatically assume either
side is correct. Resolve it through tests, domain review, and an explicit change to the
specification or the implementation.

A prompt is a document, and documents carry errors, contradictions, stale rules, accidental
regressions, instructions that cannot be formally implemented, and requirements that
conflict with the data schema or with safety. "The prompt is always right" would make every
one of those unfixable, and would quietly turn a specification into scripture.

Conformance mismatches are resolved, not obeyed:

1. reproduce it as a failing test or evaluation case;
2. decide which side is wrong — with the owner, if the answer is a dendrology judgement;
3. change that side, and record which one moved in `CHANGELOG.md`.

The prompt file is still edited **only by its owner**. Deriving from it, disagreeing with
it, and raising a conformance failure against it are all permitted; rewriting it to make
code convenient is not.

Derivations to keep in step when it changes:

| Prompt section | Derived into |
|---|---|
| §2 evidence hierarchy, §6 confidence scale | `knowledge/evidence_hierarchy.py` |
| §3 checking the user's version | `final_decision.rule_on_user_claim` |
| §4 hard mode, §5 jokes, §12 self-correction | `response_composer.decide_tone` |
| §7-§11 response formats | `response_composer.select_format` |
| §13 failure cases | reviewer checks + the named failure-mode evaluation cases |
| §14 base taxa | `knowledge/taxa/*.yaml` **feature rules** |
| §16 when to ask for photographs | `photo_planner`, card `follow_up_evidence` |

Do not "improve" the prompt to make code easier. It is edited only by its owner.

Editing it does mean re-sealing: `prompts/versions.yaml` pins the prompt's hash, and the run
fails closed until `evil-duck prompt-seal --write` re-attests it. That re-attests bytes and
nothing else — the conformance review above is still owed.

Not everything in the derived files comes from the prompt. Family placements, `larix`, and
the response register do not, and say so in their own provenance. Where a derivation is
partial, the file records which parts it covers.

### Known limitations

- Knowledge is **demonstration content**: 25 taxa, no dendrologist review. 24 have feature
  rules from the domain prompt's §14; `larix` is not in the prompt at all.
- Node prompts ask for `bark.flake_geometry`, which no taxon card declares — evidence for it
  is recorded and then admits nothing. Unresolved: see
  `docs/reviews/CORRECTNESS-BOUNDARY-2026-07-26.md`.
- Not validated against real photographs at scale. The suite proves the machinery, not
  field accuracy.
- Provider adapters are integration boundaries, not hardened clients.
- Response composition is deterministic, not model-backed.
- Retry budget is 1, and global rather than per-node.
- Only `uk` and `en` output locales exist.

Full list, and what is deliberately deferred, in `CHANGELOG.md`.

---

## 13. Public Repository Constraints

The repository is world-readable and permanently archived by third parties. Deleting a
commit does not unpublish it.

**Never commit:** credentials, tokens, API keys, private keys, connection strings,
`.env` files, internal hostnames or IP ranges, employer/client identifiers, real user
data, customer payloads, or unredacted logs. Use synthetic fixtures
(`user@example.com`, RFC 5737 addresses, obviously fake IDs) everywhere — an allowlist
of "real values that are fine" is how the source project ended up sanitizing PII across
six example payloads after the fact.

**If a secret is committed:** treat it as compromised the moment it is pushed. Rotate
first, scrub second. History rewriting is cleanup, never remediation, and never a reason
to skip rotation.

**Security disclosure.** Vulnerabilities go through the private channel in `SECURITY.md`
(GitHub private vulnerability reporting), never a public issue, never a public PR
description, never a commit message that explains the exploit before the fix ships.
Agents must not open a public issue describing a suspected vulnerability — report it to
the owner and stop.

**Reputation surface.** Issues, PR descriptions, and review comments are public and
permanent. Assume anything written there is quotable. Keep them technical.

**Third-party ecosystem.** Assume the repository is cloned, vendored, and scraped for
training. Do not commit anything whose value depends on staying private.

---

## 14. Licensing and Provenance

- **The project is licensed [Apache-2.0](LICENSE)** (chosen 2026-07-25: permissive, with an
  explicit patent grant). `LICENSE` at the repository root is authoritative and every file
  in the repo must be compatible with it. Attribution and the status of the domain prompt
  and knowledge cards are recorded in `NOTICE`.
- **Provenance rule: never paste code you cannot license.** No copying from Stack
  Overflow answers, blog posts, GPL/AGPL sources (unless the project is itself
  copyleft), or another repository without checking its license and preserving required
  notices and attribution. Vendored third-party code goes in a clearly marked directory
  with its original license file intact.
- Adding, upgrading, or removing a dependency requires owner approval (§2) and a stated
  reason. Check the dependency's license for compatibility, its maintenance status, and
  its transitive footprint. A one-line utility is not worth a supply-chain edge.
- Do not add a dependency to avoid writing fifteen lines (§1.2, simplicity first).
- Record notable third-party attributions where the license requires it (`NOTICE`, or a
  section in `README.md`).

---

## 15. Contribution Surface

Required public-facing files. Each is a single source of truth for its subject; do not
duplicate their content into `docs/**`:

| File | Owns |
|---|---|
| `README.md` | What it is, why you'd use it, install, minimal example, license line |
| `LICENSE` | The grant |
| `CONTRIBUTING.md` | Setup, build/test commands, commit convention (§6), PR expectations |
| `CODE_OF_CONDUCT.md` | Behavioral standard and the reporting contact |
| `SECURITY.md` | Supported versions and the private disclosure channel (§13) |
| `CHANGELOG.md` | User-visible changes per release, newest first |

Rules:

- `CONTRIBUTING.md` restates for humans only what §§4-6 require mechanically, and points
  at the CI checks. It does not become a second rulebook — when the two disagree,
  `AGENTS.md` wins and `CONTRIBUTING.md` gets fixed.
- **Public API is a contract.** Once released, a breaking change needs a major version
  and a `CHANGELOG.md` entry. Semantic versioning. Deprecate before removing.
- `CHANGELOG.md` records what a *user* observes, not what the diff touched. Internal
  refactors that change no behavior do not get entries.
- Review external contributions as untrusted input: read the whole diff, never run a
  fork's build scripts locally to "check it", and be more suspicious of a first-time
  contributor's changes to CI config, build tooling, or dependencies than of their
  changes to source.
- Be generous in review tone and strict on the gates. The gates are impersonal; that is
  the point — they let you say no to code without saying no to a person.

**Settled:**

1. ~~License~~ — **Apache-2.0**, decided 2026-07-25 (§14).
2. ~~Issue tracker layout~~ — bug / feature templates and a config with a private security
   link are in `.github/ISSUE_TEMPLATE/`; a PR template enforces the gate checklist.

---

## 16. Benchmark Governance

Applies from the moment the first real photograph enters `evals/golden/`. Written before
that happened, deliberately: a rule against overfitting is worthless if it arrives after
the first tempting failure.

### Benchmark cases are immutable evaluation assets

Taxon cards, comparison cards, prompts, thresholds and routing rules **must not be tuned
against individual golden cases**.

> A benchmark failure may reveal a defect.
> It may not, by itself, define the fix.

The distinction is the whole point. "Case 47 says Picea and we say Pinus" is an
observation. It is not a specification, it is not a botanical source, and it is not
permission to edit `picea.yaml` until case 47 goes green. That edit costs nothing today and
destroys the benchmark's meaning permanently — after it, the suite measures how well the
cards were fitted to the suite.

### Any change motivated by a benchmark failure carries a justification

Record it in the pull request, using this block:

```yaml
change_justification:
  observed_failure:            # the specific case and what it got wrong
  generalized_failure_class:   # the class of error, stated without reference to the case
  independent_domain_source:   # a source that is NOT the benchmark: prompt section,
                               # field guide, literature, expert review
  new_non_golden_tests:        # unit/contract/public-eval tests that fail before the fix
  affected_rules:              # cards, thresholds or routing touched
  benchmark_cases_rechecked:   # cases re-run afterwards, including ones that were passing
```

A change that cannot fill `independent_domain_source` and `new_non_golden_tests` is
overfitting, whatever else it is called. Send it back.

### Separation of concerns

- **Golden material never informs card authoring.** The 25 shipped cards' *feature rules*
  derive from the domain prompt's section 14 and nothing else, which is what makes the first
  benchmark run a genuine measurement rather than a self-assessment. Provenance is recorded
  per card, per feature rule and per declared identity, because not everything on a card
  comes from that one source: family placements are standard taxonomy the prompt never
  states, and `larix` is not in the prompt at all. Each says so where it sits.
- **Public fixtures and golden cases stay apart.** `evals/fixtures/` scripts provider
  responses to exercise the machinery; `evals/golden/` measures botanical correctness.
  Fixture wording must never be copied from a golden case, and no card may contain a value
  token that exists only to satisfy one.
- **Blind by default.** The graph never receives the expected answer. Anyone adding a
  benchmark runner must keep it that way.
- **Report the denominator.** Accuracy over 60 hand-picked cases is a statement about those
  60 cases. Say so, every time.

### What the public evaluation suite is for

`evals/public/` is a **conformance and regression** suite, not an accuracy benchmark. It
proves the machinery behaves as specified on constructed situations. It is legitimate — and
expected — to add a public case for a newly-understood failure class. That is not
overfitting, because the fixtures are synthetic and the case documents a rule, not an
answer.

---

## 17. Open Decisions

**Still open, blocking the first public push:**

3. DCO sign-off, CLA, or neither. Until decided, `CONTRIBUTING.md` asserts only that a
   contributor has the right to submit under Apache-2.0.
4. Contribution policy on AI-assisted code, if any.
5. ~~Replace `OWNER` in the repository URLs~~ — **`Dendro-Inspector/Dendro-Inspector`**,
   taken from `origin` and applied 2026-07-26 to `pyproject.toml`, `CHANGELOG.md` and
   `.github/ISSUE_TEMPLATE/config.yml`. The contact links in that config assume Discussions
   and private vulnerability reporting are enabled on the repository; enable both, or drop
   the links rather than publish two dead ends. No release tags exist yet, so the changelog
   compare links resolve only once `v0.2.1` and `v0.2.2` are pushed.
6. Branch protection on `main`: require the CI checks from `.github/workflows/ci.yml`.
   Until this is switched on, §11's enforcement layer does not exist — the workflow runs
   but nothing stops a merge over a red run.
