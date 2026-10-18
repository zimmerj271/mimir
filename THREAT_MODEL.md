# Mimir v1 threat model — security test planning

## Assets and boundaries
User identities, opaque bearer sessions, immutable conversation revisions, managed Mem0 memories, embedding indexes, operation journal, local keyring/file fallback, checkpoints, prompts, backups and diagnostic logs. Trust boundaries: Go CLI ↔ authenticated backend; API ↔ PostgreSQL RLS; orchestrator ↔ Ollama; orchestrator ↔ Mem0 ↔ Qdrant; job workers ↔ tenant-scoped persistence; backup restore ↔ purge fences. All history, memory, piped stdin and retrieved text are **untrusted data**, never system/developer instructions.

## Abuse cases and required mitigations/tests
- Cross-tenant IDs/cursors/vector points/worker claims → authenticated owner scope, canonical PostgreSQL validation, RLS tests under adversarial IDs.
- Prompt injection via retrieved memory/history/stdin → structural prompt delimiting, no instructions extracted from untrusted text, no hosted fallback, adversarial regression fixtures.
- Poisoned or stale memories → provenance, normalized revision identity, only committed eligible turns, correction/retrieval evaluation, Mem0 outcome reconciliation.
- Session token theft/replay, insecure file fallback → keyring-first, restrictive file permissions, atomic writes and tests, authenticated renewal, redaction of logs; documented threat of compromised endpoint.
- Run duplication, idempotency spoof/replay, cancellation races and SSE overflow → server-verified fingerprint and ownership, state machines, bounded streams, no automatic replay after acceptance, adversarial restart tests.
- Account deletion vs restore/resurrected data → durable deletion fence surviving restored archive, rekey/re-auth policy, restore/purge test.
- Resource exhaustion → bounded concurrency, queues, input/request/body limits, timeouts, safe errors; prohibit leaking private data in error or telemetry.

Maintain explicit threat → mitigating control → test ID → owner task mapping in `TRACEABILITY.md`; treat any changed trust boundary as requiring threat review. No document here asserts that a control is implemented.
