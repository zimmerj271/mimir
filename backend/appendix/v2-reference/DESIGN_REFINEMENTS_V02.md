# Canonical database integrity addendum — v0.2

**Status:** Approved architectural choices translated to a *design-level SQL delta*. This draft has **not** passed a PostgreSQL/Flyway run and must not be treated as release-ready migrations. `V001`–`V004` are retained unchanged for traceability; `V005` illustrates additive integrity measures and still requires verification. Explicitly identified enforcement gaps are part of the acceptance gate.

## A. Branch integrity and purge

### Branch snapshot contract
1. Root conversations have no inherited rows or branch-point identifiers. A branch has a parent owned by the same user and a frozen inherited ordered prefix; `branch_point_message_id`/`branch_point_revision_id` equal the final inherited row when prefix is nonempty. An empty-prefix branch has both null.
2. `inherited_ordinal` is dense from 1. No repeated source message; all source revisions are of the same user. The snapshot must be the *exact* visible prefix of its parent as of branch creation, including inherited rows. Simple FK/UNIQUE constraints cannot prove prefix equality.
3. Creation uses a short `SERIALIZABLE` transaction (retry on serialization failure) or an explicitly documented locking protocol, acquiring source-conversation lock and serializing any operations that alter its visible path. Materialize the prefix and create branch + snapshot atomically. Branch-of-branch must work. Client may not insert snapshot rows directly.
4. A parent edit cannot modify existing inherited revisions. Parent relationship is lineage; snapshot is the child's historical content source.
5. Database guards may enforce the snapshot is complete at commit with a DEFERRABLE constraint trigger; the exact-prefix check is implemented by a database function (`archive.create_branch`) or a trusted service with database enforcement preventing direct writes by untrusted roles. Restrict INSERT/UPDATE/DELETE on branch snapshot to the narrow service/definer path. **Do not imply a row-level CHECK can validate the parent prefix.**

### Purge contract
1. Ordinary archive hides content; it does not delete it. An explicit permanent purge is a separate privileged operation with preflight impact manifest and confirmation.
2. For a parent-conversation purge, include its entire transitive descendant tree, source messages/revisions and attachments; identify every memory/evidence/candidate/model artifact/index projection/outbox event containing or derived from deleted content. Evidence-only withdrawal is insufficient when the memory text itself discloses deleted content: redact/revise, retract, or delete derived memory as appropriate.
3. Record **non-sensitive purge metadata only** (operation ID, owner, timestamp, aggregate counts, state), never deleted prompt/payload text. Scrub redundant payloads in jobs, retrieval logs, projection event audits, backups according to a documented retention/cryptographic-erasure policy. Do not promise literal immediate erasure from immutable backups; define recovery-window disclosure and reapply purge tombstones during restore.
4. Purge must protect against post-purge reintroduction by delayed workers. Quiesce affected jobs, set deletion tombstone/fence, remove references and content, issue vector deletes, reconcile until confirmed, then finalize. Non-atomic PostgreSQL/Qdrant deletion demands a resumable state machine, not one DB transaction across both stores.
5. No blanket `ON DELETE CASCADE`. Privileged purge may bypass immutability triggers only in a narrowly controlled function/role, with a durable non-content audit event and no ordinary runtime privilege to toggle triggers.

## B. Revision and lifecycle integrity

- Current pointers use `(user_id, owner_id, revision_id)` DEFERRABLE FKs already in `V001` and `V002`; require current revision on committed records via deferred trigger. `messages.status='pending'` may temporarily allow null until first revision is committed; committed memory never null.
- Revise by inserting a new immutable row with the next positive revision_number while locking its parent; change current pointer within one transaction. Reject any mutation of committed revision content by runtime role.
- Define state transition table: creation -> active; active -> active/retracted/superseded; superseded/retracted are terminal in initial version. `superseded` requires replacement edge(s); directed replacement graph acyclic and same-owner; validate with recursion while locking affected memory rows in deterministic order. Ordinary FK/CHECK alone cannot enforce this.
- Immutability triggers also protect evidence-event history / relationship history, while administrative purge has controlled exception.

## C. Event sequencing

- **Single** `memory.memory_events` stream for content, evidence, lifecycle, project affinity and relationship changes. Retire the previously discussed separate `memory_evidence_events` table (it does not appear in the v0.1 migrations).
- Each canonical operation increments `memories.aggregate_version` **once per affected memory**, under row lock. Events use `(memory_id, aggregate_version, event_ordinal)` unique; ordinal starts at 1 and is dense within that operation. Historical replay folds event payloads through the selected aggregate_version.
- A cross-memory consolidation has one operation_id, distinct aggregate-version increments per affected memory. Domain writes, event writes, idempotency ledger, and outbox are one PostgreSQL transaction. No inference within transaction. Re-run with same operation ID returns committed result; re-run against stale expected version fails for reevaluation.
- Ensure a deferred consistency checker verifies at least one event for each version increment and that event versions do not exceed memory version. Application-written changes without matching event/outbox must be rejected by privileges or transactional stored routines; checking gaps solely with `UNIQUE` is inadequate.

