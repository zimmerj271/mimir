# T05 — Conversation and project schema

**Milestone:** M1  
**Branch:** `feature/t05-archive-core-schema` (from updated `main`)  
**Dependencies:** T04  
**Status:** V003 implementation candidate and executable PostgreSQL tests supplied
on the T05 branch; independent review and human merge approval pending. Exact
results and the submitted commit are recorded in the PR.

## Objective / authorized scope
Owner-scoped users/projects/conversations/messages revisions base relational schema.

Reuse T04 users. Add V003 projects, conversations, messages and message revisions;
preserve applied V001/V002. Initial base was merged T04 (PR #5),
`105d4747299ab317c0d5dda5469d892bcdbc66e2`. Submission base `main` is
`e9a122836f09ff9256aa20d346c2b422f9982363`, including the independently reviewed
and owner-merged deployment correction (PR #6). The owner explicitly authorized
resuming T05 after that merge; retain the Compose-only deployment decision.

## Explicit owner-approved lifecycle clarification

The owner approved these rules during the T05 assignment and authorized their
recording in the decision ledger and relevant normative design documents:

- Archive owns authoritative content; runtime exclusively owns execution state.
  Canonical messages persist only as `completed`, with their exact immutable
  selected revision. A missing revision is permitted only before transaction commit.
- User message/initial revision commit atomically with pending run (T12).
  Assistant message is created only on success, atomically with revision, terminal
  run and eligible ingestion job (T16). Failed/cancelled/interrupted runs create no
  canonical assistant message. Partial output and failure state remain runtime
  checkpoints/streaming concerns. Only successfully committed user/assistant
  revision pairs are ingestion-eligible. Consecutive user messages are valid.
- Revision rows and committed message identity/owner/conversation/role/ordinal
  are immutable. Selected revision must match message and owner and cannot be
  replaced or cleared. No application/internal operational exceptions. Authorized
  archive deletion/account purge remain governed by their approved lifecycles.
- Keep separate messages/revisions. Edits/regeneration create new conversations;
  inherited history references exact revisions without copying text. T06 retains
  branch snapshots; T07 runtime schema/checkpoints; T12 submission/run atomicity;
  T16 inference/finalization. T05 must not implement these successors.
- Unsuccessful checkpoint retention and explicit retry behavior remain unresolved.
- `contracts/openapi.yaml` still lists pending/failed/interrupted canonical message
  states. Desired semantics are `completed` only. Record the discrepancy and obtain
  separate contract-governance authorization before editing protected contract
  artifacts or their manifest. T05 adds no public HTTP/event implementation.

## Out of scope
Do not implement successor tasks, change shared contracts or introduce major dependencies without approval. No public per-memory CRUD, application-owned canonical memory tables, hidden direct client database/provider access, or future-web implementation.

## Required test scenarios and acceptance
Composite owner FKs, immutable revisions, active version constraints exercised on PostgreSQL. Add pertinent failure, auth, boundary and concurrency tests. Prefer deterministic unit tests where possible and real-service integration tests when persistence/provider guarantees are involved.

Cover atomic message/revision creation, commit-time rejection of missing selected
revision, unsupported states, wrong-message/wrong-owner pointers, revision updates,
pointer replacement/removal and immutable message identity/order changes. Verify
consecutive user messages, dense local ordinals/revision numbers, content byte
boundaries, owner FKs, actual runtime RLS/grants, fresh/upgrade Flyway validation,
transaction rollback/recovery, drift and competing transactions on PostgreSQL 18.
Acceptance coverage: archive-local DB-01/DB-02/DB-03 and DB-05 storage support;
supporting DB-ROLE-01/DB-MIG-01 evidence only. Full submission, branch, runtime,
deletion and V001–V005 acceptance remain successor work.

## Required engineering review
- Read `AGENTS.md`, `CODING_STANDARDS.md`, `ARCHITECTURE.md` and relevant domain documents before coding.
- Favor cohesive OOP services/models/adapters, composition and dependency injection. Do not introduce speculative frameworks or standalone business-logic functions.
- Formatting, linting, type checks, targeted tests and relevant integration checks must have **actual logged results**.
- Reviewer independently checks changed files, contracts, security, quality, tests and scope.
- No direct push to `main`; submit one PR from `feature/t05-archive-core-schema`, obtain user approval, then merge.
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
