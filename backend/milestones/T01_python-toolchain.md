# T01 — Python project and quality toolchain

**Milestone:** M1  
**Branch:** `feature/t01-python-toolchain` (from updated `main`)  
**Dependencies:** None  
**Status:** Design brief only; not implemented or tested.

Acceptance IDs: GOV-01 (supporting Python quality/CI and mechanical evidence coverage;
normative ownership remains S00/T41). See the T01 mapping in the root acceptance registry.

## Objective / authorized scope
Establish backend package, uv lock, Ruff, typing and pytest; no domain behavior.

## Out of scope
Do not implement successor tasks, change shared contracts or introduce major dependencies without approval. No public per-memory CRUD, application-owned canonical memory tables, hidden direct client database/provider access, or future-web implementation.

## Required test scenarios and acceptance
Fresh environment setup is reproducible; lint/type/empty smoke tests run; pinned dependencies. Add pertinent failure, auth, boundary and concurrency tests. Prefer deterministic unit tests where possible and real-service integration tests when persistence/provider guarantees are involved.

## Required engineering review
- Read `AGENTS.md`, `CODING_STANDARDS.md`, `ARCHITECTURE.md` and relevant domain documents before coding.
- Favor cohesive OOP services/models/adapters, composition and dependency injection. Do not introduce speculative frameworks or standalone business-logic functions.
- Formatting, linting, type checks, targeted tests and relevant integration checks must have **actual logged results**.
- Reviewer independently checks changed files, contracts, security, quality, tests and scope.
- No direct push to `main`; submit one PR from `feature/t01-python-toolchain`, obtain user approval, then merge.
- Stop at this brief's boundary; do not start a dependent task automatically.

## PR evidence / completion template
- Branch and base commit; changed paths; concise design rationale.
- Acceptance cases → test files → exact commands → results or blockers.
- New or modified public interfaces, migrations, dependencies.
- Unresolved risks and architectural deviations (explicitly none if absent).
- Reviewer outcome and user approval (pending until granted).

## Mandatory bootstrap additions
Adopt `backend/AGENTS.md` toolchain policy: create `pyproject.toml` with src layout, uv-managed Python and dev/test groups, committed `uv.lock`, configured Ruff, strict mypy, pytest/pytest-asyncio and coverage. Pin and document exact compatible Python/uv tool versions after checking availability. Provide `uv sync --locked --all-groups` and `uv run --locked` test/lint/type commands in README and CI. Establish first iteration of mechanical docs/acceptance checks per `../../QUALITY_GATES.md`. No application code or migration execution in this task.

## Agent hardening gate
Apply repository `AGENTS.md` and language `CODING_STANDARDS.md`. Before writing observable behavior, check `SPECIFICATION_GAPS.md` and the frozen `contracts/` artifacts. Stop for missing, contradictory, or unapproved interface/state semantics; do not infer them. Map relevant acceptance IDs and tests in `TRACEABILITY.md`, execute applicable `QUALITY_GATES.md` commands, and report literal results. This gate does not expand the authorized scope of this task.

### Blocking branch acceptance checkpoint
At completion, **STOP**. Open one task-scoped PR using the root `PR_REVIEW_TEMPLATE.md`. Require successful documented checks, independent coding-agent review, explicit human review and affirmative merge approval. Do not merge autonomously or begin the next task before merge confirmation and a fresh human assignment. See `AGENTS.md` and `QUALITY_GATES.md`.

**V1 normative references:** `CONTRACT_DECISIONS_V1.md`, `STATE_MACHINES.md`, `PROMPT_AND_MEMORY_CONTRACT.md`, `contracts/openapi.yaml`, `contracts/sse.schema.json`, and `contracts/errors.json` at unified-package root. These are consumed from the one shared `/contracts` directory in the monorepo; without separate copies.