## D. Vector consistency

- Each indexed immutable revision has a revision-specific `point_id` (or deterministic UUID derived from revision and embedding config). Prior revisions can coexist briefly. Desired present/absent state is canonical in PostgreSQL; each upsert/delete has an immutable audit attempt with payload/version/hash.
- No stale worker can overwrite *another revision's* point; stale points might reappear after delete. A reconciliation loop removes them, and **every retrieval validates user_id, active lifecycle and exact current revision ID in PostgreSQL** before working-context assembly.
- `desired_state='absent'` is terminal for a retired revision/configuration. Retry/reconciliation handles out-of-order calls and maintains a deletion fence for permanently purged records.
- Migrating embedding model uses another versioned collection/config; no vectors from different models in one incompatible collection.

## E. Authentication/authorization

- `archive.users` is an ownership domain table, **not** the credential store. Define `auth.identities` (provider, provider_subject, user_id, unique provider+subject) and `auth.sessions` (user_id, hashed opaque refresh token ID, expiry, revocation, timestamps), or explicitly choose an external identity provider before implementation. The identity mapping must be server-verified.
- Runtime DB role cannot own tables or bypass RLS. Backend sets owner context via transaction-local session setting; no untrusted SQL or user-controlled `SET`. Credentials/sign-in run through a separate limited auth path; ordinary user RLS prevents bootstrap querying other identities. Pool reuse test must prove reset after every transaction.
- Worker jobs carry verified owner identity and run tenant-scoped. Administrative purge uses separately authorized privileged role/functions with auditable access.
- Treat vector tenant filters as required search safeguards, **not** an authentication boundary; Qdrant is not directly accessible by end clients.

## F. Validation gate (release-blocking)

1. Spin up clean PostgreSQL 18 and 17 test instances with Flyway; `migrate`, `validate`, then migrate a second time (no pending changes). Assert correct schema and history checksum; roles separate, no ORM DDL.
2. SQL structural checks: all tenant FKs, same-entity pointers, DEFERRABLE properties, RLS FORCE, restricted role privileges, expected indexes and trigger registrations.
3. Branch tests: root/child/grandchild; branch at empty prefix, first/middle/final positions; exact and dense snapshot; concurrent branch/append/edit; parent edits stable; deliberate forged prefix rejected; branch tree purge leaves no dangling references.
4. Revision tests: initial and subsequent revisions; null current pointer rejected at commit; current pointer cannot reference other message/memory/user; direct UPDATE/DELETE rejected; simultaneous revisions allocate unique sequential numbers.
5. Memory tests: CREATE/REINFORCE/REVISE/SUPERSEDE/RETRACT; wrong transitions rejected; replacement missing and cycle rejected; evidence-only mutation increments aggregate version but not revision number; complete ordered event replay; transaction rollback leaves no partial state; duplicate operation ID exactly once.
6. Security tests: at least two users with same-looking content; cross-user FKs reject, REST and worker queries never leak; RLS unscoped denies; connection-pool reuse; auth bootstrap and disabled-account rules; direct Qdrant client access denied.
7. Vector tests: job upsert out of order, supersession during upsert, delete after purge, crash between DB commit and vector success, repeated reconcile, obsolete vectors never accepted into working context.
8. Purge tests: parent+descendant tree, memory with multiple sources, sensitive retained in audit payload/job/artifacts, in-flight jobs, disconnected Qdrant, restored backup re-applies tombstones; unrelated user's records unchanged.
9. Repeat all tests with runtime-role DB connections, not migration-owner credentials. Provide machine-readable test results, Flyway info/validate output, schema diff, and open defects; **no implementation milestone passes if any release-blocker fails**.

## Identified gaps in v0.1 SQL requiring implementation before passing gate

- No DB enforcement of branch prefix correctness/density or parent-path serialization.
- No deferred non-null current-revision assertion on canonical commit.
- No guarded transition/supersession DAG validation.
- No guarantee that aggregate-version increments correspond to event sequences or one idempotent canonical operation.
- No physical auth schema, session logic, privileged purge functions or durable deletion fence.
- `V004` RLS policies alone do not safely bootstrap login or grant controlled maintenance powers; provisioning/GRANTs and roles remain necessary.
- No real PostgreSQL or Flyway execution has taken place in this authoring environment; syntax and behavior must be verified before implementation approval.

## G. Approved deliverable requirement (v0.3)

Before PostgreSQL design sign-off, produce a **complete, consolidated Flyway migration baseline**, not only incremental illustrative SQL. It must implement the v0.2 contracts with explicit SQL for database-enforceable invariants, executable integration tests for database and service transaction semantics, and evidence from real PostgreSQL/Flyway execution. All release-blocking validation categories above must pass. This is a required **design handoff artifact**, not authorization to start application implementation.
