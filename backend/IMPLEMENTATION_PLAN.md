> **Normative v1 reconciliation:** See `../docs/decisions/FINAL_V1.md` for the approved deployment, lifecycle, contract and governance rules. Historical conflicting implementation suggestions do not authorize changes.

> **Agent assignment override (v1.2):** The seven milestones below are roadmap groupings only. The actual implementation plan is the **41 **backend** feature-branch tasks** in `TASK_INDEX.md`; one PR per brief with mandatory review and approval.

# Coding-agent implementation plan

## Rules for every agent
- Read `ARCHITECTURE.md` and the relevant domain spec before coding. Architectural decisions are approved; deviations require explicit review.
- Deliver narrowly scoped changes, Flyway SQL if needed, automated tests, executable commands, logs/results, and a factual completion report. Do not claim unrun tests passed.
- No app-owned `memory.*` schema or per-memory public APIs; no hidden Mem0/Ollama coupling; no Python-to-CLI direct data interface; no unauthorized hosted fallback.
- Use pinned dependencies, `uv.lock`, real PostgreSQL/Flyway integration tests, deterministic fake providers for most inference tests.

## Early feasibility spike (parallel to M1)
Pin Mem0 OSS, Qdrant and Ollama releases; test in-process adapter or justify supported REST boundary; target Qwen3-Embedding-4B 2560-dim embeddings; user isolation on search and full-user purge; metadata behavior, retry ambiguities and full enumeration/export. Document supported operations and failure modes. If release-blocking guarantees fail, STOP and bring the decision back for architectural review rather than inventing custom memory framework.

## Milestones
| Phase | Outcome | Acceptance gate |
|---|---|---|
| M1: Foundation/schema | Backend package/uv, Docker Compose, local auth bootstrap, Flyway **complete v1 baseline**, database roles/RLS | Fresh migrate + validate twice; owner/branch/integrity tests on real PostgreSQL; no memory-engine tables |
| M2: Conversation domain/API | Projects, archive, immutable revisions, snapshot branches, REST/idempotency/CAS | Owner isolation, version conflict, branch nested snapshots, purge subtree tests |
| M3: Inference/context/SSE | Model registry, fake & Ollama provider, pure context assembler, durable runs, snapshot/live SSE, cancellation | Exact-once durable finalization, budget tests, stream snapshot race tests, interruption/cancel/retry tests |
| M4: Backend API integration | Standalone runs, operation lookup, session continuity, project reassignment, contract tests | REST/SSE compatibility and security tests pass without building Go CLI |
| M5: Mem0 integration | MemoryService ingest/search, async committed-turn ingestion, Qdrant, local embeddings, default memory retrieval | Isolation, persistence, metadata, ambiguous retries, no duplicate inherited ingestion, tests with real OSS Mem0 |
| M6: Hardening | Job lease recovery, account purge/deletion fence, coordinated backups, encrypted storage, telemetry and health | Crash/restore/security/outage tests, operator runbooks |
| M7: Release | Complete compatibility, performance measurements, deployment reproducibility, docs | Every release-blocking test in `ACCEPTANCE_TESTS.md` passed, deviations closed |

## Dependencies and scope discipline
- M1 is foundational; M2 precedes production conversation/runs. M3 can use stub memory; M4 validates backend client contracts before M5; the independent Go CLI can proceed against contract mocks and does not block backend M5. Minimal inference scheduling in M3; durable general jobs in M5/M6. M5 spike should be **early** despite full integration coming later.
- Web UI belongs to a later separately scoped client milestone after shared backend/CLI contract is proven. Agent must not implement web frontend as prerequisite.
- Each milestone package contains objective, in-scope files, excluded changes, versioned contract, test matrix, commands and completion evidence.
- Freeze the authoritative design at the milestone boundary; future ideas appended to the ARCHITECTURE appendix, not silently included in v1.

