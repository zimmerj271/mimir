> **v1.2 feature-branch governance:** 41 reviewable **backend** tasks, including nine backend client-integration tasks, each with a named branch, PR, individual acceptance tests and required independent review/user merge approval. See `TASK_INDEX.md` and `AGENTS.md`.

# Mimir Server — Backend v1 authoritative design package

## Python package and quality toolchain (T01)

T01 supplies an independently installable `src/mimir_backend` package and quality
tools. It supplies no HTTP app, database connectivity, inference, memory provider,
migrations or runtime commands. The empty typed package is intentional.

Prerequisite S00 merged as `b5824cb12cdf70443634c0df7a183951f933cf03`; T01 branches
from that SHA. The canonical task graph governs prerequisites where older task
brief prose says "None". The approved design revision is FINAL_V1.md at that base.

Use **uv 0.12.10** and **CPython 3.14.7**, pinned by `tool.uv.required-version`,
`.python-version` and CI. `requires-python` restricts this initial toolchain to
Python 3.14. Interpreter compatibility with future provider dependencies remains
the responsibility of their assigned tasks, not a T01 support claim.

From `backend/`, run:

```sh
uv sync --locked --all-groups
uv lock --check
uv run --locked ruff format --check .
uv run --locked ruff check .
uv run --locked mypy src
uv run --locked pytest -q
uv build --offline
```

From the repository root, `make backend-check` runs locked sync, lock validation,
formatting, lint, strict typing and tests; `make check` retains root governance
validation. Both run on Linux/macOS in the existing required foundation CI jobs.
No backend path filter skips these checks. CI setup actions and tool releases are
pinned; neither cached environments nor a stale lock can substitute for sync.

Initial bootstrap explicitly contacts PyPI/files.pythonhosted.org for locked
development/test tools and the pinned uv build backend. uv may obtain the pinned
interpreter if absent; CI obtains it using setup-python. There are no runtime
dependencies. After bootstrap, reproduction, lock-rejection and wheel/sdist
tests use `--offline` and disable interpreter downloads. A truly empty cache
needs the initial connected sync; offline tests do not claim otherwise.

Ruff 0.17.1, mypy 2.4.0 and uv-build 0.12.10 are in the `dev` group; pytest 9.1.1,
pytest-asyncio 1.4.0 and pytest-cov 7.1.0 are in `test`. All direct tool versions
are exact pins, transitives have hashes in `uv.lock`, and the PEP 517 backend is
`uv_build==0.12.10`. Use `uv add`/`uv remove` for reviewed dependency updates;
never edit the lock by hand or introduce a second dependency authority.

Pytest uses strict config/markers, isolated import mode, warnings as errors and
function-scoped async loops. Coverage is configured and emits `coverage.xml`;
the package currently has **zero executable statements**, so its reported 100%
is an empty-package measurement, not application coverage. No arbitrary coverage
threshold is invented by this foundation task. Strict mypy has no exceptions.

Acceptance coverage: GOV-01 supporting quality/evidence controls, recorded in
[TRACEABILITY.md](../TRACEABILITY.md) and the
[acceptance registry](../docs/acceptance/registry.json). Tests verify installation
outside the checkout, async cancellation/cleanup, fresh/repeated locked sync,
missing-lock and wrong-uv rejection on the full backend project, stale-lock
rejection on a dependency-free fixture with a successful unlocked control,
wheel/sdist contents, rebuilding from
the sdist through PEP 517 with the locked dev build backend (no build isolation
or network), and importing the resulting wheel without the source checkout. They
test tooling guarantees; they establish no application auth/concurrency behavior.

Contract semantics and producer/consumer validation remain assigned to S01 and
later owners. PostgreSQL/Flyway, RLS, provider, auth, E2E and native CLI tests are
not implemented by T01. No shared contract or approved architecture is changed.

Independent agent review and explicit human approval/merge authorization remain
pending until recorded on the PR. To roll back T01, use a reviewed revert PR;
there is no runtime data or migration rollback. No successor task is started.

**Design baseline:** 2026-10-16 · **Status:** architecture approved, implementation and integration validation pending.

This package supersedes `local-llm-design-v0.4` and the database design drafts v0.1–v0.3 for **v1 implementation**. `ARCHITECTURE.md` is normative for approved architecture; `DATABASE_DESIGN.md`, `API_CONTRACT.md`, `IMPLEMENTATION_PLAN.md`, `ACCEPTANCE_TESTS.md`, and `OPERATIONS.md` refine it. Where a proposed implementation detail is not yet validated, it is identified explicitly.

## Reading order
1. `ARCHITECTURE.md` — approved decisions and scope, with future-work appendix at the end
2. `DATABASE_DESIGN.md` — v1 relational domain and Flyway implementation requirements
3. `API_CONTRACT.md` — REST/SSE, concurrency, authentication and failure semantics
4. `IMPLEMENTATION_PLAN.md` — milestones and agent assignment boundaries
5. `ACCEPTANCE_TESTS.md` — executable test targets and release gates
6. `OPERATIONS.md` — deployment, runtime, backup, recovery and observability
7. `migrations/README.md` — candidate SQL baseline status
8. `appendix/v2-reference/` — archived obsolete advanced memory-engine designs, **non-normative**

## Artifact/validation honesty
A v1 candidate Flyway SQL baseline is included for agent review and execution. **It has not been run against PostgreSQL/Flyway in this creation step**; do not treat its constraints as verified. Full executable integration tests are specified but **not yet implemented or passed**. M1 is responsible for finishing migration constraints, implementing tests, validating real PostgreSQL and providing a report. Do not deploy the SQL based solely on this package.

## Shared monorepo contract
The canonical Python backend API serves both a Go/Bubble Tea CLI (first) and TypeScript/React assistant-ui web frontend (later). This package specifies backend behavior; the independent implementation-ready Mimir CLI/TUI package is in `../cli/`.

## Independent flow
This folder contains the **backend build only**, with its original v1.2 architecture, documents, 41 tasks, and candidate SQL updated for newly approved CLI-facing backend requirements. The Go CLI/TUI is designed and planned separately in `../cli/`. Do not treat T20–T28 as Go tasks.
