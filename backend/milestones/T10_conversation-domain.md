# T10 — Conversation/project services and repositories

**Milestone:** M2  
**Branch:** `feature/t10-conversation-domain` (from updated `main`)  
**Dependencies:** T05, T06  
**Status:** Candidate implementation; independent review and human merge approval pending.

## Objective / authorized scope
Owner-scoped repository and domain services; canonical visible history and snapshot branches.

## Out of scope
Do not implement successor tasks, change shared contracts or introduce major dependencies without approval. No public per-memory CRUD, application-owned canonical memory tables, hidden direct client database/provider access, or future-web implementation.

## Additional normative Mimir integration requirement
Implement owner-scoped conversation project reassignment/unassignment with optimistic version increment and unchanged immutable messages/branches. Maintain deterministic recent-active conversation ordering.

## Owner-authorized scope expansion: established conversation fences

Implement the persistent owner/root conversation fence and reusable visibility
boundary described in the [pre-implementation physical proposal](../../docs/decisions/T10_CONVERSATION_VISIBILITY.md).
Use V008 because T09 already owns V007; preserve V001–V007 unchanged. Include root
and descendant fences, existing account lifecycle/established controls, forced RLS,
read-only runtime grants, fail-closed dependency errors and one authoritative
snapshot for eligibility/metadata/run discovery. Requested account deletion alone
is not a fence. Real PostgreSQL tests use privileged fixtures to establish fences.

T10 provides typed keyset repository boundaries; T11 owns opaque public cursors,
HTTP resources and idempotent create/branch acceptance using the shared transaction.
T35 owns account orchestration/revocation. T36 owns production conversation fence
establishment, deletion acceptance, writer coordination, run/ingestion coordination
and cleanup. T37 owns journal/restore replay. T10 delivers no production deletion
endpoint/writer, mutation grants, cleanup or restoration guarantee.

## Required test scenarios and acceptance

Cross-user denials, nested inherited revision history, optimistic version conflicts. Add pertinent failure, auth, boundary and concurrency tests. Prefer deterministic unit tests where possible and real-service integration tests when persistence/provider guarantees are involved.

## Required engineering review
- Read `AGENTS.md`, `CODING_STANDARDS.md`, `ARCHITECTURE.md` and relevant domain documents before coding.
- Favor cohesive OOP services/models/adapters, composition and dependency injection. Do not introduce speculative frameworks or standalone business-logic functions.
- Formatting, linting, type checks, targeted tests and relevant integration checks must have **actual logged results**.
- Reviewer independently checks changed files, contracts, security, quality, tests and scope.
- No direct push to `main`; submit one PR from `feature/t10-conversation-domain`, obtain user approval, then merge.
- Stop at this brief's boundary; do not start a dependent task automatically.

## PR evidence / completion template
- Branch and base commit; changed paths; concise design rationale.
- Acceptance cases → test files → exact commands → results or blockers.
- New or modified public interfaces, migrations, dependencies.
- Unresolved risks and architectural deviations (explicitly none if absent).
- Reviewer outcome and user approval (pending until granted).

## Agent hardening gate
Apply repository `AGENTS.md` and language `CODING_STANDARDS.md`. Before writing observable behavior, check `SPECIFICATION_GAPS.md` and the frozen `contracts/` artifacts. Stop for missing, contradictory, or unapproved interface/state semantics; do not infer them. Map relevant acceptance IDs and tests in `TRACEABILITY.md`, execute applicable `QUALITY_GATES.md` commands, and report literal results. This gate does not expand the authorized scope of this task.

### Blocking branch acceptance checkpoint
At completion, **STOP**. Open one task-scoped PR using the root `PR_REVIEW_TEMPLATE.md`. Require successful documented checks, independent coding-agent review, explicit human review and affirmative merge approval. Do not merge autonomously or begin the next task before merge confirmation and a fresh human assignment. See `AGENTS.md` and `QUALITY_GATES.md`.

**V1 normative references:** `CONTRACT_DECISIONS_V1.md`, `STATE_MACHINES.md`, `PROMPT_AND_MEMORY_CONTRACT.md`, `contracts/openapi.yaml`, `contracts/sse.schema.json`, and `contracts/errors.json` at unified-package root. These are consumed from the one shared `/contracts` directory in the monorepo; without separate copies.

## Owner-approved S01-DISCOVERY obligation

Own the owner-scoped conversation domain/repository read-snapshot boundary and derived active-run lookup using existing V005 runtime.model_runs and its active-conversation partial unique index. Preserve RLS/visibility/deletion fences; metadata and active_run_id must share one authoritative snapshot. Pending/running conversational runs qualify even during requested cancellation; standalone/terminal/foreign runs do not. No persisted active_run_id, discovery-specific migration or canonical version/timestamp update; the separately authorized fence foundation uses V008. V005 prerequisite T07 is already merged in the S01 baseline; T11 integrates the projection into HTTP.

See the single canonical [S01-DISCOVERY decision](../../contracts/HTTP_SEMANTICS.md). This adds downstream acceptance obligations only; no runtime implementation or test success is claimed by S01.