## Definition of done (every milestone)
1. Required functionality integrated into established service boundary.
2. Unit and relevant real-service integration tests added and executed, with results recorded.
3. Formatting/type/lint checks pass; schema/OpenAPI artifacts regenerated where applicable.
4. Failure, cancellation, authorization, and data isolation considered explicitly.
5. User-facing commands and relevant operations notes documented.
6. No scope or architectural deviation without approved change record.


## Normative v1.2 task/branch mapping

`TASK_INDEX.md` and the 41 `milestones/Txx_*.md` briefs are the authoritative implementation breakdown. Branch naming: `feature/tXX-short-slug` (lowercase `t`, two-digit sequence). Every task has one named branch, one PR, acceptance cases, and a stop/merge gate.

Backend T20–T28 are **nine server-owned integration tasks**: schema contract, standalone domain and HTTP, accepted-operation lookup, silent session renewal, project reassignment, idempotent conversation creation, unified run recovery, and backend contract acceptance. All Go CLI/TUI development lives in the independent `../cli/IMPLEMENTATION_PLAN.md` and CLI-01–CLI-16 task briefs. No backend task requires a Go TUI build.

Dependencies are listed per task. Never treat the seven milestone rows above as assignments; the numbered task briefs supersede previous v1.1 T01–T26 references. Independent features may be built in parallel only on non-overlapping branches; merge order follows prerequisites and every PR reruns tests against current `main`.

## Two independent development flows
- **Mimir Server (this directory):** T01–T41 build the Python backend, persistence, execution, memory and client-facing contracts. Backend tests do not require the Go app. T40 reviews server workflows; T41 records server release evidence.
- **Mimir CLI/TUI (`../cli/`):** CLI-01–CLI-16 build Go CLI, state, terminal UI, theme and client-side recovery. CLI tests can use OpenAPI/SSE contract mocks; production integration runs against Mimir Server.
- **Shared gate:** contract compatibility tests prove matching payloads/statuses. Neither plan can quietly modify the other system’s behavior.

## Specification-closure prerequisite
Apply top-level `AGENTS.md`, `SPECIFICATION_GAPS.md`, `QUALITY_GATES.md`, `TRACEABILITY.md`, `THREAT_MODEL.md`, and `EVALS.md` before commencing any externally observable API, state-machine or persistence behavior. Bootstrap tasks may establish tools and harnesses; do not treat unapproved product defaults or missing contract fixture files as complete. Explicitly freeze shared monorepo schemas and reconcile task ownership before affected work begins.

## Mandatory feature-branch STOP / human-and-agent PR review checkpoint

1. **Start** only the assigned task from the latest merged `main` with its declared predecessors merged. Create one `feature/<task-id>-<slug>` branch; record base SHA, task ID and approved contract revision. Do not stack dependent work on an unmerged branch.
2. **Implement/test** only the assigned task; include relevant unit, integration, contract, negative/security and race/restart cases. Capture actual commands, versions, results and remaining risks; a failed/unrun gate must be disclosed, never represented as passed.
3. **STOP implementation** at the feature boundary. Open a PR with `PR_REVIEW_TEMPLATE.md` completed, scope/diff summary, contract/schema/dependency changes, test evidence, migration reversal/recovery guidance and review checklist. The implementation agent must not approve its own PR.
4. **Independent agent reviewer** reviews the full diff against the task, invariants, repository guardrails and contract; records findings and severity and verifies checks. The author addresses findings and obtains a new review on material changes.
5. **Human reviewer/owner** inspects the PR and evidence; explicit approval to merge is required **after** independent review. Silence, green CI, or agent approval alone is never consent. No bot auto-merge, direct main push, or self-merging on behalf of an unavailable human.
6. **Merge checkpoint** only an authorized human (or specifically delegated operator after affirmative human approval) merges. Record merge SHA and update task state/traceability. **Halt:** do not start or branch for the next task until merge is confirmed and the human assigns/authorizes subsequent work. If rejected, revise within the same branch and repeat review.
7. Shared contract changes require an explicitly authorized PR against canonical /contracts, independent agent review, and human approval before any dependent backend or CLI work. Monorepo tasks remain bounded to a single assigned feature branch; never presume atomic multi-task delivery.
