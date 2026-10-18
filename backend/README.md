> **v1.2 feature-branch governance:** 41 reviewable **backend** tasks, including nine backend client-integration tasks, each with a named branch, PR, individual acceptance tests and required independent review/user merge approval. See `TASK_INDEX.md` and `AGENTS.md`.

# Mimir Server — Backend v1 authoritative design package

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
