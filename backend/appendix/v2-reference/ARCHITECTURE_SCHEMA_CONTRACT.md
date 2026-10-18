# Local LLM with Memory — Canonical PostgreSQL Data Model

**Status:** Design draft v0.3 (deliverable requirement added; migrations remain unvalidated). See `DESIGN_REFINEMENTS_V02.md` for approved integrity contracts and remaining SQL gaps; not yet validated on PostgreSQL.
**Database:** PostgreSQL 17/18; application-generated UUIDv7 identifiers.  
**Schema authority:** Flyway versioned SQL; SQLAlchemy maps schema but does not create it.  
**Layers:** Conversation archive, managed memory, working context. The latter is transient.

## Migration contents

- `V001`: users, projects, conversations, message revisions, immutable branch-prefix reference records, attachment metadata.
- `V002`: managed memory, current content revisions, independent evidence, projects, relationships, canonical event log.
- `V003`: model runs, jobs, operation ledger, outbox, retrieval events, unresolved candidates, vector projection state and attempts.
- `V004`: illustrative RLS policies and immutable-record triggers.

Migrations are SQL-first **design artifacts**, not a signed-off production deployment. PostgreSQL/Flyway binaries were not available for execution in the authoring environment. Integration tests must validate syntax and semantics on actual PostgreSQL before adoption.

## Normative cross-cutting rules

1. Each domain resource has exactly one owner. Relational associations are same-owner via composite FKs; no family sharing initially.
2. Client-supplied user IDs never determine authorization. The trusted backend sets transaction-local owner context (`SET LOCAL app.current_user_id = '<uuid>'`) based on authenticated identity. Use a non-owner, non-BYPASSRLS runtime role.
3. A project has many conversations; a conversation has at most one project. A memory may draw evidence from many conversations and have multiple project affinities.
4. A branch has a separate conversation ID and a frozen list of exact inherited message revisions; no content duplication. Editing prior messages or regenerating assistant output branches rather than rewriting completed downstream history.
5. Each committed message/memory revision is immutable. Lifecycle transitions and evidence changes create ordered audit events, without necessarily creating new content revisions.
6. Memory candidates are separate from active managed memory. Active/superseded/retracted are the only canonical lifecycle values.
7. Distinct assertions cannot be merged solely on vector similarity. Candidate operation vocabulary: CREATE, REINFORCE, REVISE, SUPERSEDE, RETRACT, NO_CHANGE, DEFER.
8. Memory aggregate version increments once per canonical operation under a row lock, with event ordinals for multiple events. Content revision numbers increment only on content changes.
9. Qdrant uses immutable revision-specific projection IDs; only PostgreSQL-current active revision IDs may be supplied to working context. Stale points are tolerated during reconciliation but never trusted.
10. No implicit expiration, message partitioning, or arbitrary memory limits initially.
11. Deletion is a privileged dependency-aware purge, not uncontrolled cascade. Descendant branches and evidence-derived memory/projection/audit payloads must be included in impact analysis.

## Required transactional services

### Create a message

Under a transaction-local authenticated owner scope: lock the owning conversation; allocate its `next_message_ordinal`; create message + initial revision and current-revision pointer; create processing outbox event if appropriate; commit. Never hold a DB transaction during inference.

### Create a branch

Lock source conversation; materialize the full visible prefix as `(source_message_id, source_revision_id, inherited_ordinal)`; create branch with parent and project; insert prefix references; commit. Enforce that the last snapshot entry equals branch point. Subsequent parent mutations must not modify the snapshot.

### Commit memory action

Acquire affected memory locks in deterministic ID order; validate expected aggregate versions and operation idempotency; apply content/lifecycle/evidence/relationship changes; increment aggregate version once per affected memory; insert ordered events and outbox work; atomically record operation ledger; commit. Any stale extraction recommendation must be reconsidered, not blindly retried.

### Vector projection reconciliation

Generate embeddings outside DB transactions; persist intended projection and audit operation; Qdrant upsert/delete asynchronously by immutable projection identity. Re-check desired state after I/O. Validate candidate user, memory status, and current revision against PostgreSQL before placing into working context.

## Items requiring a second DDL pass before approval

The SQL draft deliberately does **not** claim full enforcement of all invariants. Complete these before implementation-agent handoff:

- Add deferred constraint triggers to forbid committed memories without `current_revision_id`, and completed messages without a revision. Validate monotonically contiguous message ordinals and branch-snapshot prefix completeness.
- Enforce branch-point message's ownership **and membership of parent visible path**, including branch-of-branch snapshots. Enforce ancestor acyclicity; prohibit a branch inheriting its own revisions.
- Decide whether same-message repeated revisions are allowed in branch lineage under future edits; presently disallowed by snapshot uniqueness.
- Add database transaction routines or constraints for lifecycle transition legality, aggregate-version sequencing, supersession target validity, cycle prevention, and event-to-state consistency.
- Design privileged purging before making immutable trigger policies operational. Do not bypass triggers casually, and do not preserve purged sensitive content in payload audit/history.
- Define explicit privileged user creation/login provisioning flow; ordinary user-scoped RLS cannot create a user when no user context exists.
- Define artifact storage security, credential/session tables, complete authorization grants, and whether project-instruction history requires an independent revision table.
- Define Qdrant delete/upsert attempts across crash windows, reconciliation behavior, and read validation batching. Ensure `desired_state` moves monotonically to absent for obsolete immutable revisions.
- Include stricter `CHECK`s, domain constraints, and concurrency tests, and verify all migrations with `flyway migrate` and `flyway validate` in a disposable PostgreSQL instance.

## Acceptance tests (must be executable against real PostgreSQL)

1. Same-owner inserts succeed; cross-owner projects/messages/evidence/relationships/projections fail.
2. Nonprivileged RLS reads and writes fail closed when user scope is absent or incorrect, including after pooled connection reuse.
3. Same-entity current-revision pointers enforce membership at commit.
4. Illegal mutation/deletion of immutable revisions, events, and branch snapshots fails under normal roles.
5. An inherited branch still resolves exactly the historical revision prefix after parent revisions change; branch-of-branch history is stable.
6. Concurrent message appends produce distinct, ordered ordinals.
7. Concurrent memory changes cannot both commit against the same stale aggregate version.
8. Reinforcement increases aggregate version and adds evidence/events without adding a content revision.
9. Repeated operation IDs are idempotent; distinct operations with similar text are not blindly collapsed.
10. Contradiction evidence does not automatically overwrite or retract canonical memory.
11. Superseded/retracted and outdated vector revisions never enter working context, including during Qdrant outage and out-of-order jobs.
12. Purging a parent and dependent branches leaves no dangling references or residual sensitive vector/audit artifacts.
13. Clean Flyway migration and repeat `validate` pass; applied versioned scripts remain unchanged.

## Required final database-design deliverable — consolidated Flyway baseline

**Mandatory before database design sign-off and before implementation-agent handoff.**
Produce a complete, consolidated, executable Flyway SQL migration baseline implementing the approved v0.2 integrity contracts. The existing V001–V005 design drafts are references, not a validated release baseline.

The final deliverable must include:

1. **Versioned Flyway SQL:** complete tables, columns, types, primary/foreign keys, indexes, deferrable constraints, triggers, state-transition guards, transaction-safe routines, row-level security policies, grants, and required authentication/deletion support.
2. **Explicit integrity enforcement:** branch-prefix correctness; same-entity revision pointers; immutable committed revisions; per-memory event ordering; legal lifecycle changes; supersession acyclicity; tenant-consistent relationships; idempotency and concurrency invariants.
3. **Privileged deletion and projection consistency:** resumable purge with descendant/evidence impact handling, deletion fences, sensitive audit-payload cleanup, and revision-specific Qdrant projection contracts.
4. **Executable integration tests:** tests against real PostgreSQL using ordinary restricted runtime roles, covering every release-blocking scenario in `tests/VALIDATION_GATE.md` and `DESIGN_REFINEMENTS_V02.md`.
5. **Validation evidence:** clean `flyway migrate` and `flyway validate` executions, repeat migration idempotence, schema/constraint inspection, machine-readable test results, defects report, and PostgreSQL-version compatibility results.

**Sign-off rule:** No implementation-ready status until all release-blocking checks pass. When a requirement cannot be enforced purely by DDL, document its transactional service contract and demonstrate enforcement with executable integration tests. Do not represent SQL drafts as tested migrations.

**Delivery format:** A versioned design package with migrations, tests, a reproducible execution guide, schema contract, and validation results. Coding agents must not be expected to invent missing invariants.

## Review gates

**Gate A — relational model:** approve tables, fields, keys, lifecycle semantics, and the outstanding design items above.  
**Gate B — consolidated Flyway baseline:** complete executable SQL plus integration tests implementing the v0.2 contracts, with recorded PostgreSQL/Flyway validation evidence and zero release-blocking failures.  
**Gate C — vector projection:** Qdrant collection, payload index, embedding configuration identity, and reconciliation contract finalized.
