> **Normative v1 reconciliation:** See `../docs/decisions/FINAL_V1.md` for the approved deployment, lifecycle, contract and governance rules. Historical conflicting implementation suggestions do not authorize changes.

# Database design — normative v1 requirements

## Authoritative boundary
PostgreSQL is authoritative for users, auth, projects, conversations, immutable message revisions, branch snapshots, attachments, model runs, jobs, operational diagnostics, and account-deletion control. **No application-owned canonical managed-memory schema** is permitted in v1. Mem0 may use provider-chosen persistent stores/configuration; its internals do not become app-owned tables.

## PostgreSQL baseline
Target PostgreSQL 18; prefer 18 when validated. UUIDv7 generated in application. All timestamps `timestamptz` UTC. Flyway owns schema DDL; runtime role cannot create/alter tables. Schemas: `archive`, `auth`, `runtime`; optional `analytics` views. Do not create `memory` schema for managed-memory lifecycle.

| Table | Required semantics |
|---|---|
| `archive.users` | Private owner, status; user ID stable until purge |
| `archive.projects` | Owner-scoped metadata, optional instructions, archive status |
| `archive.conversations` | Owner/project, parent branch, branch point, conversation_version, status |
| `archive.messages` | Owner, conversation, dense local ordinal, role, state, current immutable revision pointer |
| `archive.message_revisions` | Exact immutable content with ordinal/version and hash |
| `archive.conversation_branch_revisions` | Immutable ordered inherited prefix references exact existing revisions |
| `archive.attachments`, `archive.message_revision_attachments` | Owner-scoped metadata and revision attachment references; blob bytes outside DB |
| `auth.identities`, `auth.sessions` | Admin-provisioned Argon2id users and revocable hashed session tokens |
| `runtime.model_runs`, `runtime.model_run_attempts` | Owner-bound durable inference state, attempts, output checkpoints and token metadata |
| `runtime.jobs`, `runtime.memory_ingestions` | Generic durable job claims plus memory delivery status, exact source revision IDs |
| `runtime.operation_ledger` | Idempotency key + request fingerprint + response identity under user scope |
| `runtime.deletion_controls` | Fenced user purge and restoration tombstone/journal handling |
| `runtime.outbox_events` | Optional durable publication of domain events where needed; no duplicate job hop required |
| `runtime.context_assembly_records` | Selected source IDs, budget/degradation and optional prompt artifact reference |

## Integrity requirements
- Ownership relationships use `UNIQUE(user_id, entity_id)` and composite `(user_id, entity_id)` FKs. Every user-owned table is covered by FORCE RLS and authorized application filters; runtime role cannot bypass or own RLS-protected tables. Transaction-scoped `app.current_user_id` must be set only by trusted code. Security tests under actual runtime role.
- Messages and memory-external content revisions are immutable. `messages.current_revision_id` must belong to the same `(user_id,message_id)`. Deferrable cycles allowed; commit guards required so completed messages cannot have null current revision.
- Branch parent owner must match; branch point must exist in the parent's visible history at exact revision. Branch snapshot ordinals dense and equal exact inherited visible prefix of parent as of creation; flatten branch-of-branch. These are transactional-service invariants with SQL-backed tests; SQL checks alone are insufficient. Descendant dependency-aware purge.
- Conversations have `conversation_version` monotonic increment for visible-state mutation. Atomic message insert and run creation with operation idempotency; at most one nonterminal run per conversation via compatible DB constraint/index.
- A model-run terminal update and finalized assistant message must commit atomically; partial output checkpoints stored separately rather than as many immutable revisions. Background ingestion enqueue atomic with eligible message finalization.
- Job claim using `SKIP LOCKED`, short transaction, lease token; only current token may complete. Expired lease recovery, capped backoff and explicit `reconciliation_required` state for ambiguous Mem0 outcomes.
- Full user deletion fences new writes, revokes sessions, drains/cancels jobs, purges provider storage and internal data, verifies and persists tombstone until older backups cannot reintroduce it.
- Deleting conversation subtree purges exclusively associated message revisions, attachments and execution artifacts; **Mem0 memories unaffected**. Queued source-referencing ingestion jobs must be cancelled/fenced.

## Migrations and known validation gap
T03–T07 implement separate forward-only Flyway V001–V005 SQL migrations in order, under independent reviewed PRs. This design package supplies specifications, not unexecuted candidate SQL. Require actual PostgreSQL 18 migration, privilege and integrity tests; v2-reference SQL is non-normative.

## Required SQL verification
- Fresh migrate and second migrate/validate on supported PostgreSQL; inspect catalog constraints, RLS policies, role grants, indexes, triggers and migration checksum history.
- Attempts at forged user_id, cross-owner FKs, missing actor context, malformed branch snapshots, revision updates, unsafe purge and duplicate active runs must fail.
- Execute competing PostgreSQL transactions for message submission/version CAS, branch creation and job claiming; verify no duplicates or lost updates.
- Test account deletion and restore fence semantics as an integration workflow (cannot be guaranteed from DDL alone).


## Standalone runs, renewable sessions, and operation lookup (v1)
- `runtime.model_runs` is user-owned and includes a discriminator (`conversation` or `standalone`). For conversation runs, `conversation_id` and `input_message_id` are required; for standalone runs, both are null. Enforce the exclusive shape with a database CHECK; any terminal assistant-message FK is null for standalone runs. The one-active-run-per-conversation partial unique index applies only when `conversation_id IS NOT NULL`; standalone concurrency is constrained by user/provider queue limits instead.
- Persist standalone prompt only as minimally necessary for execution and recovery in a protected run input, **not** as a canonical message; ensure short bounded retention, encryption at rest, diagnostic redaction and explicit purge. `runtime.model_runs` is not an archival replacement. Define TTL and deletion worker during T21/T23 and validate in T40.
- `auth.sessions` needs configurable `expires_at` (sliding idle expiry), an immutable absolute expiry ceiling, revocation, and last-use fields. Successful authenticated activity may extend `expires_at` at a throttled cadence within ceiling; concurrently executing CLI instances must not invalidate one another. Sessions are keyed only by server-stored hash of bearer credential; no client token-management requirement.
- `runtime.operation_ledger` must permit authenticated lookup by `(user_id, operation_type, idempotency_key)` returning safe operation/run/created-resource identities; include accepted-status semantics, operation retention policy, and non-sensitive response identity fields. A stable idempotency record must be committed atomically with the accepted operation.
- Conversation-to-project reassignment is an owner-checked metadata mutation guarded by `expected_conversation_version`; a null project means unassigned. Preserve inherited branch snapshot pointers and previous ingestion provenance. Indexed metadata/list reads reflect new association.
- No executable V001–V005 files are bundled. T03–T07 own their respective migrations. Never edit an already applied Flyway migration; validate fresh and upgrade paths against PostgreSQL 18.
