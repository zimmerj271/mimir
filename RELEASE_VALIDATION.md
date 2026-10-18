# Release validation and evidence gates

Design-text completeness is not equivalent to runtime readiness. All gates are blocking before implementation-freeze/release signoff:

1. `openapi.yaml` parses and all `$ref`s resolve; JSON Schema validates golden examples; backend generated FastAPI OpenAPI matches reviewed contract or diff is explicitly approved.
2. Flyway migration chain runs against ephemeral supported Postgres from empty DB and upgrade fixture, reruns validate cleanly; non-owner RLS tests, privilege tests and concurrent commit/branch immutability tests pass. Record exact command and environment.
3. Backend full integration/acceptance suite and CLI fake-server and real-backend tests pass; CI verifies source task/acceptance/test mapping. Monorepo cross-component compatibility test uses the single canonical contracts/ checksum.
4. Threat-model negative cases and memory evaluation regression suite pass under pinned models; performance and restart/durability baselines met; Linux/macOS terminals including PTY restore tested.
5. Manual coordinated backup + isolated restoration drill confirms integrity, deletion-fence replay, tombstone persistence, deletion reconciliation and auth revocation; no RPO/RTO service-level objective is claimed for v1. No secret leakage in logs, stdout or backup artifacts.
6. Each feature change was merged only after independent agent and human PR reviews with retained evidence. Release is a distinct human-authorized checkpoint; no inferred approval.

**Do not certify a gate based on a document or unexecuted command.** This package alone cannot produce runtime evidence without repositories and running dependencies.
