# Acceptance test specifications and release gates

**Status:** executable test requirements, NOT implemented tests or successful test results. Coding agents must turn these into automated suites and attach execution evidence.

## Real PostgreSQL / Flyway — M1/M2
- `DB-01` Fresh PostgreSQL 18 deploy: Flyway migration succeeds; second migrate is no-op; validate passes; runtime role cannot execute DDL or bypass RLS.
- `DB-02` Force RLS with different authenticated scopes on reused pooled connections, including missing scope; no cross-user read/write; owner-mismatch FKs reject.
- `DB-03` Commit completed message and exact immutable revision; update to existing revision rejected; current revision belongs to same message.
- `DB-04` Construct branch from nested branch; inherited prefix references exact historical revisions and matches visible parent prefix; parent subsequent revisions cannot change inherited result.
- `DB-05` Concurrent message submissions with same expected version: exactly one commit; other conflict. Concurrent branch/purge tests preserve FKs.
- `DB-06` One nonterminal run per conversation enforced. Idempotency key replay same payload returns same IDs; changed payload rejected.
- `DB-07` Archive conversation hides but preserves; delete subtree removes dependent archive artifacts, cancels referencing pending jobs and leaves Mem0 state untouched.

## Pure unit / API / SSE — M3
- `CTX-01` Identical assembler inputs produce identical output and diagnostics with no I/O or mutations. Mandatory system/project/current message never truncated.
- `CTX-02` User message exactly once; coherent newest turns selected; memories provider ordered; leftover budget redistributed; correct omissions/reasons and token estimates.
- `CTX-03` Mandatory overflow emits typed error; giant optional turn/memory omitted intact; protocol/tool pair not torn; injection-like memory text remains untrusted data.
- `INF-01` Fake provider streams ordered deltas and exactly one terminal state; final message revision and run state committed atomically; no per-token revisions.
- `INF-02` Cancellation before/during generation, completion/cancel race, interrupted producer, crash checkpoint recovery; no automatic regeneration after visible output.
- `SSE-01` Snapshot/live atomic handoff stress test: reconnecting client receives no lost/duplicated text; snapshot replaces displayed buffer.
- `SSE-02` Subscriber overflow bounded, disconnect recovers via snapshot; client disconnect does not cancel run; cross-user SSE denied.
- `API-01` Session expiry/revocation, CSRF, API version shapes, error codes, cursor ownership, idempotency, OpenAPI contract drift check.

## Memory adapter — M5
- `MEM-01` Real pinned self-hosted Mem0 OSS/Qdrant/Ollama local model (no hosted credentials): ingestion of clear preference becomes retrievable.
- `MEM-02` Different users' semantically identical preferences remain isolated on search, export/list internal and purge; explicit cross-user operation cannot mutate state.
- `MEM-03` Provider owns consolidation; multiple overlapping statements don't cause uncontrolled duplication; correction behavior measured rather than presumed.
- `MEM-04` Soft project affinity and global user retrieval behavior documented and tested; metadata limitations explicit.
- `MEM-05` Mem0 persistence survives restart; Qwen3-Embedding-4B 2560-dim FP32 cosine compatibility verified in pinned release.
- `MEM-06` Replayed/ambiguous ingestion doesn't silently claim exactly-once; reconciliation_required path exercised; per-user serialization enforced.
- `MEM-07` Ordinary conversation deletion does not delete managed memory. Full user deletion **does** purge user-owned memory, verifies absence and fences future ingestion.
- `MEM-08` Portable export enumerates all current provider-visible memories or fails explicitly; no silent partial-success.

## Runtime / ops / security — M6/M7
- `JOB-01` Competing `SKIP LOCKED` workers claim unique jobs; stale lease token cannot finalize; crash and retry recover correctly.
- `JOB-02` Completed turn + memory ingestion job atomic; failed transaction produces no job; Mem0 external call never performed inside open PostgreSQL transaction.
- `OPS-01` Fresh Compose install, repeated restart, migration compatibility, startup failure, Ollama/Qdrant degraded states; database required for readiness.
- `OPS-02` Encrypted backup + real restore: archive, branches, Mem0 persistent storage and sessions restored appropriately; backups of different store snapshots reconciled/documented.
- `SEC-01` Admin provisioning, Argon2id verification, hashed session tokens, secure browser cookie, CSRF rejection, no cross-user run or job access, hosted inference consent.
- `SEC-02` Full account purge durable across crash and restore older backup; deletion ledger/fence re-applied before restored service accessible; acknowledges backup retention limits.
- `OBS-01` Correlated IDs, time-to-first-token, queue depth, error classifications, no raw prompts/secrets in ordinary logs, optional prompt retention disabled by default.
- `E2E-01` Go CLI authenticates, creates/branches conversation, submits, streams, cancels, reconnects, sees durable history; other user isolated.
- `PERF-01` Record baseline expected-user concurrency, TTFT, inference queue delay, background memory interference, SSE backpressure; thresholds determined on selected hardware, not invented in design.

## Evidence deliverables
Each release-blocking test reports test command, pinned image/dependency versions, environment, pass/fail, duration, and relevant logs. Failure blocks corresponding milestone. This specification is not evidence of test execution.


## Additional Mimir server release-blocking scenarios
- `AUTH-RENEW-01`: Two concurrent CLI processes reuse the same opaque session while backend silently extends idle expiry; neither process loses its credential. Absolute cap, explicit logout, account disablement and revoked token deny access.
- `RUN-STAND-01`: Authenticated standalone run can retrieve user memory, finalizes durable result, and creates zero `archive.messages`, `archive.conversations`, or `runtime.memory_ingestions` rows; other users cannot inspect, cancel or attach to it.
- `RUN-STAND-02`: Standalone result/input checkpoints expire and are purged under configured retention, while live and recoverable runs are protected appropriately; full user purge erases retained standalone artifacts.
- `OP-LOOKUP-01`: Accepted one-shot and conversation-create operations are recoverable from owner-scoped idempotency identity with run/conversation IDs but without prompt body; unknown/expired identity is distinct; cross-user lookup denied.
- `OP-LOOKUP-02`: Retry with same key and identical fingerprint after lost acknowledgment returns same run ID and conversation ID; changed fingerprint is conflict. Replay handles stale expected version before CAS check.
- `PROJECT-MOVE-01`: Reassign and unassign conversation with expected version; reject unauthorized project/cross-user IDs, reject stale version; preserve immutable revisions, child branches and ingested memory.
- `SSE-STAND-01`: Standalone stream reconnect uses authoritative checkpoint-to-live handoff; both run kinds can cancel, complete, fail and interrupt deterministically without duplicate finalization.
- `CONTRACT-01`: OpenAPI 3.1 exported from actual FastAPI and normative SSE schema pass consumer compatibility tests against the independently maintained Mimir Go CLI mocks.
- `CLI-INTEGRATION-01`: Backend API and deterministic fake provider support one-shot run, explicit conversation, project assignment, session recovery, ambiguous POST and SSE disconnect without requiring Bubble Tea source in backend repository.
